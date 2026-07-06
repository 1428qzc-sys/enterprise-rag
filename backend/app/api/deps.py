"""路由公共依赖：数据库会话、认证主体、租户数据访问控制。"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from ..database import get_session
from ..models import KnowledgeBase, Tenant, User
from ..security import Principal, decode_access_token, permissions_for_user

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_session),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = session.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号不存在或已停用",
            headers={"WWW-Authenticate": "Bearer"},
        )
    tenant = session.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(status_code=403, detail="租户不存在或已停用")

    return Principal(user=user, tenant=tenant, permissions=permissions_for_user(session, user))


def require_permission(permission: str) -> Callable:
    def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not principal.can(permission):
            raise HTTPException(status_code=403, detail="权限不足")
        return principal

    return dependency


def can_access_kb(principal: Principal, kb: KnowledgeBase) -> bool:
    return principal.user.is_superuser or kb.tenant_id == principal.tenant_id


def get_kb_or_404(
    kb_id: str,
    principal: Principal = Depends(require_permission("kb:read")),
    session: Session = Depends(get_session),
) -> KnowledgeBase:
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None or not can_access_kb(principal, kb):
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb
