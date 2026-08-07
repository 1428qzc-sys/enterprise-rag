"""知识库 Embedding 模型/维度的隔离重建与原子切换。"""

from __future__ import annotations

from datetime import datetime
from typing import List

from sqlalchemy import update as sa_update
from sqlmodel import Session, select

from ..core.embeddings import make_embeddings
from ..core.vector_store import VectorPoint, collection_for_kb, collection_name, get_vector_store
from ..database import engine
from ..models import (
    Chunk,
    ConsistencyStatus,
    DocumentVersion,
    JobStatus,
    KnowledgeBase,
    KnowledgeBaseReindexJob,
)


class ReindexCanceled(RuntimeError):
    pass


def create_reindex_job(
    session: Session,
    kb: KnowledgeBase,
    *,
    provider: str,
    model: str,
    dim: int,
    max_attempts: int,
) -> KnowledgeBaseReindexJob:
    running = session.exec(
        select(KnowledgeBaseReindexJob).where(
            KnowledgeBaseReindexJob.kb_id == kb.id,
            KnowledgeBaseReindexJob.status.in_([JobStatus.PENDING, JobStatus.PROCESSING]),
        )
    ).first()
    if running is not None:
        return running
    revision = max(1, kb.vector_revision) + 1
    job = KnowledgeBaseReindexJob(
        tenant_id=kb.tenant_id,
        kb_id=kb.id,
        target_provider=provider,
        target_model=model,
        target_dim=dim,
        target_revision=revision,
        target_collection=collection_name(kb.id, revision),
        previous_collection=collection_for_kb(kb),
        max_attempts=max(1, max_attempts),
    )
    kb.reindex_status = JobStatus.PENDING
    kb.reindex_progress = 0
    kb.reindex_error = ""
    kb.updated_at = datetime.utcnow()
    session.add(job)
    session.add(kb)
    session.commit()
    session.refresh(job)
    return job


def _claim(job_id: str) -> bool:
    now = datetime.utcnow()
    with Session(engine) as session:
        result = session.execute(
            sa_update(KnowledgeBaseReindexJob)
            .where(
                KnowledgeBaseReindexJob.id == job_id,
                KnowledgeBaseReindexJob.status == JobStatus.PENDING,
                KnowledgeBaseReindexJob.attempt < KnowledgeBaseReindexJob.max_attempts,
            )
            .values(
                status=JobStatus.PROCESSING,
                stage="starting",
                progress=1,
                attempt=KnowledgeBaseReindexJob.attempt + 1,
                error="",
                started_at=now,
                finished_at=None,
                updated_at=now,
            )
        )
        session.commit()
        return bool(result.rowcount == 1)


def _progress(job_id: str, stage: str, progress: int, *, check_cancel: bool = True) -> None:
    now = datetime.utcnow()
    with Session(engine) as session:
        job = session.get(KnowledgeBaseReindexJob, job_id)
        if job is None:
            raise RuntimeError("重建任务不存在")
        if check_cancel and job.cancel_requested:
            raise ReindexCanceled("重建任务已取消")
        kb = session.get(KnowledgeBase, job.kb_id)
        value = max(0, min(100, progress))
        job.stage = stage
        job.progress = value
        job.updated_at = now
        session.add(job)
        if kb is not None:
            kb.reindex_status = JobStatus.PROCESSING
            kb.reindex_progress = value
            kb.reindex_error = ""
            kb.updated_at = now
            session.add(kb)
        session.commit()


def _context(job_id: str) -> dict:
    with Session(engine) as session:
        job = session.get(KnowledgeBaseReindexJob, job_id)
        if job is None:
            raise RuntimeError("重建任务不存在")
        kb = session.get(KnowledgeBase, job.kb_id)
        if kb is None or kb.tenant_id != job.tenant_id:
            raise RuntimeError("重建任务所属知识库不存在")
        chunks = session.exec(
            select(Chunk)
            .where(
                Chunk.kb_id == kb.id,
                Chunk.tenant_id == kb.tenant_id,
                Chunk.is_active.is_(True),
            )
            .order_by(Chunk.document_id, Chunk.chunk_index)
        ).all()
        return {
            "job_id": job.id,
            "kb_id": kb.id,
            "tenant_id": kb.tenant_id,
            "target_provider": job.target_provider,
            "target_model": job.target_model,
            "target_dim": job.target_dim,
            "target_revision": job.target_revision,
            "target_collection": job.target_collection,
            "previous_collection": job.previous_collection,
            "already_activated": (
                kb.vector_collection == job.target_collection
                and kb.vector_revision == job.target_revision
            ),
            "chunks": [
                {
                    "id": chunk.id,
                    "document_id": chunk.document_id,
                    "version_id": chunk.version_id,
                    "chunk_index": chunk.chunk_index,
                    "page": chunk.page,
                    "content": chunk.content,
                }
                for chunk in chunks
            ],
        }


def _points(context: dict, vectors: List[List[float]]) -> List[VectorPoint]:
    return [
        VectorPoint(
            id=chunk["id"],
            vector=vector,
            payload={
                "tenant_id": context["tenant_id"],
                "kb_id": context["kb_id"],
                "chunk_id": chunk["id"],
                "document_id": chunk["document_id"],
                "version_id": chunk["version_id"],
                "chunk_index": chunk["chunk_index"],
                "page": chunk["page"],
            },
        )
        for chunk, vector in zip(context["chunks"], vectors)
    ]


def _mark_done(job_id: str) -> None:
    now = datetime.utcnow()
    with Session(engine) as session:
        job = session.get(KnowledgeBaseReindexJob, job_id)
        if job is None:
            return
        kb = session.get(KnowledgeBase, job.kb_id)
        job.status = JobStatus.DONE
        job.stage = "done"
        job.progress = 100
        job.error = ""
        job.cancel_requested = False
        job.finished_at = now
        job.updated_at = now
        session.add(job)
        if kb is not None:
            kb.reindex_status = JobStatus.DONE
            kb.reindex_progress = 100
            kb.reindex_error = ""
            kb.consistency_status = ConsistencyStatus.CONSISTENT
            kb.updated_at = now
            session.add(kb)
        session.commit()


def _mark_failure(job_id: str, exc: Exception, *, activated: bool) -> None:
    now = datetime.utcnow()
    message = f"{type(exc).__name__}: {exc}"[:2000]
    with Session(engine) as session:
        job = session.get(KnowledgeBaseReindexJob, job_id)
        if job is None:
            return
        kb = session.get(KnowledgeBase, job.kb_id)
        job.status = JobStatus.FAILED
        job.error = message
        job.finished_at = now
        job.updated_at = now
        session.add(job)
        if kb is not None:
            kb.reindex_status = JobStatus.FAILED
            kb.reindex_error = message
            kb.consistency_status = (
                ConsistencyStatus.PENDING_CLEANUP
                if activated
                else ConsistencyStatus.CONSISTENT
            )
            kb.updated_at = now
            session.add(kb)
        session.commit()


def _mark_canceled(job_id: str) -> None:
    now = datetime.utcnow()
    with Session(engine) as session:
        job = session.get(KnowledgeBaseReindexJob, job_id)
        if job is None:
            return
        kb = session.get(KnowledgeBase, job.kb_id)
        job.status = JobStatus.CANCELED
        job.stage = "canceled"
        job.error = "任务已由用户取消"
        job.finished_at = now
        job.updated_at = now
        session.add(job)
        if kb is not None:
            kb.reindex_status = JobStatus.CANCELED
            kb.reindex_error = job.error
            kb.reindex_progress = job.progress
            kb.updated_at = now
            session.add(kb)
        session.commit()


def process_reindex_job(job_id: str) -> None:
    if not _claim(job_id):
        return
    activated = False
    context: dict = {}
    store = get_vector_store()
    try:
        context = _context(job_id)
        if context["already_activated"]:
            _progress(job_id, "cleanup", 95, check_cancel=False)
            if context["previous_collection"] != context["target_collection"]:
                store.delete_collection(context["previous_collection"])
            if store.count(context["target_collection"]) != len(context["chunks"]):
                raise RuntimeError("目标 collection 向量数与有效 Chunk 数不一致")
            _mark_done(job_id)
            return

        _progress(job_id, "embedding", 20)
        embedder = make_embeddings(
            context["target_provider"], context["target_model"], context["target_dim"]
        )
        vectors = embedder.embed_documents([chunk["content"] for chunk in context["chunks"]])
        if len(vectors) != len(context["chunks"]):
            raise ValueError("Embedding 返回数量与有效 Chunk 数不一致")
        if any(len(vector) != context["target_dim"] for vector in vectors):
            raise ValueError("Embedding 返回维度与目标维度不一致")
        points = _points(context, vectors)

        _progress(job_id, "building_collection", 60)
        store.delete_collection(context["target_collection"])
        store.ensure_collection(context["target_collection"], context["target_dim"])
        store.upsert(context["target_collection"], points)
        if store.count(context["target_collection"]) != len(points):
            raise RuntimeError("目标 collection 构建后的向量数量不一致")

        _progress(job_id, "activating", 88)
        now = datetime.utcnow()
        with Session(engine) as session:
            job = session.get(KnowledgeBaseReindexJob, job_id)
            kb = session.get(KnowledgeBase, context["kb_id"])
            if job is None or kb is None or job.cancel_requested:
                raise ReindexCanceled("重建任务已取消")
            kb.embedding_provider = context["target_provider"]
            kb.embedding_model = context["target_model"]
            kb.embedding_dim = context["target_dim"]
            kb.vector_collection = context["target_collection"]
            kb.vector_revision = context["target_revision"]
            kb.reindex_status = JobStatus.PROCESSING
            kb.reindex_progress = 92
            kb.consistency_status = ConsistencyStatus.PENDING_CLEANUP
            kb.updated_at = now
            session.execute(
                sa_update(DocumentVersion)
                .where(
                    DocumentVersion.kb_id == kb.id,
                    DocumentVersion.is_active.is_(True),
                )
                .values(
                    embedding_provider=context["target_provider"],
                    embedding_model=context["target_model"],
                    embedding_dim=context["target_dim"],
                    updated_at=now,
                )
            )
            job.stage = "cleanup"
            job.progress = 92
            job.updated_at = now
            session.add(kb)
            session.add(job)
            session.commit()
        activated = True

        if context["previous_collection"] != context["target_collection"]:
            store.delete_collection(context["previous_collection"])
        if store.count(context["target_collection"]) != len(points):
            raise RuntimeError("切换后目标 collection 向量数量不一致")
        _mark_done(job_id)
    except ReindexCanceled:
        if context and not activated:
            try:
                store.delete_collection(context["target_collection"])
            except Exception:
                pass
        _mark_canceled(job_id)
    except Exception as exc:  # noqa: BLE001
        if context and not activated:
            try:
                store.delete_collection(context["target_collection"])
            except Exception:
                pass
        _mark_failure(job_id, exc, activated=activated)


def request_reindex_cancel(
    session: Session, job: KnowledgeBaseReindexJob
) -> KnowledgeBaseReindexJob:
    if job.status in {JobStatus.PENDING, JobStatus.PROCESSING}:
        job.cancel_requested = True
        job.updated_at = datetime.utcnow()
        session.add(job)
        session.commit()
        session.refresh(job)
    return job


def reset_reindex_job(
    session: Session, job: KnowledgeBaseReindexJob
) -> KnowledgeBaseReindexJob:
    if job.status not in {JobStatus.FAILED, JobStatus.CANCELED}:
        raise ValueError("只有失败或已取消的重建任务可以重试")
    if job.attempt >= job.max_attempts:
        job.max_attempts = job.attempt + 1
    job.status = JobStatus.PENDING
    job.stage = "queued"
    job.progress = 0
    job.cancel_requested = False
    job.error = ""
    job.finished_at = None
    job.updated_at = datetime.utcnow()
    kb = session.get(KnowledgeBase, job.kb_id)
    if kb is not None:
        kb.reindex_status = JobStatus.PENDING
        kb.reindex_progress = 0
        kb.reindex_error = ""
        kb.updated_at = datetime.utcnow()
        session.add(kb)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def recover_reindex_jobs() -> List[str]:
    now = datetime.utcnow()
    with Session(engine) as session:
        running = session.exec(
            select(KnowledgeBaseReindexJob).where(
                KnowledgeBaseReindexJob.status == JobStatus.PROCESSING
            )
        ).all()
        for job in running:
            job.status = (
                JobStatus.FAILED
                if job.attempt >= job.max_attempts
                else JobStatus.PENDING
            )
            job.stage = "recovered"
            job.error = "服务重启后自动恢复"
            job.updated_at = now
            session.add(job)
        session.commit()
        return list(
            session.exec(
                select(KnowledgeBaseReindexJob.id).where(
                    KnowledgeBaseReindexJob.status == JobStatus.PENDING
                )
            ).all()
        )
