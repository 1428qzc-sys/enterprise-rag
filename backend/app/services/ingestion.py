"""可恢复的版本化入库任务与数据/向量补偿。"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from typing import List

from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlmodel import Session, select

from ..core.embeddings import make_embeddings
from ..core.vector_store import VectorPoint, collection_for_kb, get_vector_store
from ..database import engine
from ..models import (
    Chunk,
    ConsistencyStatus,
    DocStatus,
    Document,
    DocumentVersion,
    IngestionJob,
    JobStatus,
    KnowledgeBase,
)
from . import bm25_index
from .chunking import ChunkData, chunk_sections
from .parsing import parse_file, parse_url

_INJECTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"system\s+prompt",
        r"developer\s+message",
        r"忽略.{0,8}(之前|以上|系统).{0,8}(指令|提示)",
        r"泄露.{0,8}(密钥|口令|提示词)",
    )
]


class JobCanceled(RuntimeError):
    pass


def content_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def has_prompt_injection(text: str) -> bool:
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def create_ingestion_job(
    session: Session,
    document: Document,
    version: DocumentVersion,
    *,
    kind: str,
    idempotency_key: str,
    max_attempts: int = 3,
) -> IngestionJob:
    existing = session.exec(
        select(IngestionJob).where(IngestionJob.idempotency_key == idempotency_key)
    ).first()
    if existing is not None:
        return existing
    job = IngestionJob(
        tenant_id=document.tenant_id,
        kb_id=document.kb_id,
        document_id=document.id,
        version_id=version.id,
        kind=kind,
        idempotency_key=idempotency_key,
        max_attempts=max(1, max_attempts),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _claim_job(job_id: str) -> bool:
    now = datetime.utcnow()
    with Session(engine) as session:
        result = session.execute(
            sa_update(IngestionJob)
            .where(
                IngestionJob.id == job_id,
                IngestionJob.status == JobStatus.PENDING,
                IngestionJob.attempt < IngestionJob.max_attempts,
            )
            .values(
                status=JobStatus.PROCESSING,
                stage="starting",
                progress=1,
                attempt=IngestionJob.attempt + 1,
                error="",
                started_at=now,
                finished_at=None,
                updated_at=now,
            )
        )
        session.commit()
        return bool(result.rowcount == 1)


def _set_progress(job_id: str, stage: str, progress: int, *, check_cancel: bool = True) -> None:
    now = datetime.utcnow()
    with Session(engine) as session:
        job = session.get(IngestionJob, job_id)
        if job is None:
            raise RuntimeError("入库任务不存在")
        if check_cancel and job.cancel_requested:
            raise JobCanceled("任务已取消")
        version = session.get(DocumentVersion, job.version_id)
        document = session.get(Document, job.document_id)
        value = max(0, min(100, progress))
        job.stage = stage
        job.progress = value
        job.updated_at = now
        session.add(job)
        if version is not None:
            version.status = DocStatus.PROCESSING
            version.progress = value
            version.updated_at = now
            session.add(version)
        if document is not None:
            document.status = DocStatus.PROCESSING
            document.progress = value
            document.cancel_requested = job.cancel_requested
            document.error = ""
            document.updated_at = now
            session.add(document)
        session.commit()


def _job_context(job_id: str) -> dict:
    with Session(engine) as session:
        job = session.get(IngestionJob, job_id)
        if job is None:
            raise RuntimeError("入库任务不存在")
        version = session.get(DocumentVersion, job.version_id)
        document = session.get(Document, job.document_id)
        kb = session.get(KnowledgeBase, job.kb_id)
        if version is None or document is None or kb is None:
            raise RuntimeError("入库任务关联的数据不存在")
        return {
            "job_id": job.id,
            "job_kind": job.kind,
            "version_id": version.id,
            "document_id": document.id,
            "kb_id": kb.id,
            "tenant_id": document.tenant_id,
            "source_type": version.source_type,
            "source": version.source,
            "stored_path": version.stored_path,
            "name": version.name,
            "content_hash": version.content_hash,
            "embedding_provider": version.embedding_provider or kb.embedding_provider,
            "embedding_model": version.embedding_model or kb.embedding_model,
            "embedding_dim": version.embedding_dim or kb.embedding_dim,
            "collection": collection_for_kb(kb),
            "active_version_id": document.active_version_id,
            "version_is_active": version.is_active,
        }


def _mark_duplicate(job_id: str, content_hash: str) -> None:
    now = datetime.utcnow()
    with Session(engine) as session:
        job = session.get(IngestionJob, job_id)
        if job is None:
            return
        document = session.get(Document, job.document_id)
        version = session.get(DocumentVersion, job.version_id)
        if document is None or version is None:
            return
        version.content_hash = content_hash
        version.status = DocStatus.CANCELED
        version.error = "内容与当前版本一致，未生成重复向量"
        version.progress = 100
        version.updated_at = now
        job.status = JobStatus.DONE
        job.stage = "duplicate"
        job.progress = 100
        job.error = ""
        job.finished_at = now
        job.updated_at = now
        document.status = DocStatus.DONE
        document.error = ""
        document.progress = 100
        document.cancel_requested = False
        document.updated_at = now
        session.add(version)
        session.add(job)
        session.add(document)
        session.commit()


def _mark_canceled(job_id: str) -> None:
    now = datetime.utcnow()
    with Session(engine) as session:
        job = session.get(IngestionJob, job_id)
        if job is None:
            return
        document = session.get(Document, job.document_id)
        version = session.get(DocumentVersion, job.version_id)
        session.execute(
            sa_delete(Chunk).where(Chunk.version_id == job.version_id, Chunk.is_active.is_(False))
        )
        if version is not None and not version.is_active:
            version.status = DocStatus.CANCELED
            version.error = "任务已由用户取消"
            version.updated_at = now
            session.add(version)
        job.status = JobStatus.CANCELED
        job.stage = "canceled"
        job.error = "任务已由用户取消"
        job.finished_at = now
        job.updated_at = now
        if document is not None:
            document.status = DocStatus.DONE if document.active_version_id else DocStatus.CANCELED
            document.error = "" if document.active_version_id else job.error
            document.progress = 100 if document.active_version_id else job.progress
            document.cancel_requested = False
            document.updated_at = now
            session.add(document)
        session.add(job)
        session.commit()


def _mark_failure(job_id: str, exc: Exception, *, activated: bool) -> None:
    now = datetime.utcnow()
    message = f"{type(exc).__name__}: {exc}"[:2000]
    with Session(engine) as session:
        job = session.get(IngestionJob, job_id)
        if job is None:
            return
        document = session.get(Document, job.document_id)
        version = session.get(DocumentVersion, job.version_id)
        if version is not None and not activated:
            version.status = DocStatus.FAILED
            version.error = message
            version.updated_at = now
            session.add(version)
        job.status = JobStatus.FAILED
        job.error = message
        job.finished_at = now
        job.updated_at = now
        session.add(job)
        if document is not None:
            document.status = DocStatus.FAILED
            document.error = message
            document.retry_count = job.attempt
            document.cancel_requested = False
            if activated:
                document.consistency_status = ConsistencyStatus.PENDING_CLEANUP
                document.cleanup_error = message
            document.updated_at = now
            session.add(document)
        session.commit()


def _mark_done(job_id: str, expected_count: int) -> None:
    now = datetime.utcnow()
    with Session(engine) as session:
        job = session.get(IngestionJob, job_id)
        if job is None:
            return
        document = session.get(Document, job.document_id)
        version = session.get(DocumentVersion, job.version_id)
        if document is None or version is None:
            return
        job.status = JobStatus.DONE
        job.stage = "done"
        job.progress = 100
        job.error = ""
        job.finished_at = now
        job.updated_at = now
        version.status = DocStatus.DONE
        version.progress = 100
        version.chunk_count = expected_count
        version.error = ""
        version.updated_at = now
        document.status = DocStatus.DONE
        document.progress = 100
        document.chunk_count = expected_count
        document.error = ""
        document.retry_count = job.attempt
        document.cancel_requested = False
        document.consistency_status = ConsistencyStatus.CONSISTENT
        document.cleanup_error = ""
        document.updated_at = now
        session.add(job)
        session.add(version)
        session.add(document)
        session.commit()


def _points_for_chunks(
    chunks: List[Chunk], vectors: List[List[float]], version_id: str
) -> List[VectorPoint]:
    return [
        VectorPoint(
            id=chunk.id,
            vector=vector,
            payload={
                "tenant_id": chunk.tenant_id,
                "kb_id": chunk.kb_id,
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "version_id": version_id,
                "chunk_index": chunk.chunk_index,
                "page": chunk.page,
            },
        )
        for chunk, vector in zip(chunks, vectors)
    ]


def _reconcile_active_version(job_id: str, context: dict) -> int:
    with Session(engine) as session:
        document = session.get(Document, context["document_id"])
        if document is None or not document.active_version_id:
            raise RuntimeError("文档没有可对账的有效版本")
        active_version = session.get(DocumentVersion, document.active_version_id)
        kb = session.get(KnowledgeBase, document.kb_id)
        if active_version is None or kb is None:
            raise RuntimeError("有效版本或知识库不存在")
        chunks = list(
            session.exec(
                select(Chunk)
                .where(
                    Chunk.document_id == document.id,
                    Chunk.version_id == active_version.id,
                    Chunk.is_active.is_(True),
                )
                .order_by(Chunk.chunk_index)
            ).all()
        )
        inactive_version_ids = list(
            session.exec(
                select(DocumentVersion.id).where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.id != active_version.id,
                )
            ).all()
        )
        collection = collection_for_kb(kb)
        provider = active_version.embedding_provider
        model = active_version.embedding_model
        dim = active_version.embedding_dim

    _set_progress(job_id, "reconciling", 94, check_cancel=False)
    embedder = make_embeddings(provider, model, dim)
    vectors = embedder.embed_documents([chunk.content for chunk in chunks])
    if len(vectors) != len(chunks) or any(len(vector) != dim for vector in vectors):
        raise ValueError("对账时 Embedding 数量或维度不匹配")
    store = get_vector_store()
    store.ensure_collection(collection, dim)
    store.upsert(collection, _points_for_chunks(chunks, vectors, active_version.id))
    for version_id in inactive_version_ids:
        store.delete_version(collection, version_id)
    actual = store.count_version(collection, active_version.id)
    total_for_document = store.count_document(collection, document.id)
    if actual != len(chunks) or total_for_document != len(chunks):
        store.delete_document(collection, document.id)
        store.upsert(collection, _points_for_chunks(chunks, vectors, active_version.id))
        actual = store.count_version(collection, active_version.id)
        total_for_document = store.count_document(collection, document.id)
    if actual != len(chunks) or total_for_document != len(chunks):
        raise RuntimeError(
            "向量数量不一致："
            f"expected={len(chunks)}, active={actual}, document={total_for_document}"
        )
    return len(chunks)


def process_ingestion_job(job_id: str) -> None:
    """处理一项持久入库任务；可被多个副本安全竞争领取。"""

    if not _claim_job(job_id):
        return
    activated = False
    context: dict = {}
    store = get_vector_store()
    try:
        context = _job_context(job_id)
        if context["version_is_active"] and context["active_version_id"] == context["version_id"]:
            count = _reconcile_active_version(job_id, context)
            _mark_done(job_id, count)
            bm25_index.invalidate(context["kb_id"])
            return

        _set_progress(job_id, "parsing", 10)
        if context["source_type"] == "url":
            sections, _title = parse_url(context["source"])
            normalized = "\n".join(section.text for section in sections).encode("utf-8")
            content_hash = content_sha256(normalized)
        else:
            sections = parse_file(context["stored_path"], context["name"])
            content_hash = context["content_hash"]
            if not content_hash:
                with open(context["stored_path"], "rb") as source_file:
                    content_hash = content_sha256(source_file.read())

        duplicate = False
        with Session(engine) as session:
            document = session.get(Document, context["document_id"])
            active = session.get(DocumentVersion, document.active_version_id) if document else None
            duplicate = (
                context["job_kind"] not in {"reembed", "reconcile"}
                and active is not None
                and active.content_hash == content_hash
            )
            version = session.get(DocumentVersion, context["version_id"])
            if version is not None and not duplicate:
                version.content_hash = content_hash
                session.add(version)
                session.commit()
        if duplicate:
            _mark_duplicate(job_id, content_hash)
            return

        _set_progress(job_id, "chunking", 30)
        chunk_data: List[ChunkData] = chunk_sections(sections)
        if not chunk_data:
            raise ValueError("未能从文档中解析出任何文本内容")

        _set_progress(job_id, "embedding", 45)
        embedder = make_embeddings(
            context["embedding_provider"],
            context["embedding_model"],
            context["embedding_dim"],
        )
        vectors = embedder.embed_documents([item.content for item in chunk_data])
        if len(vectors) != len(chunk_data):
            raise ValueError("Embedding 返回数量与分块数量不一致")
        if any(len(vector) != context["embedding_dim"] for vector in vectors):
            raise ValueError("Embedding 返回维度与知识库快照不一致")

        _set_progress(job_id, "staging", 65)
        store.ensure_collection(context["collection"], context["embedding_dim"])
        store.delete_version(context["collection"], context["version_id"])
        points: List[VectorPoint] = []
        with Session(engine) as session:
            session.execute(
                sa_delete(Chunk).where(
                    Chunk.version_id == context["version_id"], Chunk.is_active.is_(False)
                )
            )
            chunks: List[Chunk] = []
            for index, item in enumerate(chunk_data):
                meta = dict(item.meta)
                meta["document_name"] = context["name"]
                chunk = Chunk(
                    tenant_id=context["tenant_id"],
                    kb_id=context["kb_id"],
                    document_id=context["document_id"],
                    version_id=context["version_id"],
                    chunk_index=index,
                    content=item.content,
                    char_count=len(item.content),
                    page=item.page,
                    meta=meta,
                    is_active=False,
                    injection_risk=has_prompt_injection(item.content),
                )
                chunk.vector_id = chunk.id
                session.add(chunk)
                chunks.append(chunk)
            points = _points_for_chunks(chunks, vectors, context["version_id"])
            session.commit()

        _set_progress(job_id, "vector_upsert", 75)
        store.upsert(context["collection"], points)

        _set_progress(job_id, "activating", 86)
        now = datetime.utcnow()
        with Session(engine) as session:
            document = session.get(Document, context["document_id"])
            version = session.get(DocumentVersion, context["version_id"])
            job = session.get(IngestionJob, job_id)
            if document is None or version is None or job is None:
                raise RuntimeError("激活版本时关联数据不存在")
            old_version_ids = list(
                session.exec(
                    select(DocumentVersion.id).where(
                        DocumentVersion.document_id == document.id,
                        DocumentVersion.is_active.is_(True),
                        DocumentVersion.id != version.id,
                    )
                ).all()
            )
            session.execute(
                sa_update(Chunk)
                .where(Chunk.document_id == document.id, Chunk.is_active.is_(True))
                .values(is_active=False)
            )
            session.execute(
                sa_update(DocumentVersion)
                .where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.is_active.is_(True),
                )
                .values(is_active=False)
            )
            session.execute(
                sa_update(Chunk)
                .where(Chunk.version_id == version.id)
                .values(is_active=True)
            )
            version.is_active = True
            version.status = DocStatus.DONE
            version.progress = 100
            version.chunk_count = len(chunks)
            version.content_hash = content_hash
            version.error = ""
            version.updated_at = now
            document.active_version_id = version.id
            document.version = version.version_number
            document.content_hash = content_hash
            document.name = version.name
            document.source_type = version.source_type
            document.source = version.source
            document.mime = version.mime
            document.size_bytes = version.size_bytes
            document.stored_path = version.stored_path
            document.chunk_count = len(chunks)
            document.consistency_status = ConsistencyStatus.PENDING_CLEANUP
            document.status = DocStatus.PROCESSING
            document.progress = 90
            document.updated_at = now
            job.stage = "cleanup"
            job.progress = 90
            job.updated_at = now
            session.add(version)
            session.add(document)
            session.add(job)
            session.commit()
        activated = True

        for old_version_id in old_version_ids:
            store.delete_version(context["collection"], old_version_id)
        actual = store.count_version(context["collection"], context["version_id"])
        total_for_document = store.count_document(
            context["collection"], context["document_id"]
        )
        if actual != len(chunks) or total_for_document != len(chunks):
            store.delete_document(context["collection"], context["document_id"])
            store.upsert(context["collection"], points)
            actual = store.count_version(context["collection"], context["version_id"])
            total_for_document = store.count_document(
                context["collection"], context["document_id"]
            )
        if actual != len(chunks) or total_for_document != len(chunks):
            raise RuntimeError(
                "向量数量不一致："
                f"expected={len(chunks)}, active={actual}, document={total_for_document}"
            )
        _mark_done(job_id, len(chunks))
        bm25_index.invalidate(context["kb_id"])
    except JobCanceled:
        if context and not activated:
            try:
                store.delete_version(context["collection"], context["version_id"])
            except Exception:
                pass
        _mark_canceled(job_id)
    except Exception as exc:  # noqa: BLE001
        if context and not activated:
            try:
                store.delete_version(context["collection"], context["version_id"])
            except Exception:
                pass
            with Session(engine) as session:
                session.execute(
                    sa_delete(Chunk).where(
                        Chunk.version_id == context["version_id"],
                        Chunk.is_active.is_(False),
                    )
                )
                session.commit()
        _mark_failure(job_id, exc, activated=activated)
        if context:
            bm25_index.invalidate(context["kb_id"])


def request_job_cancel(session: Session, job: IngestionJob) -> IngestionJob:
    if job.status not in {JobStatus.PENDING, JobStatus.PROCESSING}:
        return job
    job.cancel_requested = True
    job.updated_at = datetime.utcnow()
    document = session.get(Document, job.document_id)
    if document is not None:
        document.cancel_requested = True
        document.updated_at = datetime.utcnow()
        session.add(document)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def reset_job_for_retry(session: Session, job: IngestionJob) -> IngestionJob:
    if job.status not in {JobStatus.FAILED, JobStatus.CANCELED}:
        raise ValueError("只有失败或已取消的任务可以重试")
    if job.attempt >= job.max_attempts:
        job.max_attempts = job.attempt + 1
    job.status = JobStatus.PENDING
    job.stage = "queued"
    job.progress = 0
    job.cancel_requested = False
    job.error = ""
    job.finished_at = None
    job.updated_at = datetime.utcnow()
    document = session.get(Document, job.document_id)
    if document is not None:
        document.status = DocStatus.PENDING
        document.progress = 0
        document.cancel_requested = False
        document.error = ""
        document.updated_at = datetime.utcnow()
        session.add(document)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def recover_incomplete_jobs() -> List[str]:
    """服务重启时把未完成任务恢复为 pending，并返回可重新调度的 id。"""

    now = datetime.utcnow()
    with Session(engine) as session:
        running = session.exec(
            select(IngestionJob).where(IngestionJob.status == JobStatus.PROCESSING)
        ).all()
        for job in running:
            if job.attempt >= job.max_attempts:
                job.status = JobStatus.FAILED
                job.error = "服务重启后已达到最大尝试次数"
                job.finished_at = now
            else:
                job.status = JobStatus.PENDING
                job.stage = "recovered"
                job.error = "服务重启后自动恢复"
            job.updated_at = now
            session.add(job)
        session.commit()
        return list(
            session.exec(
                select(IngestionJob.id).where(IngestionJob.status == JobStatus.PENDING)
            ).all()
        )


def process_document(document_id: str) -> None:
    """兼容旧调用：为尚无版本记录的文档建立 v1 后执行任务。"""

    with Session(engine) as session:
        document = session.get(Document, document_id)
        if document is None:
            return
        job = session.exec(
            select(IngestionJob)
            .where(IngestionJob.document_id == document_id)
            .order_by(IngestionJob.created_at.desc())
        ).first()
        if job is None:
            kb = session.get(KnowledgeBase, document.kb_id)
            if kb is None:
                return
            raw_hash = ""
            if document.stored_path and os.path.exists(document.stored_path):
                with open(document.stored_path, "rb") as source_file:
                    raw_hash = content_sha256(source_file.read())
            version = DocumentVersion(
                tenant_id=document.tenant_id,
                kb_id=document.kb_id,
                document_id=document.id,
                created_by_user_id=document.created_by_user_id,
                version_number=1,
                name=document.name,
                source_type=document.source_type,
                source=document.source,
                mime=document.mime,
                size_bytes=document.size_bytes,
                stored_path=document.stored_path,
                content_hash=raw_hash,
                embedding_provider=kb.embedding_provider,
                embedding_model=kb.embedding_model,
                embedding_dim=kb.embedding_dim,
            )
            document.latest_version = 1
            session.add(version)
            session.add(document)
            session.commit()
            session.refresh(version)
            job = create_ingestion_job(
                session,
                document,
                version,
                kind="ingest",
                idempotency_key=f"legacy:{document.id}:{version.id}",
            )
        job_id = job.id
    process_ingestion_job(job_id)
