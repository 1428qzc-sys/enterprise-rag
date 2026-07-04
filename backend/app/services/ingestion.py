"""文档入库流水线：解析 → 分块 → 嵌入 → 写向量库 → 落库。

设计为幂等：处理前先清理该文档的旧 chunk 与旧向量，可安全重试 / 重嵌入。
以后台任务方式运行（同步函数在 Starlette 线程池执行，不阻塞事件循环），
使用独立数据库会话。
"""

from __future__ import annotations

from datetime import datetime
from typing import List

from sqlmodel import Session, select

from ..core.embeddings import make_embeddings
from ..core.vector_store import VectorPoint, collection_name, get_vector_store
from ..database import engine
from ..models import Chunk, DocStatus, Document, KnowledgeBase
from . import bm25_index
from .chunking import chunk_sections
from .parsing import parse_file, parse_url


def _clear_document_data(session: Session, kb_id: str, document_id: str) -> None:
    """清理指定文档的旧 chunk（DB）与旧向量（向量库）。"""
    old = session.exec(select(Chunk).where(Chunk.document_id == document_id)).all()
    for c in old:
        session.delete(c)
    session.commit()
    vs = get_vector_store()
    vs.delete_document(collection_name(kb_id), document_id)


def process_document(document_id: str) -> None:
    """完整处理一篇文档。异常会写入 document.error 并置为 failed。"""
    with Session(engine) as session:
        doc = session.get(Document, document_id)
        if doc is None:
            return
        kb = session.get(KnowledgeBase, doc.kb_id)
        if kb is None:
            doc.status = DocStatus.FAILED
            doc.error = "所属知识库不存在"
            session.add(doc)
            session.commit()
            return

        doc.status = DocStatus.PROCESSING
        doc.error = ""
        doc.updated_at = datetime.utcnow()
        session.add(doc)
        session.commit()

        try:
            # 1) 解析
            if doc.source_type == "url":
                sections, _title = parse_url(doc.source)
            else:
                sections = parse_file(doc.stored_path, doc.name)

            # 2) 分块
            chunkdatas = chunk_sections(sections)
            if not chunkdatas:
                raise ValueError("未能从文档中解析出任何文本内容")

            # 3) 准备向量库 & 清理旧数据（幂等）
            vs = get_vector_store()
            col = collection_name(kb.id)
            vs.ensure_collection(col, kb.embedding_dim)
            _clear_document_data(session, kb.id, doc.id)

            # 4) 嵌入
            embedder = make_embeddings(kb.embedding_provider, kb.embedding_model, kb.embedding_dim)
            texts = [c.content for c in chunkdatas]
            vectors = embedder.embed_documents(texts)
            if len(vectors) != len(chunkdatas):
                raise ValueError("Embedding 返回数量与分块数量不一致")

            # 5) 组装 chunk 记录与向量点
            points: List[VectorPoint] = []
            for i, (cd, vec) in enumerate(zip(chunkdatas, vectors)):
                meta = dict(cd.meta)
                meta["document_name"] = doc.name
                chunk = Chunk(
                    kb_id=kb.id,
                    document_id=doc.id,
                    chunk_index=i,
                    content=cd.content,
                    char_count=len(cd.content),
                    page=cd.page,
                    meta=meta,
                )
                chunk.vector_id = chunk.id
                session.add(chunk)
                points.append(
                    VectorPoint(
                        id=chunk.id,
                        vector=vec,
                        payload={
                            "chunk_id": chunk.id,
                            "document_id": doc.id,
                            "chunk_index": i,
                        },
                    )
                )

            # 6) 先写向量库，再提交关系库
            vs.upsert(col, points)
            doc.status = DocStatus.DONE
            doc.chunk_count = len(chunkdatas)
            doc.updated_at = datetime.utcnow()
            session.add(doc)
            session.commit()

            bm25_index.invalidate(kb.id)
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            doc = session.get(Document, document_id)
            if doc is not None:
                doc.status = DocStatus.FAILED
                doc.error = str(exc)[:2000]
                doc.updated_at = datetime.utcnow()
                session.add(doc)
                session.commit()
            bm25_index.invalidate(document_id)
