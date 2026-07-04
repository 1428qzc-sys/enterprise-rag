"""文档管理：上传文件 / 抓取 URL / 列表 / 详情 / 删除 / 重嵌入。"""

from __future__ import annotations

import os
from datetime import datetime
from typing import List

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
)
from sqlalchemy import delete as sa_delete
from sqlmodel import Session, select

from ..config import settings
from ..core.vector_store import collection_name, get_vector_store
from ..database import get_session
from ..models import Chunk, DocStatus, Document, KnowledgeBase
from ..schemas import DocumentRead, IngestUrlRequest
from ..services import bm25_index
from ..services.ingestion import process_document
from ..services.parsing import SUPPORTED_EXTS

router = APIRouter(prefix="/knowledge-bases/{kb_id}/documents", tags=["documents"])


def _require_kb(kb_id: str, session: Session) -> KnowledgeBase:
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb


@router.post("/upload", response_model=DocumentRead, status_code=201)
async def upload_document(
    kb_id: str,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> DocumentRead:
    _require_kb(kb_id, session)
    filename = file.filename or "未命名文件"
    ext = os.path.splitext(filename.lower())[1]
    if ext not in SUPPORTED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型：{ext or '未知'}；支持 {sorted(SUPPORTED_EXTS)}",
        )

    content = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"文件超过 {settings.max_upload_mb}MB 上限")

    doc = Document(
        kb_id=kb_id,
        name=filename,
        source_type="file",
        source=filename,
        mime=file.content_type or "",
        size_bytes=len(content),
        status=DocStatus.PENDING,
    )
    os.makedirs(settings.upload_dir, exist_ok=True)
    stored_path = os.path.join(settings.upload_dir, f"{doc.id}{ext}")
    with open(stored_path, "wb") as f:
        f.write(content)
    doc.stored_path = stored_path

    session.add(doc)
    session.commit()
    session.refresh(doc)

    background.add_task(process_document, doc.id)
    return DocumentRead.model_validate(doc)


@router.post("/url", response_model=DocumentRead, status_code=201)
def ingest_url(
    kb_id: str,
    body: IngestUrlRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
) -> DocumentRead:
    _require_kb(kb_id, session)
    url = body.url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="URL 需以 http:// 或 https:// 开头")

    doc = Document(
        kb_id=kb_id,
        name=url,
        source_type="url",
        source=url,
        mime="text/html",
        status=DocStatus.PENDING,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)

    background.add_task(process_document, doc.id)
    return DocumentRead.model_validate(doc)


@router.get("", response_model=List[DocumentRead])
def list_documents(kb_id: str, session: Session = Depends(get_session)) -> List[DocumentRead]:
    _require_kb(kb_id, session)
    docs = session.exec(
        select(Document).where(Document.kb_id == kb_id).order_by(Document.created_at.desc())
    ).all()
    return [DocumentRead.model_validate(d) for d in docs]


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(kb_id: str, document_id: str, session: Session = Depends(get_session)) -> DocumentRead:
    _require_kb(kb_id, session)
    doc = session.get(Document, document_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    return DocumentRead.model_validate(doc)


@router.delete("/{document_id}", status_code=204)
def delete_document(kb_id: str, document_id: str, session: Session = Depends(get_session)) -> None:
    _require_kb(kb_id, session)
    doc = session.get(Document, document_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")

    session.execute(sa_delete(Chunk).where(Chunk.document_id == document_id))
    session.delete(doc)
    session.commit()

    try:
        get_vector_store().delete_document(collection_name(kb_id), document_id)
    except Exception:
        pass
    if doc.stored_path and os.path.exists(doc.stored_path):
        try:
            os.remove(doc.stored_path)
        except OSError:
            pass
    bm25_index.invalidate(kb_id)


@router.post("/{document_id}/reembed", response_model=DocumentRead)
def reembed_document(
    kb_id: str,
    document_id: str,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
) -> DocumentRead:
    _require_kb(kb_id, session)
    doc = session.get(Document, document_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc.source_type == "file" and not (doc.stored_path and os.path.exists(doc.stored_path)):
        raise HTTPException(status_code=409, detail="原始文件已丢失，无法重嵌入")

    doc.status = DocStatus.PENDING
    doc.error = ""
    doc.updated_at = datetime.utcnow()
    session.add(doc)
    session.commit()
    session.refresh(doc)

    background.add_task(process_document, doc.id)
    return DocumentRead.model_validate(doc)
