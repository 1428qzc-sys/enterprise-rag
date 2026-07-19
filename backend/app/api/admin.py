"""租户内用户管理接口。

当前版本提供最小可用的后台管理闭环：租户管理员可创建本租户用户并查看用户列表。
"""

from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from ..audit import record_audit
from ..database import get_session
from ..models import AuditLog, User, UserRole
from ..schemas import AdminUserCreate, AuditLogRead, UserRead
from ..security import (
    ADMIN_ROLE,
    DEFAULT_PERMISSIONS,
    Principal,
    _ensure_role,
    hash_password,
    permissions_for_user,
)
from .deps import require_permission

router = APIRouter(prefix="/admin", tags=["admin"])

MEMBER_ROLE = "member"
MEMBER_PERMISSIONS = [
    "kb:read",
    "kb:write",
    "doc:read",
    "doc:write",
    "chat:use",
    "conversation:delete",
]


def _user_read(session: Session, user: User) -> UserRead:
    return UserRead(
        id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        permissions=sorted(permissions_for_user(session, user)),
    )


@router.get("/users", response_model=List[UserRead])
def list_users(
    principal: Principal = Depends(require_permission("admin:manage")),
    session: Session = Depends(get_session),
) -> List[UserRead]:
    users = session.exec(
        select(User).where(User.tenant_id == principal.tenant_id).order_by(User.created_at.desc())
    ).all()
    return [_user_read(session, user) for user in users]


@router.post("/users", response_model=UserRead, status_code=201)
def create_user(
    body: AdminUserCreate,
    request: Request,
    principal: Principal = Depends(require_permission("admin:manage")),
    session: Session = Depends(get_session),
) -> UserRead:
    email = body.email.lower().strip()
    if session.exec(select(User).where(User.email == email)).first() is not None:
        raise HTTPException(status_code=409, detail="邮箱已存在")

    user = User(
        tenant_id=principal.tenant_id,
        email=email,
        display_name=body.display_name.strip() or email,
        password_hash=hash_password(body.password),
        is_active=body.is_active,
        is_superuser=body.is_superuser,
        updated_at=datetime.utcnow(),
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    if body.is_superuser:
        role = _ensure_role(session, principal.tenant_id, ADMIN_ROLE, DEFAULT_PERMISSIONS.keys())
    else:
        role = _ensure_role(session, principal.tenant_id, MEMBER_ROLE, MEMBER_PERMISSIONS)
    session.add(UserRole(user_id=user.id, role_id=role.id))
    session.commit()
    record_audit(
        "admin.user_create",
        "success",
        request=request,
        principal=principal,
        resource_type="user",
        resource_id=user.id,
        detail={"email": email, "is_superuser": body.is_superuser},
    )
    return _user_read(session, user)


@router.get("/audit-logs", response_model=List[AuditLogRead])
def list_audit_logs(
    principal: Principal = Depends(require_permission("admin:manage")),
    session: Session = Depends(get_session),
) -> List[AuditLogRead]:
    rows = session.exec(
        select(AuditLog)
        .where(AuditLog.tenant_id == principal.tenant_id)
        .order_by(AuditLog.created_at.desc())
        .limit(200)
    ).all()
    return [AuditLogRead.model_validate(row) for row in rows]
