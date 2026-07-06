"""知识库管理：创建 / 列表 / 详情 / 更新 / 删除（含级联清理向量与文档）。"""

from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete as sa_delete
from sqlalchemy import func
from sqlmodel import Session, select

from ..config import settings
from ..core.vector_store import collection_name, get_vector_store
from ..database import get_session
from ..models import Chunk, Conversation, Document, KnowledgeBase, Message
from ..schemas import KBCreate, KBRead, KBUpdate
from ..security import Principal
from ..services import bm25_index
from .deps import can_access_kb, require_permission

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


def _to_read(session: Session, kb: KnowledgeBase) -> KBRead:
    doc_count = session.scalar(
        select(func.count()).select_from(Document).where(Document.kb_id == kb.id)
    ) or 0
    chunk_count = session.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.kb_id == kb.id)
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
        document_count=int(doc_count),
        chunk_count=int(chunk_count),
        created_at=kb.created_at,
        updated_at=kb.updated_at,
    )


@router.post("", response_model=KBRead, status_code=201)
def create_kb(
    body: KBCreate,
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
    session.add(kb)
    session.commit()
    session.refresh(kb)
    return _to_read(session, kb)


@router.get("", response_model=List[KBRead])
def list_kbs(
    principal: Principal = Depends(require_permission("kb:read")),
    session: Session = Depends(get_session),
) -> List[KBRead]:
    query = select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())
    if not principal.user.is_superuser:
        query = query.where(KnowledgeBase.tenant_id == principal.tenant_id)
    kbs = session.exec(query).all()
    return [_to_read(session, kb) for kb in kbs]


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


@router.patch("/{kb_id}", response_model=KBRead)
def update_kb(
    kb_id: str,
    body: KBUpdate,
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
    return _to_read(session, kb)


@router.delete("/{kb_id}", status_code=204)
def delete_kb(
    kb_id: str,
    principal: Principal = Depends(require_permission("kb:delete")),
    session: Session = Depends(get_session),
) -> None:
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None or not can_access_kb(principal, kb):
        raise HTTPException(status_code=404, detail="知识库不存在")

    convs = session.exec(select(Conversation).where(Conversation.kb_id == kb_id)).all()
    conv_ids = [c.id for c in convs]
    if conv_ids:
        session.execute(sa_delete(Message).where(Message.conversation_id.in_(conv_ids)))
    session.execute(sa_delete(Conversation).where(Conversation.kb_id == kb_id))
    session.execute(sa_delete(Chunk).where(Chunk.kb_id == kb_id))
    session.execute(sa_delete(Document).where(Document.kb_id == kb_id))
    session.delete(kb)
    session.commit()

    # 清理向量库与稀疏索引缓存（失败不影响主删除）
    try:
        get_vector_store().delete_collection(collection_name(kb_id))
    except Exception:
        pass
    bm25_index.invalidate(kb_id)
