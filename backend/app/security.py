"""认证、密码哈希与权限工具。

不依赖外部认证库，使用标准库实现：
- PBKDF2-SHA256 密码哈希；
- HS256 JWT Bearer Token；
- 基于角色的权限集合计算。

生产部署时必须通过环境变量覆盖 ``AUTH_SECRET_KEY`` 与引导管理员密码。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Set

from sqlmodel import Session, select

from .config import settings
from .models import Permission, Role, RolePermission, Tenant, User, UserRole

TOKEN_ALGORITHM = "HS256"
PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 210_000

DEFAULT_PERMISSIONS: Dict[str, str] = {
    "kb:read": "读取本租户知识库",
    "kb:write": "创建与更新本租户知识库",
    "kb:delete": "删除本租户知识库",
    "doc:read": "读取本租户文档",
    "doc:write": "上传、抓取与重嵌入本租户文档",
    "doc:delete": "删除本租户文档",
    "chat:use": "使用本租户检索与问答",
    "conversation:delete": "删除本租户会话",
    "admin:manage": "管理本租户用户与角色",
}

ADMIN_ROLE = "admin"


@dataclass(frozen=True)
class Principal:
    """当前登录主体。"""

    user: User
    tenant: Tenant
    permissions: Set[str]
    roles: tuple[Role, ...] = ()

    @property
    def user_id(self) -> str:
        return self.user.id

    @property
    def tenant_id(self) -> str:
        return self.tenant.id

    def can(self, permission: str) -> bool:
        return self.user.is_superuser or permission in self.permissions


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("ascii"),
        PASSWORD_ITERATIONS,
    ).hex()
    return f"{PASSWORD_SCHEME}${PASSWORD_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored_hash.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("ascii"),
            int(iterations),
        ).hex()
        return hmac.compare_digest(candidate, digest)
    except Exception:
        return False


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "tenant_id": user.tenant_id,
        "email": user.email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
    }
    header = {"alg": TOKEN_ALGORITHM, "typ": "JWT"}
    signing_input = ".".join(
        [
            _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ]
    )
    signature = hmac.new(
        settings.auth_secret_key.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> Dict:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".", 2)
        signing_input = f"{header_b64}.{payload_b64}"
        expected = hmac.new(
            settings.auth_secret_key.encode("utf-8"),
            signing_input.encode("ascii"),
            hashlib.sha256,
        ).digest()
        supplied = _b64url_decode(signature_b64)
        if not hmac.compare_digest(expected, supplied):
            raise ValueError("签名无效")
        header = json.loads(_b64url_decode(header_b64))
        if header.get("alg") != TOKEN_ALGORITHM:
            raise ValueError("算法不支持")
        payload = json.loads(_b64url_decode(payload_b64))
        exp = int(payload.get("exp", 0))
        if exp < int(datetime.now(timezone.utc).timestamp()):
            raise ValueError("Token 已过期")
        return payload
    except Exception as exc:  # noqa: BLE001
        raise ValueError("无效登录凭证") from exc


def permissions_for_user(session: Session, user: User) -> Set[str]:
    if user.is_superuser:
        return set(DEFAULT_PERMISSIONS)
    rows = session.exec(
        select(RolePermission.permission_code)
        .join(Role, Role.id == RolePermission.role_id)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.id, Role.tenant_id == user.tenant_id)
    ).all()
    return set(rows)


def ensure_permissions(session: Session) -> None:
    for code, description in DEFAULT_PERMISSIONS.items():
        existing = session.get(Permission, code)
        if existing is None:
            session.add(Permission(code=code, description=description))
    session.commit()


def _ensure_role(session: Session, tenant_id: str, name: str, permissions: Iterable[str]) -> Role:
    role = session.exec(
        select(Role).where(Role.tenant_id == tenant_id, Role.name == name)
    ).first()
    if role is None:
        role = Role(tenant_id=tenant_id, name=name, description=f"{name} role")
        session.add(role)
        session.commit()
        session.refresh(role)

    existing_codes = set(
        session.exec(
            select(RolePermission.permission_code).where(RolePermission.role_id == role.id)
        ).all()
    )
    for code in permissions:
        if code not in existing_codes:
            session.add(RolePermission(role_id=role.id, permission_code=code))
    session.commit()
    return role


def ensure_bootstrap_identity(session: Session) -> None:
    """创建默认租户、权限、管理员角色与管理员账号。

    该函数幂等；不会打印或泄露引导密码。
    """

    ensure_permissions(session)

    tenant = session.exec(
        select(Tenant).where(Tenant.slug == settings.bootstrap_tenant_slug)
    ).first()
    if tenant is None:
        tenant = Tenant(
            name=settings.bootstrap_tenant_name,
            slug=settings.bootstrap_tenant_slug,
        )
        session.add(tenant)
        session.commit()
        session.refresh(tenant)

    admin_role = _ensure_role(session, tenant.id, ADMIN_ROLE, DEFAULT_PERMISSIONS.keys())
    email = settings.bootstrap_admin_email.lower().strip()
    user = session.exec(
        select(User).where(User.tenant_id == tenant.id, User.email == email)
    ).first()
    if user is None:
        user = User(
            tenant_id=tenant.id,
            email=email,
            display_name=settings.bootstrap_admin_name,
            password_hash=hash_password(settings.bootstrap_admin_password),
            is_superuser=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)

    link = session.get(UserRole, (user.id, admin_role.id))
    if link is None:
        session.add(UserRole(user_id=user.id, role_id=admin_role.id))
        session.commit()
