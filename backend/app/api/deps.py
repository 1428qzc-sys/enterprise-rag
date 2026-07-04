"""路由公共依赖。"""

from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlmodel import Session

from ..database import get_session
from ..models import KnowledgeBase


def get_kb_or_404(kb_id: str, session: Session = Depends(get_session)) -> KnowledgeBase:
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb
