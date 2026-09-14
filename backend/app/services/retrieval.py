"""可观测的混合检索：向量 + BM25 -> RRF -> 可选重排。"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from ..config import settings
from ..core.embeddings import make_embeddings
from ..core.reranker import get_reranker, reciprocal_rank_fusion
from ..core.vector_store import collection_for_kb, get_vector_store
from ..models import Chunk, Document, KnowledgeBase
from ..observability import observe_retrieval
from . import bm25_index


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_name: str
    chunk_index: int
    page: Optional[int]
    content: str
    score: float
    score_type: str = "rrf"
    vector_score: Optional[float] = None
    bm25_score: Optional[float] = None
    rrf_score: float = 0.0
    rerank_score: Optional[float] = None
    injection_risk: bool = False


@dataclass
class RetrievalResult:
    chunks: List[RetrievedChunk]
    diagnostics: Dict[str, Any] = field(default_factory=dict)


def _load_chunks(
    session: Session, ids: List[str], tenant_id: str, kb_id: str
) -> Dict[str, Chunk]:
    if not ids:
        return {}
    rows = session.exec(
        select(Chunk).where(
            Chunk.id.in_(ids),
            Chunk.tenant_id == tenant_id,
            Chunk.kb_id == kb_id,
            Chunk.is_active.is_(True),
        )
    ).all()
    return {chunk.id: chunk for chunk in rows}


def _load_doc_names(
    session: Session, doc_ids: List[str], tenant_id: str, kb_id: str
) -> Dict[str, str]:
    if not doc_ids:
        return {}
    rows = session.exec(
        select(Document.id, Document.name).where(
            Document.id.in_(doc_ids),
            Document.tenant_id == tenant_id,
            Document.kb_id == kb_id,
        )
    ).all()
    return {row[0]: row[1] for row in rows}


def retrieve_with_diagnostics(
    session: Session,
    kb: KnowledgeBase,
    query: str,
    top_k: Optional[int] = None,
) -> RetrievalResult:
    total_started = perf_counter()
    final_k = top_k or settings.hybrid_top_k
    degraded_reasons: List[str] = []
    print(f"[RETRIEVAL DEBUG] query={query}, top_k={final_k}")
    vector_started = perf_counter()
    vector_rank: List[str] = []
    vector_scores: Dict[str, float] = {}
    vector_error = ""
    try:
        embedder = make_embeddings(kb.embedding_provider, kb.embedding_model, kb.embedding_dim)
        query_vector = embedder.embed_query(query)
        hits = get_vector_store().search(
            collection_for_kb(kb), query_vector, settings.vector_top_k
        )
        for hit in hits:
            chunk_id = hit.payload.get("chunk_id")
            score = float(hit.score)
            if not chunk_id or score < settings.vector_min_score:
                continue
            if chunk_id not in vector_scores:
                vector_rank.append(chunk_id)
            vector_scores[chunk_id] = max(score, vector_scores.get(chunk_id, score))
        vector_status = "ok"
    except Exception as exc:  # noqa: BLE001
        vector_status = "degraded"
        vector_error = type(exc).__name__
        degraded_reasons.append(f"vector:{vector_error}")
    vector_diagnostics = {
        "status": vector_status,
        "elapsed_ms": _elapsed_ms(vector_started),
        "candidate_count": len(vector_rank),
        "min_score": settings.vector_min_score,
        "error": vector_error,
    }

    bm25_started = perf_counter()
    bm25_error = ""
    try:
        bm25_hits = bm25_index.search(session, kb.id, query, settings.bm25_top_k)
        bm25_status = "ok"
    except Exception as exc:  # noqa: BLE001
        bm25_hits = []
        bm25_status = "degraded"
        bm25_error = type(exc).__name__
        degraded_reasons.append(f"bm25:{bm25_error}")
    bm25_rank = [chunk_id for chunk_id, _ in bm25_hits]
    bm25_scores = {chunk_id: float(score) for chunk_id, score in bm25_hits}
    bm25_diagnostics = {
        "status": bm25_status,
        "elapsed_ms": _elapsed_ms(bm25_started),
        "candidate_count": len(bm25_rank),
        "error": bm25_error,
    }

    fusion_started = perf_counter()
    fused = reciprocal_rank_fusion([vector_rank, bm25_rank], settings.rrf_k)
    candidate_ids = sorted(fused, key=lambda chunk_id: fused[chunk_id], reverse=True)
    pool_size = max(final_k * 3, settings.rerank_top_n * 2, 20)
    pool_ids = candidate_ids[:pool_size]
    chunks = _load_chunks(session, pool_ids, kb.tenant_id, kb.id)
    pool_ids = [chunk_id for chunk_id in pool_ids if chunk_id in chunks]
    fusion_diagnostics = {
        "status": "ok",
        "method": "rrf",
        "rrf_k": settings.rrf_k,
        "elapsed_ms": _elapsed_ms(fusion_started),
        "candidate_count": len(pool_ids),
        "filtered_count": len(candidate_ids) - len(pool_ids),
    }

    rerank_started = perf_counter()
    rerank_scores: Dict[str, float] = {}
    reranker = get_reranker()
    if reranker is None:
        final_ids = pool_ids[:final_k]
        rerank_diagnostics = {
            "status": "skipped",
            "provider": "none",
            "elapsed_ms": _elapsed_ms(rerank_started),
            "candidate_count": len(pool_ids),
            "error": "",
        }
    else:
        documents = [(chunk_id, chunks[chunk_id].content) for chunk_id in pool_ids]
        outcome = reranker.rerank_with_scores(
            query, documents, top_n=max(final_k, settings.rerank_top_n)
        )
        final_ids = outcome.ids[:final_k]
        rerank_scores = outcome.scores
        status = "applied" if outcome.applied else "degraded"
        if outcome.degraded_reason:
            degraded_reasons.append(f"rerank:{outcome.degraded_reason}")
        rerank_diagnostics = {
            "status": status,
            "provider": outcome.provider,
            "elapsed_ms": _elapsed_ms(rerank_started),
            "candidate_count": len(pool_ids),
            "error": outcome.degraded_reason,
        }

    doc_names = _load_doc_names(
        session,
        list({chunks[chunk_id].document_id for chunk_id in final_ids}),
        kb.tenant_id,
        kb.id,
    )
    results: List[RetrievedChunk] = []
    rerank_applied = rerank_diagnostics["status"] == "applied"
    for chunk_id in final_ids:
        chunk = chunks[chunk_id]
        rerank_score = rerank_scores.get(chunk_id) if rerank_applied else None
        score = rerank_score if rerank_score is not None else fused.get(chunk_id, 0.0)
        results.append(
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                document_name=doc_names.get(
                    chunk.document_id, chunk.meta.get("document_name", "")
                ),
                chunk_index=chunk.chunk_index,
                page=chunk.page,
                content=chunk.content,
                score=round(float(score), 6),
                score_type="rerank" if rerank_score is not None else "rrf",
                vector_score=(
                    round(vector_scores[chunk_id], 6) if chunk_id in vector_scores else None
                ),
                bm25_score=(
                    round(bm25_scores[chunk_id], 6) if chunk_id in bm25_scores else None
                ),
                rrf_score=round(float(fused.get(chunk_id, 0.0)), 6),
                rerank_score=(round(rerank_score, 6) if rerank_score is not None else None),
                injection_risk=chunk.injection_risk,
            )
        )

    diagnostics = {
        "total_ms": _elapsed_ms(total_started),
        "result_count": len(results),
        "degraded": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
        "vector": vector_diagnostics,
        "bm25": bm25_diagnostics,
        "fusion": fusion_diagnostics,
        "rerank": rerank_diagnostics,
    }
    observe_retrieval(diagnostics)
    print(f"[RETRIEVAL DEBUG] returned {len(results)} chunks")
    return RetrievalResult(chunks=results, diagnostics=diagnostics)


def retrieve(
    session: Session,
    kb: KnowledgeBase,
    query: str,
    top_k: Optional[int] = None,
) -> List[RetrievedChunk]:
    """兼容原调用方，只返回排序后的 Chunk。"""
    return retrieve_with_diagnostics(session, kb, query, top_k).chunks
