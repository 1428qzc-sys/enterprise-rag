"""知识库管理：创建 / 列表 / 详情 / 更新 / 删除（含级联清理向量与文档）。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import delete as sa_delete
from sqlalchemy import func
from sqlmodel import Session, select

from ..audit import record_audit
from ..config import settings
from ..core.embeddings import make_embeddings
from ..core.vector_store import VectorPoint, collection_for_kb, get_vector_store
from ..database import get_session
from ..models import (
    Chunk,
    Conversation,
    Document,
    DocumentVersion,
    IngestionJob,
    JobStatus,
    KnowledgeBase,
    KnowledgeBaseReindexJob,
    Message,
)
from ..schemas import KBCreate, KBRead, KBReindexJobRead, KBReindexRequest, KBUpdate
from ..security import Principal
from ..services import bm25_index
from ..services.reindexing import (
    create_reindex_job,
    process_reindex_job,
    request_reindex_cancel,
    reset_reindex_job,
)
from .deps import can_access_kb, require_permission

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


def _to_read(session: Session, kb: KnowledgeBase) -> KBRead:
    doc_count = session.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.kb_id == kb.id, Document.tenant_id == kb.tenant_id)
    ) or 0
    chunk_count = session.scalar(
        select(func.count())
        .select_from(Chunk)
        .where(
            Chunk.kb_id == kb.id,
            Chunk.tenant_id == kb.tenant_id,
            Chunk.is_active.is_(True),
        )
    ) or 0
    return KBRead(
        id=kb.id,
        tenant_id=kb.tenant_id,
        name=kb.name,
        description=kb.description,
        embedding_provider=kb.embedding_provider,
        embedding_model=kb.embedding_model,
        embedding_dim=kb.embedding_dim,
        vector_backend=kb.vector_backend,
        vector_collection=kb.vector_collection,
        vector_revision=kb.vector_revision,
        reindex_status=kb.reindex_status,
        reindex_progress=kb.reindex_progress,
        reindex_error=kb.reindex_error,
        consistency_status=kb.consistency_status,
        document_count=int(doc_count),
        chunk_count=int(chunk_count),
        created_at=kb.created_at,
        updated_at=kb.updated_at,
    )


@router.post("", response_model=KBRead, status_code=201)
def create_kb(
    body: KBCreate,
    request: Request,
    principal: Principal = Depends(require_permission("kb:write")),
    session: Session = Depends(get_session),
) -> KBRead:
    kb = KnowledgeBase(
        tenant_id=principal.tenant_id,
        created_by_user_id=principal.user_id,
        name=body.name,
        description=body.description,
        embedding_provider=settings.embedding_provider,
        embedding_model=settings.embedding_model,
        embedding_dim=settings.embedding_dim,
        vector_backend=settings.vector_backend,
    )
    kb.vector_collection = collection_for_kb(kb)
    session.add(kb)
    session.commit()
    session.refresh(kb)
    record_audit(
        "kb.create",
        "success",
        request=request,
        principal=principal,
        resource_type="knowledge_base",
        resource_id=kb.id,
        detail={"name": kb.name},
    )
    return _to_read(session, kb)


@router.get("", response_model=List[KBRead])
def list_kbs(
    principal: Principal = Depends(require_permission("kb:read")),
    session: Session = Depends(get_session),
) -> List[KBRead]:
    doc_counts = (
        select(Document.kb_id, func.count(Document.id).label("document_count"))
        .group_by(Document.kb_id)
        .subquery()
    )
    chunk_counts = (
        select(Chunk.kb_id, func.count(Chunk.id).label("chunk_count"))
        .where(Chunk.is_active.is_(True))
        .group_by(Chunk.kb_id)
        .subquery()
    )
    query = (
        select(
            KnowledgeBase,
            func.coalesce(doc_counts.c.document_count, 0),
            func.coalesce(chunk_counts.c.chunk_count, 0),
        )
        .outerjoin(doc_counts, doc_counts.c.kb_id == KnowledgeBase.id)
        .outerjoin(chunk_counts, chunk_counts.c.kb_id == KnowledgeBase.id)
        .order_by(KnowledgeBase.created_at.desc())
    )
    query = query.where(KnowledgeBase.tenant_id == principal.tenant_id)
    rows = session.exec(query).all()
    return [
        KBRead(
            id=kb.id,
            tenant_id=kb.tenant_id,
            name=kb.name,
            description=kb.description,
            embedding_provider=kb.embedding_provider,
            embedding_model=kb.embedding_model,
            embedding_dim=kb.embedding_dim,
            vector_backend=kb.vector_backend,
            vector_collection=kb.vector_collection,
            vector_revision=kb.vector_revision,
            reindex_status=kb.reindex_status,
            reindex_progress=kb.reindex_progress,
            reindex_error=kb.reindex_error,
            consistency_status=kb.consistency_status,
            document_count=int(document_count),
            chunk_count=int(chunk_count),
            created_at=kb.created_at,
            updated_at=kb.updated_at,
        )
        for kb, document_count, chunk_count in rows
    ]


@router.get("/{kb_id}", response_model=KBRead)
def get_kb(
    kb_id: str,
    principal: Principal = Depends(require_permission("kb:read")),
    session: Session = Depends(get_session),
) -> KBRead:
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None or not can_access_kb(principal, kb):
        raise HTTPException(status_code=404, detail="知识库不存在")
    return _to_read(session, kb)


def _get_reindex_job(
    session: Session,
    kb: KnowledgeBase,
    job_id: str,
) -> KnowledgeBaseReindexJob:
    job = session.exec(
        select(KnowledgeBaseReindexJob).where(
            KnowledgeBaseReindexJob.id == job_id,
            KnowledgeBaseReindexJob.kb_id == kb.id,
            KnowledgeBaseReindexJob.tenant_id == kb.tenant_id,
        )
    ).first()
    if job is None:
        raise HTTPException(status_code=404, detail="知识库重建任务不存在")
    return job


@router.post("/{kb_id}/reindex", response_model=KBReindexJobRead, status_code=202)
def reindex_kb(
    kb_id: str,
    body: KBReindexRequest,
    background: BackgroundTasks,
    request: Request,
    principal: Principal = Depends(require_permission("kb:write")),
    session: Session = Depends(get_session),
) -> KBReindexJobRead:
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None or not can_access_kb(principal, kb):
        raise HTTPException(status_code=404, detail="知识库不存在")
    job = create_reindex_job(
        session,
        kb,
        provider=body.embedding_provider,
        model=body.embedding_model,
        dim=body.embedding_dim,
        max_attempts=settings.ingestion_max_attempts,
    )
    background.add_task(process_reindex_job, job.id)
    record_audit(
        "kb.reindex",
        "accepted",
        request=request,
        principal=principal,
        resource_type="knowledge_base",
        resource_id=kb.id,
        detail={
            "job_id": job.id,
            "provider": body.embedding_provider,
            "model": body.embedding_model,
            "dim": body.embedding_dim,
        },
    )
    return KBReindexJobRead.model_validate(job)


@router.get("/{kb_id}/reindex-jobs", response_model=List[KBReindexJobRead])
def list_reindex_jobs(
    kb_id: str,
    principal: Principal = Depends(require_permission("kb:read")),
    session: Session = Depends(get_session),
) -> List[KBReindexJobRead]:
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None or not can_access_kb(principal, kb):
        raise HTTPException(status_code=404, detail="知识库不存在")
    jobs = session.exec(
        select(KnowledgeBaseReindexJob)
        .where(
            KnowledgeBaseReindexJob.kb_id == kb.id,
            KnowledgeBaseReindexJob.tenant_id == principal.tenant_id,
        )
        .order_by(KnowledgeBaseReindexJob.created_at.desc())
    ).all()
    return [KBReindexJobRead.model_validate(job) for job in jobs]


@router.post(
    "/{kb_id}/reindex-jobs/{job_id}/cancel",
    response_model=KBReindexJobRead,
)
def cancel_reindex_job(
    kb_id: str,
    job_id: str,
    request: Request,
    principal: Principal = Depends(require_permission("kb:write")),
    session: Session = Depends(get_session),
) -> KBReindexJobRead:
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None or not can_access_kb(principal, kb):
        raise HTTPException(status_code=404, detail="知识库不存在")
    job = request_reindex_cancel(session, _get_reindex_job(session, kb, job_id))
    record_audit(
        "kb.reindex_cancel",
        "accepted",
        request=request,
        principal=principal,
        resource_type="knowledge_base_reindex_job",
        resource_id=job.id,
        detail={"kb_id": kb.id},
    )
    return KBReindexJobRead.model_validate(job)


@router.post(
    "/{kb_id}/reindex-jobs/{job_id}/retry",
    response_model=KBReindexJobRead,
)
def retry_reindex_job(
    kb_id: str,
    job_id: str,
    background: BackgroundTasks,
    request: Request,
    principal: Principal = Depends(require_permission("kb:write")),
    session: Session = Depends(get_session),
) -> KBReindexJobRead:
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None or not can_access_kb(principal, kb):
        raise HTTPException(status_code=404, detail="知识库不存在")
    try:
        job = reset_reindex_job(session, _get_reindex_job(session, kb, job_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background.add_task(process_reindex_job, job.id)
    record_audit(
        "kb.reindex_retry",
        "accepted",
        request=request,
        principal=principal,
        resource_type="knowledge_base_reindex_job",
        resource_id=job.id,
        detail={"kb_id": kb.id, "attempt": job.attempt},
    )
    return KBReindexJobRead.model_validate(job)


@router.patch("/{kb_id}", response_model=KBRead)
def update_kb(
    kb_id: str,
    body: KBUpdate,
    request: Request,
    principal: Principal = Depends(require_permission("kb:write")),
    session: Session = Depends(get_session),
) -> KBRead:
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None or not can_access_kb(principal, kb):
        raise HTTPException(status_code=404, detail="知识库不存在")
    if body.name is not None:
        kb.name = body.name
    if body.description is not None:
        kb.description = body.description
    kb.updated_at = datetime.utcnow()
    session.add(kb)
    session.commit()
    session.refresh(kb)
    record_audit(
        "kb.update",
        "success",
        request=request,
        principal=principal,
        resource_type="knowledge_base",
        resource_id=kb.id,
        detail={"name": kb.name},
    )
    return _to_read(session, kb)


@router.delete("/{kb_id}", status_code=204)
def delete_kb(
    kb_id: str,
    request: Request,
    principal: Principal = Depends(require_permission("kb:delete")),
    session: Session = Depends(get_session),
) -> None:
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None or not can_access_kb(principal, kb):
        raise HTTPException(status_code=404, detail="知识库不存在")

    running_doc_job = session.exec(
        select(IngestionJob.id).where(
            IngestionJob.kb_id == kb.id,
            IngestionJob.status.in_([JobStatus.PENDING, JobStatus.PROCESSING]),
        )
    ).first()
    running_reindex = session.exec(
        select(KnowledgeBaseReindexJob.id).where(
            KnowledgeBaseReindexJob.kb_id == kb.id,
            KnowledgeBaseReindexJob.status.in_([JobStatus.PENDING, JobStatus.PROCESSING]),
        )
    ).first()
    if running_doc_job is not None or running_reindex is not None:
        raise HTTPException(status_code=409, detail="知识库仍有进行中的任务，请先取消或等待完成")

    active_chunks = list(
        session.exec(
            select(Chunk)
            .where(
                Chunk.kb_id == kb.id,
                Chunk.tenant_id == principal.tenant_id,
                Chunk.is_active.is_(True),
            )
            .order_by(Chunk.document_id, Chunk.chunk_index)
        ).all()
    )
    restore_points: List[VectorPoint] = []
    if active_chunks:
        try:
            embedder = make_embeddings(kb.embedding_provider, kb.embedding_model, kb.embedding_dim)
            vectors = embedder.embed_documents([chunk.content for chunk in active_chunks])
            if len(vectors) != len(active_chunks):
                raise ValueError("Embedding 返回数量不匹配")
            restore_points = [
                VectorPoint(
                    id=chunk.id,
                    vector=vector,
                    payload={
                        "tenant_id": chunk.tenant_id,
                        "kb_id": chunk.kb_id,
                        "chunk_id": chunk.id,
                        "document_id": chunk.document_id,
                        "version_id": chunk.version_id,
                        "chunk_index": chunk.chunk_index,
                        "page": chunk.page,
                    },
                )
                for chunk, vector in zip(active_chunks, vectors)
            ]
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail=f"无法准备删除补偿，知识库未删除：{type(exc).__name__}",
            ) from exc

    collection = collection_for_kb(kb)
    vector_store = get_vector_store()
    try:
        vector_store.delete_collection(collection)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"向量 collection 删除失败，元数据未删除：{type(exc).__name__}",
        ) from exc

    stored_paths = list(
        session.exec(
            select(DocumentVersion.stored_path).where(DocumentVersion.kb_id == kb.id)
        ).all()
    )
    convs = session.exec(
        select(Conversation).where(
            Conversation.kb_id == kb_id,
            Conversation.tenant_id == principal.tenant_id,
        )
    ).all()
    conv_ids = [c.id for c in convs]
    try:
        if conv_ids:
            session.execute(sa_delete(Message).where(Message.conversation_id.in_(conv_ids)))
        session.execute(sa_delete(Conversation).where(Conversation.kb_id == kb_id))
        session.execute(sa_delete(Chunk).where(Chunk.kb_id == kb_id))
        session.execute(sa_delete(IngestionJob).where(IngestionJob.kb_id == kb_id))
        session.execute(
            sa_delete(DocumentVersion).where(DocumentVersion.kb_id == kb_id)
        )
        session.execute(sa_delete(Document).where(Document.kb_id == kb_id))
        session.execute(
            sa_delete(KnowledgeBaseReindexJob).where(
                KnowledgeBaseReindexJob.kb_id == kb_id
            )
        )
        session.delete(kb)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        vector_store.ensure_collection(collection, kb.embedding_dim)
        if restore_points:
            vector_store.upsert(collection, restore_points)
        raise HTTPException(status_code=500, detail="元数据删除失败，向量 collection 已恢复") from exc

    cleanup_failures = 0
    for stored_path in set(path for path in stored_paths if path):
        try:
            Path(stored_path).unlink(missing_ok=True)
        except OSError:
            cleanup_failures += 1
    bm25_index.invalidate(kb_id)
    record_audit(
        "kb.delete",
        "success",
        request=request,
        principal=principal,
        resource_type="knowledge_base",
        resource_id=kb_id,
        detail={"file_cleanup_failures": cleanup_failures},
    )
