"""混合检索：向量召回 + BM25 召回 → RRF 融合 →（可选）交叉编码器重排。

流程：
1. 向量：用知识库自己的 Embedding 把 query 编码后在向量库 top-k 召回；
2. BM25：在稀疏索引上 top-k 召回；
3. RRF：把两路排名做倒数秩融合，得到统一候选序；
4. 重排：若启用交叉编码器，对候选精排；否则直接取融合序 top-n。
返回带溯源信息的 ``RetrievedChunk`` 列表。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from sqlmodel import Session, select

from ..config import settings
from ..core.embeddings import make_embeddings
from ..core.reranker import get_reranker, reciprocal_rank_fusion
from ..core.vector_store import collection_name, get_vector_store
from ..models import Chunk, Document, KnowledgeBase
from . import bm25_index


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_name: str
    chunk_index: int
    page: Optional[int]
    content: str
    score: float


def _load_chunks(session: Session, ids: List[str]) -> Dict[str, Chunk]:
    if not ids:
        return {}
    rows = session.exec(select(Chunk).where(Chunk.id.in_(ids))).all()
    return {c.id: c for c in rows}


def _load_doc_names(session: Session, doc_ids: List[str]) -> Dict[str, str]:
    if not doc_ids:
        return {}
    rows = session.exec(select(Document.id, Document.name).where(Document.id.in_(doc_ids))).all()
    return {r[0]: r[1] for r in rows}


def retrieve(
    session: Session,
    kb: KnowledgeBase,
    query: str,
    top_k: Optional[int] = None,
) -> List[RetrievedChunk]:
    final_k = top_k or settings.hybrid_top_k

    # 1) 向量召回
    vector_rank: List[str] = []
    try:
        embedder = make_embeddings(kb.embedding_provider, kb.embedding_model, kb.embedding_dim)
        qvec = embedder.embed_query(query)
        vs = get_vector_store()
        hits = vs.search(collection_name(kb.id), qvec, settings.vector_top_k)
        vector_rank = [h.payload.get("chunk_id") for h in hits if h.payload.get("chunk_id")]
    except Exception:
        # 向量库不可用/未配置 embedding 时，降级为纯 BM25
        vector_rank = []

    # 2) BM25 召回
    bm = bm25_index.search(session, kb.id, query, settings.bm25_top_k)
    bm25_rank = [cid for cid, _ in bm]

    if not vector_rank and not bm25_rank:
        return []

    # 3) RRF 融合
    fused = reciprocal_rank_fusion([vector_rank, bm25_rank], settings.rrf_k)
    candidate_ids = sorted(fused, key=lambda c: fused[c], reverse=True)

    # 只对融合靠前的候选做后续处理（含可选精排）
    pool_size = max(final_k * 3, settings.rerank_top_n * 2, 20)
    pool_ids = candidate_ids[:pool_size]
    chunks = _load_chunks(session, pool_ids)
    pool_ids = [cid for cid in pool_ids if cid in chunks]  # 过滤已删除

    # 4) 可选交叉编码器重排
    reranker = get_reranker()
    if reranker is not None:
        docs = [(cid, chunks[cid].content) for cid in pool_ids]
        ordered = reranker.rerank(query, docs, top_n=max(final_k, settings.rerank_top_n))
        final_ids = ordered[:final_k]
    else:
        final_ids = pool_ids[:final_k]

    doc_names = _load_doc_names(session, list({chunks[cid].document_id for cid in final_ids}))

    results: List[RetrievedChunk] = []
    for cid in final_ids:
        c = chunks[cid]
        results.append(
            RetrievedChunk(
                chunk_id=c.id,
                document_id=c.document_id,
                document_name=doc_names.get(c.document_id, c.meta.get("document_name", "")),
                chunk_index=c.chunk_index,
                page=c.page,
                content=c.content,
                score=round(float(fused.get(cid, 0.0)), 6),
            )
        )
    return results
