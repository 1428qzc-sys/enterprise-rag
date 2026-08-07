"""文档、版本、持久入库任务、原文分块与一致性管理。"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from sqlalchemy import delete as sa_delete
from sqlmodel import Session, select

from ..audit import record_audit
from ..config import settings
from ..core.embeddings import make_embeddings
from ..core.vector_store import VectorPoint, collection_for_kb, get_vector_store
from ..database import get_session
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
from ..schemas import (
    ChunkRead,
    DocumentRead,
    DocumentVersionRead,
    IngestionJobRead,
    IngestUrlRequest,
)
from ..security import Principal
from ..security_network import UnsafeUrlError, validate_public_http_url
from ..services import bm25_index
from ..services.ingestion import (
    content_sha256,
    create_ingestion_job,
    process_ingestion_job,
    request_job_cancel,
    reset_job_for_retry,
)
from ..services.parsing import SUPPORTED_EXTS, validate_file_content
from .deps import can_access_kb, require_permission

router = APIRouter(prefix="/knowledge-bases/{kb_id}/documents", tags=["documents"])


def _require_kb(kb_id: str, session: Session, principal: Principal) -> KnowledgeBase:
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None or not can_access_kb(principal, kb):
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb


def _require_document(
    kb_id: str,
    document_id: str,
    session: Session,
    principal: Principal,
) -> Document:
    _require_kb(kb_id, session, principal)
    document = session.exec(
        select(Document).where(
            Document.id == document_id,
            Document.kb_id == kb_id,
            Document.tenant_id == principal.tenant_id,
        )
    ).first()
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return document


def _running_job(session: Session, document_id: str) -> IngestionJob | None:
    return session.exec(
        select(IngestionJob)
        .where(
            IngestionJob.document_id == document_id,
            IngestionJob.status.in_([JobStatus.PENDING, JobStatus.PROCESSING]),
        )
        .order_by(IngestionJob.created_at.desc())
    ).first()


def _ensure_not_processing(session: Session, document: Document) -> None:
    job = _running_job(session, document.id)
    if job is not None:
        raise HTTPException(
            status_code=409,
            detail=f"文档仍有进行中的任务（{job.stage}），请先取消或等待完成",
        )


def _job_key(raw: str | None, fallback: str, tenant_id: str) -> str:
    value = (raw or "").strip()
    if value:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return f"client:{tenant_id}:{digest}"
    return fallback


async def _stream_upload(file: UploadFile, ext: str) -> tuple[str, int, str]:
    staging_dir = Path(settings.upload_dir, ".staging")
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_path = staging_dir / f"{uuid4().hex}{ext}"
    maximum = settings.max_upload_mb * 1024 * 1024
    total = 0
    digest = hashlib.sha256()
    try:
        with staging_path.open("wb") as output:
            while True:
                block = await file.read(settings.upload_read_chunk_bytes)
                if not block:
                    break
                total += len(block)
                if total > maximum:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过 {settings.max_upload_mb}MB 上限",
                    )
                digest.update(block)
                output.write(block)
        if total == 0:
            raise HTTPException(status_code=400, detail="文件为空")
        return str(staging_path), total, digest.hexdigest()
    except Exception:
        staging_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


def _finalize_file(staging_path: str, document: Document, version_number: int, ext: str) -> str:
    target_dir = Path(settings.upload_dir, document.tenant_id, document.kb_id, document.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"v{version_number}{ext}"
    os.replace(staging_path, target)
    return str(target)


def _create_version_job(
    session: Session,
    document: Document,
    version: DocumentVersion,
    *,
    kind: str,
    idempotency_key: str,
) -> IngestionJob:
    document.latest_version = max(document.latest_version, version.version_number)
    document.status = DocStatus.PENDING
    document.progress = 0
    document.error = ""
    document.cancel_requested = False
    document.consistency_status = (
        document.consistency_status
        if document.active_version_id
        else ConsistencyStatus.PENDING
    )
    document.updated_at = datetime.utcnow()
    session.add(version)
    session.add(document)
    session.commit()
    session.refresh(version)
    return create_ingestion_job(
        session,
        document,
        version,
        kind=kind,
        idempotency_key=idempotency_key,
        max_attempts=settings.ingestion_max_attempts,
    )


async def _file_version(
    *,
    kb_id: str,
    document_id: str | None,
    file: UploadFile,
    background: BackgroundTasks,
    request: Request,
    response: Response,
    principal: Principal,
    session: Session,
    idempotency_key: str | None,
) -> DocumentRead:
    kb = _require_kb(kb_id, session, principal)
    filename = (file.filename or "未命名文件").strip()[:255]
    ext = os.path.splitext(filename.lower())[1]
    if ext not in SUPPORTED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型：{ext or '未知'}；支持 {sorted(SUPPORTED_EXTS)}",
        )
    staging_path, size_bytes, digest = await _stream_upload(file, ext)
    try:
        try:
            canonical_mime = validate_file_content(staging_path, filename)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        key = _job_key(
            idempotency_key,
            (
                f"file:{principal.tenant_id}:{kb_id}:{document_id}:{digest}"
                if document_id
                else f"file:{principal.tenant_id}:{kb_id}:new:{digest}"
            ),
            principal.tenant_id,
        )
        prior_job = session.exec(
            select(IngestionJob).where(IngestionJob.idempotency_key == key)
        ).first()
        if prior_job is not None and prior_job.tenant_id == principal.tenant_id:
            prior_document = session.get(Document, prior_job.document_id)
            if prior_document is not None:
                response.status_code = 200
                return DocumentRead.model_validate(prior_document)

        if document_id is None:
            duplicate = session.exec(
                select(Document).where(
                    Document.tenant_id == principal.tenant_id,
                    Document.kb_id == kb_id,
                    Document.content_hash == digest,
                    Document.active_version_id != "",
                )
            ).first()
            if duplicate is not None:
                response.status_code = 200
                return DocumentRead.model_validate(duplicate)
            document = Document(
                tenant_id=principal.tenant_id,
                created_by_user_id=principal.user_id,
                kb_id=kb_id,
                name=filename,
                source_type="file",
                source=filename,
                mime=canonical_mime,
                size_bytes=size_bytes,
            )
            session.add(document)
            session.commit()
            session.refresh(document)
            kind = "ingest"
        else:
            document = _require_document(kb_id, document_id, session, principal)
            _ensure_not_processing(session, document)
            if document.content_hash == digest and document.active_version_id:
                response.status_code = 200
                return DocumentRead.model_validate(document)
            kind = "version"

        version_number = document.latest_version + 1
        stored_path = _finalize_file(staging_path, document, version_number, ext)
        staging_path = ""
        version = DocumentVersion(
            tenant_id=principal.tenant_id,
            kb_id=kb_id,
            document_id=document.id,
            created_by_user_id=principal.user_id,
            version_number=version_number,
            name=filename,
            source_type="file",
            source=filename,
            mime=canonical_mime,
            size_bytes=size_bytes,
            stored_path=stored_path,
            content_hash=digest,
            embedding_provider=kb.embedding_provider,
            embedding_model=kb.embedding_model,
            embedding_dim=kb.embedding_dim,
        )
        try:
            job = _create_version_job(
                session,
                document,
                version,
                kind=kind,
                idempotency_key=key,
            )
        except Exception:
            Path(stored_path).unlink(missing_ok=True)
            raise
        background.add_task(process_ingestion_job, job.id)
        record_audit(
            "doc.upload" if kind == "ingest" else "doc.version_upload",
            "accepted",
            request=request,
            principal=principal,
            resource_type="document",
            resource_id=document.id,
            detail={
                "kb_id": kb_id,
                "version": version_number,
                "size_bytes": size_bytes,
                "job_id": job.id,
            },
        )
        session.refresh(document)
        return DocumentRead.model_validate(document)
    finally:
        if staging_path:
            Path(staging_path).unlink(missing_ok=True)


def _url_version(
    *,
    kb_id: str,
    document_id: str | None,
    body: IngestUrlRequest,
    background: BackgroundTasks,
    request: Request,
    response: Response,
    principal: Principal,
    session: Session,
    idempotency_key: str | None,
) -> DocumentRead:
    kb = _require_kb(kb_id, session, principal)
    try:
        url = validate_public_http_url(body.url.strip())
    except UnsafeUrlError as exc:
        record_audit(
            "doc.ingest_url",
            "denied",
            request=request,
            principal=principal,
            resource_type="knowledge_base",
            resource_id=kb_id,
            detail={"reason": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if document_id is None:
        existing = session.exec(
            select(Document).where(
                Document.tenant_id == principal.tenant_id,
                Document.kb_id == kb_id,
                Document.source_type == "url",
                Document.source == url,
            )
        ).first()
        if existing is not None:
            response.status_code = 200
            return DocumentRead.model_validate(existing)
        document = Document(
            tenant_id=principal.tenant_id,
            created_by_user_id=principal.user_id,
            kb_id=kb_id,
            name=url,
            source_type="url",
            source=url,
            mime="text/html",
        )
        session.add(document)
        session.commit()
        session.refresh(document)
        kind = "ingest"
    else:
        document = _require_document(kb_id, document_id, session, principal)
        _ensure_not_processing(session, document)
        kind = "version"

    url_digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    key = _job_key(
        idempotency_key,
        f"url:{principal.tenant_id}:{document.id}:{url_digest}:{int(time.time() // 60)}",
        principal.tenant_id,
    )
    prior_job = session.exec(
        select(IngestionJob).where(IngestionJob.idempotency_key == key)
    ).first()
    if prior_job is not None:
        response.status_code = 200
        return DocumentRead.model_validate(document)

    version_number = document.latest_version + 1
    version = DocumentVersion(
        tenant_id=principal.tenant_id,
        kb_id=kb_id,
        document_id=document.id,
        created_by_user_id=principal.user_id,
        version_number=version_number,
        name=url,
        source_type="url",
        source=url,
        mime="text/html",
        embedding_provider=kb.embedding_provider,
        embedding_model=kb.embedding_model,
        embedding_dim=kb.embedding_dim,
    )
    job = _create_version_job(
        session,
        document,
        version,
        kind=kind,
        idempotency_key=key,
    )
    background.add_task(process_ingestion_job, job.id)
    record_audit(
        "doc.ingest_url" if kind == "ingest" else "doc.version_url",
        "accepted",
        request=request,
        principal=principal,
        resource_type="document",
        resource_id=document.id,
        detail={"kb_id": kb_id, "version": version_number, "job_id": job.id},
    )
    session.refresh(document)
    return DocumentRead.model_validate(document)


@router.post("/upload", response_model=DocumentRead, status_code=201)
async def upload_document(
    kb_id: str,
    background: BackgroundTasks,
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_permission("doc:write")),
    session: Session = Depends(get_session),
) -> DocumentRead:
    return await _file_version(
        kb_id=kb_id,
        document_id=None,
        file=file,
        background=background,
        request=request,
        response=response,
        principal=principal,
        session=session,
        idempotency_key=idempotency_key,
    )


@router.post("/{document_id}/versions/upload", response_model=DocumentRead, status_code=201)
async def upload_document_version(
    kb_id: str,
    document_id: str,
    background: BackgroundTasks,
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_permission("doc:write")),
    session: Session = Depends(get_session),
) -> DocumentRead:
    return await _file_version(
        kb_id=kb_id,
        document_id=document_id,
        file=file,
        background=background,
        request=request,
        response=response,
        principal=principal,
        session=session,
        idempotency_key=idempotency_key,
    )


@router.post("/url", response_model=DocumentRead, status_code=201)
def ingest_url(
    kb_id: str,
    body: IngestUrlRequest,
    background: BackgroundTasks,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_permission("doc:write")),
    session: Session = Depends(get_session),
) -> DocumentRead:
    return _url_version(
        kb_id=kb_id,
        document_id=None,
        body=body,
        background=background,
        request=request,
        response=response,
        principal=principal,
        session=session,
        idempotency_key=idempotency_key,
    )


@router.post("/{document_id}/versions/url", response_model=DocumentRead, status_code=201)
def ingest_url_version(
    kb_id: str,
    document_id: str,
    body: IngestUrlRequest,
    background: BackgroundTasks,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: Principal = Depends(require_permission("doc:write")),
    session: Session = Depends(get_session),
) -> DocumentRead:
    return _url_version(
        kb_id=kb_id,
        document_id=document_id,
        body=body,
        background=background,
        request=request,
        response=response,
        principal=principal,
        session=session,
        idempotency_key=idempotency_key,
    )


@router.get("", response_model=List[DocumentRead])
def list_documents(
    kb_id: str,
    principal: Principal = Depends(require_permission("doc:read")),
    session: Session = Depends(get_session),
) -> List[DocumentRead]:
    _require_kb(kb_id, session, principal)
    documents = session.exec(
        select(Document)
        .where(Document.kb_id == kb_id, Document.tenant_id == principal.tenant_id)
        .order_by(Document.created_at.desc())
    ).all()
    return [DocumentRead.model_validate(document) for document in documents]


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(
    kb_id: str,
    document_id: str,
    principal: Principal = Depends(require_permission("doc:read")),
    session: Session = Depends(get_session),
) -> DocumentRead:
    return DocumentRead.model_validate(
        _require_document(kb_id, document_id, session, principal)
    )


@router.get("/{document_id}/versions", response_model=List[DocumentVersionRead])
def list_document_versions(
    kb_id: str,
    document_id: str,
    principal: Principal = Depends(require_permission("doc:read")),
    session: Session = Depends(get_session),
) -> List[DocumentVersionRead]:
    _require_document(kb_id, document_id, session, principal)
    versions = session.exec(
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.tenant_id == principal.tenant_id,
        )
        .order_by(DocumentVersion.version_number.desc())
    ).all()
    return [DocumentVersionRead.model_validate(version) for version in versions]


@router.get("/{document_id}/jobs", response_model=List[IngestionJobRead])
def list_document_jobs(
    kb_id: str,
    document_id: str,
    principal: Principal = Depends(require_permission("doc:read")),
    session: Session = Depends(get_session),
) -> List[IngestionJobRead]:
    _require_document(kb_id, document_id, session, principal)
    jobs = session.exec(
        select(IngestionJob)
        .where(
            IngestionJob.document_id == document_id,
            IngestionJob.tenant_id == principal.tenant_id,
        )
        .order_by(IngestionJob.created_at.desc())
    ).all()
    return [IngestionJobRead.model_validate(job) for job in jobs]


@router.get("/{document_id}/chunks", response_model=List[ChunkRead])
def list_document_chunks(
    kb_id: str,
    document_id: str,
    version_id: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    principal: Principal = Depends(require_permission("doc:read")),
    session: Session = Depends(get_session),
) -> List[ChunkRead]:
    document = _require_document(kb_id, document_id, session, principal)
    target_version = version_id or document.active_version_id
    if not target_version:
        return []
    version = session.exec(
        select(DocumentVersion).where(
            DocumentVersion.id == target_version,
            DocumentVersion.document_id == document.id,
            DocumentVersion.tenant_id == principal.tenant_id,
        )
    ).first()
    if version is None:
        raise HTTPException(status_code=404, detail="文档版本不存在")
    chunks = session.exec(
        select(Chunk)
        .where(
            Chunk.document_id == document.id,
            Chunk.version_id == version.id,
            Chunk.tenant_id == principal.tenant_id,
        )
        .order_by(Chunk.chunk_index)
        .offset(offset)
        .limit(limit)
    ).all()
    return [ChunkRead.model_validate(chunk) for chunk in chunks]


def _require_job(
    session: Session,
    document: Document,
    job_id: str,
    principal: Principal,
) -> IngestionJob:
    job = session.exec(
        select(IngestionJob).where(
            IngestionJob.id == job_id,
            IngestionJob.document_id == document.id,
            IngestionJob.tenant_id == principal.tenant_id,
        )
    ).first()
    if job is None:
        raise HTTPException(status_code=404, detail="入库任务不存在")
    return job


@router.post("/{document_id}/jobs/{job_id}/cancel", response_model=IngestionJobRead)
def cancel_job(
    kb_id: str,
    document_id: str,
    job_id: str,
    request: Request,
    principal: Principal = Depends(require_permission("doc:write")),
    session: Session = Depends(get_session),
) -> IngestionJobRead:
    document = _require_document(kb_id, document_id, session, principal)
    job = request_job_cancel(session, _require_job(session, document, job_id, principal))
    record_audit(
        "doc.job_cancel",
        "accepted",
        request=request,
        principal=principal,
        resource_type="ingestion_job",
        resource_id=job.id,
        detail={"document_id": document.id},
    )
    return IngestionJobRead.model_validate(job)


@router.post("/{document_id}/jobs/{job_id}/retry", response_model=IngestionJobRead)
def retry_job(
    kb_id: str,
    document_id: str,
    job_id: str,
    background: BackgroundTasks,
    request: Request,
    principal: Principal = Depends(require_permission("doc:write")),
    session: Session = Depends(get_session),
) -> IngestionJobRead:
    document = _require_document(kb_id, document_id, session, principal)
    try:
        job = reset_job_for_retry(session, _require_job(session, document, job_id, principal))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background.add_task(process_ingestion_job, job.id)
    record_audit(
        "doc.job_retry",
        "accepted",
        request=request,
        principal=principal,
        resource_type="ingestion_job",
        resource_id=job.id,
        detail={"document_id": document.id, "attempt": job.attempt},
    )
    return IngestionJobRead.model_validate(job)


@router.post("/{document_id}/reembed", response_model=DocumentRead, status_code=202)
def reembed_document(
    kb_id: str,
    document_id: str,
    background: BackgroundTasks,
    request: Request,
    principal: Principal = Depends(require_permission("doc:write")),
    session: Session = Depends(get_session),
) -> DocumentRead:
    document = _require_document(kb_id, document_id, session, principal)
    _ensure_not_processing(session, document)
    if not document.active_version_id:
        raise HTTPException(status_code=409, detail="文档尚无可重嵌入的有效版本")
    active = session.get(DocumentVersion, document.active_version_id)
    kb = session.get(KnowledgeBase, kb_id)
    if active is None or kb is None:
        raise HTTPException(status_code=409, detail="有效版本或知识库不存在")
    if active.source_type == "file" and not (
        active.stored_path and os.path.exists(active.stored_path)
    ):
        raise HTTPException(status_code=409, detail="原始文件已丢失，无法重嵌入")
    version_number = document.latest_version + 1
    version = DocumentVersion(
        tenant_id=document.tenant_id,
        kb_id=document.kb_id,
        document_id=document.id,
        created_by_user_id=principal.user_id,
        version_number=version_number,
        name=active.name,
        source_type=active.source_type,
        source=active.source,
        mime=active.mime,
        size_bytes=active.size_bytes,
        stored_path=active.stored_path,
        content_hash=active.content_hash,
        embedding_provider=kb.embedding_provider,
        embedding_model=kb.embedding_model,
        embedding_dim=kb.embedding_dim,
    )
    job = _create_version_job(
        session,
        document,
        version,
        kind="reembed",
        idempotency_key=f"reembed:{document.id}:{version.id}",
    )
    background.add_task(process_ingestion_job, job.id)
    record_audit(
        "doc.reembed",
        "accepted",
        request=request,
        principal=principal,
        resource_type="document",
        resource_id=document.id,
        detail={"kb_id": kb_id, "version": version_number, "job_id": job.id},
    )
    session.refresh(document)
    return DocumentRead.model_validate(document)


@router.post("/{document_id}/reconcile", response_model=IngestionJobRead, status_code=202)
def reconcile_document(
    kb_id: str,
    document_id: str,
    background: BackgroundTasks,
    request: Request,
    principal: Principal = Depends(require_permission("doc:write")),
    session: Session = Depends(get_session),
) -> IngestionJobRead:
    document = _require_document(kb_id, document_id, session, principal)
    _ensure_not_processing(session, document)
    if not document.active_version_id:
        raise HTTPException(status_code=409, detail="文档尚无有效版本")
    version = session.get(DocumentVersion, document.active_version_id)
    if version is None:
        raise HTTPException(status_code=409, detail="有效版本不存在")
    job = create_ingestion_job(
        session,
        document,
        version,
        kind="reconcile",
        idempotency_key=f"reconcile:{document.id}:{uuid4().hex}",
        max_attempts=settings.ingestion_max_attempts,
    )
    background.add_task(process_ingestion_job, job.id)
    record_audit(
        "doc.reconcile",
        "accepted",
        request=request,
        principal=principal,
        resource_type="document",
        resource_id=document.id,
        detail={"job_id": job.id},
    )
    return IngestionJobRead.model_validate(job)


@router.delete("/{document_id}", status_code=204)
def delete_document(
    kb_id: str,
    document_id: str,
    request: Request,
    principal: Principal = Depends(require_permission("doc:delete")),
    session: Session = Depends(get_session),
) -> None:
    kb = _require_kb(kb_id, session, principal)
    document = _require_document(kb_id, document_id, session, principal)
    _ensure_not_processing(session, document)
    active_version = (
        session.get(DocumentVersion, document.active_version_id)
        if document.active_version_id
        else None
    )
    active_chunks = list(
        session.exec(
            select(Chunk)
            .where(
                Chunk.document_id == document.id,
                Chunk.version_id == document.active_version_id,
                Chunk.is_active.is_(True),
            )
            .order_by(Chunk.chunk_index)
        ).all()
    )
    restore_points: List[VectorPoint] = []
    if active_version is not None and active_chunks:
        try:
            embedder = make_embeddings(
                active_version.embedding_provider,
                active_version.embedding_model,
                active_version.embedding_dim,
            )
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
                detail=f"无法准备删除补偿，文档未删除：{type(exc).__name__}",
            ) from exc

    collection = collection_for_kb(kb)
    store = get_vector_store()
    try:
        store.delete_document(collection, document.id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"向量删除失败，元数据未删除：{type(exc).__name__}",
        ) from exc

    stored_paths = list(
        session.exec(
            select(DocumentVersion.stored_path).where(
                DocumentVersion.document_id == document.id
            )
        ).all()
    )
    try:
        session.execute(sa_delete(Chunk).where(Chunk.document_id == document.id))
        session.execute(sa_delete(IngestionJob).where(IngestionJob.document_id == document.id))
        session.execute(
            sa_delete(DocumentVersion).where(DocumentVersion.document_id == document.id)
        )
        session.delete(document)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        if restore_points and active_version is not None:
            store.ensure_collection(collection, active_version.embedding_dim)
            store.upsert(collection, restore_points)
        raise HTTPException(status_code=500, detail="元数据删除失败，向量已恢复") from exc

    cleanup_failures = 0
    for stored_path in set(path for path in stored_paths if path):
        try:
            Path(stored_path).unlink(missing_ok=True)
        except OSError:
            cleanup_failures += 1
    bm25_index.invalidate(kb_id)
    record_audit(
        "doc.delete",
        request=request,
        principal=principal,
        resource_type="document",
        resource_id=document_id,
        detail={"kb_id": kb_id, "file_cleanup_failures": cleanup_failures},
    )
