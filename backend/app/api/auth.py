"""认证接口：登录与当前用户信息。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..database import get_session
from ..models import Tenant, User
from ..schemas import LoginRequest, MeResponse, TenantRead, TokenResponse, UserRead
from ..security import Principal, create_access_token, verify_password
from .deps import get_current_principal

router = APIRouter(prefix="/auth", tags=["auth"])


def _to_user_read(principal: Principal) -> UserRead:
    return UserRead(
        id=principal.user.id,
        tenant_id=principal.user.tenant_id,
        email=principal.user.email,
        display_name=principal.user.display_name,
        is_active=principal.user.is_active,
        is_superuser=principal.user.is_superuser,
        permissions=sorted(principal.permissions),
    )


def _to_tenant_read(tenant: Tenant) -> TenantRead:
    return TenantRead(id=tenant.id, name=tenant.name, slug=tenant.slug)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, session: Session = Depends(get_session)) -> TokenResponse:
    email = body.email.lower().strip()
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    tenant = session.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(status_code=403, detail="租户不存在或已停用")

    # 登录响应里的权限用于前端展示；Token 本身只保存主体身份，权限每次由数据库计算。
    from ..security import permissions_for_user

    principal = Principal(
        user=user,
        tenant=tenant,
        permissions=permissions_for_user(session, user),
    )
    return TokenResponse(
        access_token=create_access_token(user),
        user=_to_user_read(principal),
        tenant=_to_tenant_read(tenant),
    )


@router.get("/me", response_model=MeResponse)
def me(principal: Principal = Depends(get_current_principal)) -> MeResponse:
    return MeResponse(
        user=_to_user_read(principal),
        tenant=_to_tenant_read(principal.tenant),
    )
