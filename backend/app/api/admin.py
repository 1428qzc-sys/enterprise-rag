"""租户内用户、角色、权限与审计管理接口。"""

from __future__ import annotations

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete as sa_delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..audit import record_audit
from ..database import get_session
from ..models import (
    AuditLog,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from ..schemas import (
    AdminRoleCreate,
    AdminRoleUpdate,
    AdminUserCreate,
    AdminUserUpdate,
    AuditLogRead,
    PermissionRead,
    RoleRead,
    UserRead,
)
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
SYSTEM_ROLES = {ADMIN_ROLE, MEMBER_ROLE}


def _roles_for_user(session: Session, user: User) -> List[Role]:
    return list(
        session.exec(
            select(Role)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user.id, Role.tenant_id == user.tenant_id)
            .order_by(Role.name)
        ).all()
    )


def _user_read(session: Session, user: User) -> UserRead:
    roles = _roles_for_user(session, user)
    return UserRead(
        id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        permissions=sorted(permissions_for_user(session, user)),
        role_ids=[role.id for role in roles],
        role_names=[role.name for role in roles],
    )


def _role_permissions(session: Session, role_id: str) -> List[str]:
    return sorted(
        session.exec(
            select(RolePermission.permission_code).where(RolePermission.role_id == role_id)
        ).all()
    )


def _role_read(session: Session, role: Role) -> RoleRead:
    users = session.exec(select(UserRole.user_id).where(UserRole.role_id == role.id)).all()
    return RoleRead(
        id=role.id,
        tenant_id=role.tenant_id or "",
        name=role.name,
        description=role.description,
        permissions=_role_permissions(session, role.id),
        user_count=len(users),
        is_system=role.name in SYSTEM_ROLES,
        created_at=role.created_at,
    )


def _get_user(session: Session, tenant_id: str, user_id: str) -> User:
    user = session.exec(
        select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    ).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


def _get_role(session: Session, tenant_id: str, role_id: str) -> Role:
    role = session.exec(
        select(Role).where(Role.id == role_id, Role.tenant_id == tenant_id)
    ).first()
    if role is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    return role


def _validate_permission_codes(session: Session, codes: List[str]) -> List[str]:
    normalized = sorted(set(codes))
    if not normalized:
        return []
    existing = set(
        session.exec(select(Permission.code).where(Permission.code.in_(normalized))).all()
    )
    invalid = [code for code in normalized if code not in existing]
    if invalid:
        raise HTTPException(status_code=400, detail=f"未知权限：{', '.join(invalid)}")
    return normalized


def _validate_roles(session: Session, tenant_id: str, role_ids: List[str]) -> List[Role]:
    normalized = list(dict.fromkeys(role_ids))
    if not normalized:
        return []
    roles = list(
        session.exec(
            select(Role).where(Role.tenant_id == tenant_id, Role.id.in_(normalized))
        ).all()
    )
    if len(roles) != len(normalized):
        raise HTTPException(status_code=400, detail="包含不存在或不属于当前租户的角色")
    by_id = {role.id: role for role in roles}
    return [by_id[role_id] for role_id in normalized]


def _replace_user_roles(session: Session, user: User, roles: List[Role]) -> None:
    session.execute(sa_delete(UserRole).where(UserRole.user_id == user.id))
    for role in roles:
        session.add(UserRole(user_id=user.id, role_id=role.id))


def _is_tenant_admin(session: Session, user: User) -> bool:
    return user.is_superuser or ADMIN_ROLE in {role.name for role in _roles_for_user(session, user)}


def _has_other_active_admin(session: Session, user: User) -> bool:
    candidates = session.exec(
        select(User).where(
            User.tenant_id == user.tenant_id,
            User.is_active.is_(True),
            User.id != user.id,
        )
    ).all()
    return any(_is_tenant_admin(session, candidate) for candidate in candidates)


@router.get("/permissions", response_model=List[PermissionRead])
def list_permissions(
    _principal: Principal = Depends(require_permission("admin:manage")),
    session: Session = Depends(get_session),
) -> List[PermissionRead]:
    rows = session.exec(select(Permission).order_by(Permission.code)).all()
    return [PermissionRead(code=row.code, description=row.description) for row in rows]


@router.get("/roles", response_model=List[RoleRead])
def list_roles(
    principal: Principal = Depends(require_permission("admin:manage")),
    session: Session = Depends(get_session),
) -> List[RoleRead]:
    roles = session.exec(
        select(Role)
        .where(Role.tenant_id == principal.tenant_id)
        .order_by(Role.created_at, Role.name)
    ).all()
    return [_role_read(session, role) for role in roles]


@router.post("/roles", response_model=RoleRead, status_code=201)
def create_role(
    body: AdminRoleCreate,
    request: Request,
    principal: Principal = Depends(require_permission("admin:manage")),
    session: Session = Depends(get_session),
) -> RoleRead:
    name = body.name.strip()
    existing = session.exec(
        select(Role).where(Role.tenant_id == principal.tenant_id, Role.name == name)
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="角色名已存在")
    codes = _validate_permission_codes(session, body.permissions)
    role = Role(
        tenant_id=principal.tenant_id,
        name=name,
        description=body.description.strip(),
    )
    session.add(role)
    session.commit()
    session.refresh(role)
    for code in codes:
        session.add(RolePermission(role_id=role.id, permission_code=code))
    session.commit()
    record_audit(
        "admin.role_create",
        request=request,
        principal=principal,
        resource_type="role",
        resource_id=role.id,
        detail={"name": role.name, "permissions": codes},
    )
    return _role_read(session, role)


@router.patch("/roles/{role_id}", response_model=RoleRead)
def update_role(
    role_id: str,
    body: AdminRoleUpdate,
    request: Request,
    principal: Principal = Depends(require_permission("admin:manage")),
    session: Session = Depends(get_session),
) -> RoleRead:
    role = _get_role(session, principal.tenant_id, role_id)
    if body.name is not None:
        new_name = body.name.strip()
        if role.name in SYSTEM_ROLES and new_name != role.name:
            raise HTTPException(status_code=409, detail="系统角色不能重命名")
        duplicate = session.exec(
            select(Role).where(
                Role.tenant_id == principal.tenant_id,
                Role.name == new_name,
                Role.id != role.id,
            )
        ).first()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="角色名已存在")
        role.name = new_name
    if body.description is not None:
        role.description = body.description.strip()
    if body.permissions is not None:
        codes = _validate_permission_codes(session, body.permissions)
        if role.name == ADMIN_ROLE and set(codes) != set(DEFAULT_PERMISSIONS):
            raise HTTPException(status_code=409, detail="管理员角色必须保留全部权限")
        session.execute(
            sa_delete(RolePermission).where(RolePermission.role_id == role.id)
        )
        for code in codes:
            session.add(RolePermission(role_id=role.id, permission_code=code))
    session.add(role)
    session.commit()
    session.refresh(role)
    record_audit(
        "admin.role_update",
        request=request,
        principal=principal,
        resource_type="role",
        resource_id=role.id,
        detail={"name": role.name, "permissions": _role_permissions(session, role.id)},
    )
    return _role_read(session, role)


@router.delete("/roles/{role_id}", status_code=204)
def delete_role(
    role_id: str,
    request: Request,
    principal: Principal = Depends(require_permission("admin:manage")),
    session: Session = Depends(get_session),
) -> None:
    role = _get_role(session, principal.tenant_id, role_id)
    if role.name in SYSTEM_ROLES:
        raise HTTPException(status_code=409, detail="系统角色不能删除")
    assigned = session.exec(
        select(UserRole.user_id).where(UserRole.role_id == role.id).limit(1)
    ).first()
    if assigned is not None:
        raise HTTPException(status_code=409, detail="角色仍有用户，不能删除")
    session.execute(sa_delete(RolePermission).where(RolePermission.role_id == role.id))
    session.delete(role)
    session.commit()
    record_audit(
        "admin.role_delete",
        request=request,
        principal=principal,
        resource_type="role",
        resource_id=role_id,
    )


@router.get("/users", response_model=List[UserRead])
def list_users(
    principal: Principal = Depends(require_permission("admin:manage")),
    session: Session = Depends(get_session),
) -> List[UserRead]:
    users = session.exec(
        select(User)
        .where(User.tenant_id == principal.tenant_id)
        .order_by(User.created_at.desc())
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
    if session.exec(
        select(User).where(User.tenant_id == principal.tenant_id, User.email == email)
    ).first() is not None:
        raise HTTPException(status_code=409, detail="邮箱在当前租户已存在")

    roles = _validate_roles(session, principal.tenant_id, body.role_ids)
    if not roles:
        roles = [_ensure_role(session, principal.tenant_id, MEMBER_ROLE, MEMBER_PERMISSIONS)]
    user = User(
        tenant_id=principal.tenant_id,
        email=email,
        display_name=body.display_name.strip() or email,
        password_hash=hash_password(body.password),
        is_active=body.is_active,
        is_superuser=False,
        updated_at=datetime.utcnow(),
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="邮箱在当前租户已存在") from exc
    session.refresh(user)
    _replace_user_roles(session, user, roles)
    session.commit()
    record_audit(
        "admin.user_create",
        request=request,
        principal=principal,
        resource_type="user",
        resource_id=user.id,
        detail={"email": email, "role_ids": [role.id for role in roles]},
    )
    return _user_read(session, user)


@router.patch("/users/{user_id}", response_model=UserRead)
def update_user(
    user_id: str,
    body: AdminUserUpdate,
    request: Request,
    principal: Principal = Depends(require_permission("admin:manage")),
    session: Session = Depends(get_session),
) -> UserRead:
    user = _get_user(session, principal.tenant_id, user_id)
    if body.is_active is False and user.id == principal.user_id:
        raise HTTPException(status_code=409, detail="不能停用当前登录账号")

    roles = None
    if body.role_ids is not None:
        roles = _validate_roles(session, principal.tenant_id, body.role_ids)
    next_active = user.is_active if body.is_active is None else body.is_active
    next_admin = user.is_superuser or (
        roles is not None and ADMIN_ROLE in {role.name for role in roles}
    ) or (roles is None and _is_tenant_admin(session, user))
    if user.is_active and _is_tenant_admin(session, user) and not (next_active and next_admin):
        if not _has_other_active_admin(session, user):
            raise HTTPException(status_code=409, detail="租户必须至少保留一名可用管理员")

    if body.display_name is not None:
        user.display_name = body.display_name.strip() or user.email
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.is_active is not None:
        user.is_active = body.is_active
    user.updated_at = datetime.utcnow()
    session.add(user)
    if roles is not None:
        _replace_user_roles(session, user, roles)
    session.commit()
    session.refresh(user)
    record_audit(
        "admin.user_update",
        request=request,
        principal=principal,
        resource_type="user",
        resource_id=user.id,
        detail={
            "is_active": user.is_active,
            "role_ids": [role.id for role in _roles_for_user(session, user)],
            "password_changed": body.password is not None,
        },
    )
    return _user_read(session, user)


@router.get("/audit-logs", response_model=List[AuditLogRead])
def list_audit_logs(
    action: str | None = Query(default=None, max_length=128),
    outcome: str | None = Query(default=None, max_length=32),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(require_permission("admin:manage")),
    session: Session = Depends(get_session),
) -> List[AuditLogRead]:
    query = select(AuditLog).where(AuditLog.tenant_id == principal.tenant_id)
    if action:
        query = query.where(AuditLog.action == action)
    if outcome:
        query = query.where(AuditLog.outcome == outcome)
    rows = session.exec(
        query.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return [AuditLogRead.model_validate(row) for row in rows]
