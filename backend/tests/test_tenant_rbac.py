"""租户隔离与 RBAC 管理回归。"""

from __future__ import annotations

from uuid import uuid4

from sqlmodel import Session

from app.database import engine
from app.models import Tenant, User, UserRole
from app.security import ADMIN_ROLE, DEFAULT_PERMISSIONS, _ensure_role, hash_password
from tests.test_api import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    _auth_headers,
    _create_kb,
    _login,
    _upload_text,
    _wait_done,
)


def _create_tenant_admin(*, superuser: bool = True) -> tuple[str, str, str]:
    suffix = uuid4().hex[:10]
    email = f"tenant-{suffix}@example.com"
    password = "TenantAdmin123!"
    with Session(engine) as session:
        tenant = Tenant(name=f"Tenant {suffix}", slug=f"tenant-{suffix}")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        user = User(
            tenant_id=tenant.id,
            email=email,
            display_name="Other Tenant Admin",
            password_hash=hash_password(password),
            is_superuser=superuser,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        role = _ensure_role(session, tenant.id, ADMIN_ROLE, DEFAULT_PERMISSIONS.keys())
        session.add(UserRole(user_id=user.id, role_id=role.id))
        session.commit()
        tenant_id = tenant.id
    return tenant_id, email, password


def test_login_uses_tenant_slug_when_email_exists_in_multiple_tenants(client):
    suffix = uuid4().hex[:10]
    other_slug = f"duplicate-{suffix}"
    with Session(engine) as session:
        tenant = Tenant(name=f"Duplicate {suffix}", slug=other_slug)
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        session.add(
            User(
                tenant_id=tenant.id,
                email=ADMIN_EMAIL,
                display_name="Duplicate Email Admin",
                password_hash=hash_password("OtherTenant123!"),
                is_superuser=True,
            )
        )
        session.commit()

    ambiguous = client.post(
        "/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert ambiguous.status_code == 401

    demo = client.post(
        "/api/auth/login",
        json={
            "tenant_slug": "demo",
            "email": ADMIN_EMAIL,
            "password": ADMIN_PASSWORD,
        },
    )
    assert demo.status_code == 200
    assert demo.json()["tenant"]["slug"] == "demo"

    other = client.post(
        "/api/auth/login",
        json={
            "tenant_slug": other_slug,
            "email": ADMIN_EMAIL,
            "password": "OtherTenant123!",
        },
    )
    assert other.status_code == 200
    assert other.json()["tenant"]["slug"] == other_slug


def test_tenant_superuser_cannot_cross_resource_boundaries(client):
    owner_headers = _auth_headers(client)
    kb_id = _create_kb(client, f"tenant-boundary-{uuid4().hex[:8]}")
    doc_id = _upload_text(client, kb_id)
    assert _wait_done(client, kb_id, doc_id)["status"] == "done"
    chat = client.post(
        "/api/chat",
        json={"kb_id": kb_id, "question": "年假有多少天？", "stream": False},
        headers=owner_headers,
    )
    assert chat.status_code == 200
    conversation_id = chat.json()["conversation_id"]
    owner_jobs = client.get(
        f"/api/knowledge-bases/{kb_id}/documents/{doc_id}/jobs", headers=owner_headers
    ).json()
    job_id = owner_jobs[0]["id"]

    other_tenant_id, email, password = _create_tenant_admin(superuser=True)
    other_headers = _auth_headers(client, email, password)

    listed = client.get("/api/knowledge-bases", headers=other_headers)
    assert listed.status_code == 200
    assert all(row["id"] != kb_id for row in listed.json())

    attempts = [
        client.get(f"/api/knowledge-bases/{kb_id}", headers=other_headers),
        client.patch(
            f"/api/knowledge-bases/{kb_id}",
            json={"name": "cross-tenant-update"},
            headers=other_headers,
        ),
        client.delete(f"/api/knowledge-bases/{kb_id}", headers=other_headers),
        client.get(f"/api/knowledge-bases/{kb_id}/documents", headers=other_headers),
        client.get(
            f"/api/knowledge-bases/{kb_id}/documents/{doc_id}", headers=other_headers
        ),
        client.delete(
            f"/api/knowledge-bases/{kb_id}/documents/{doc_id}", headers=other_headers
        ),
        client.post(
            f"/api/knowledge-bases/{kb_id}/documents/{doc_id}/reembed",
            headers=other_headers,
        ),
        client.get(
            f"/api/knowledge-bases/{kb_id}/documents/{doc_id}/versions",
            headers=other_headers,
        ),
        client.get(
            f"/api/knowledge-bases/{kb_id}/documents/{doc_id}/jobs",
            headers=other_headers,
        ),
        client.get(
            f"/api/knowledge-bases/{kb_id}/documents/{doc_id}/chunks",
            headers=other_headers,
        ),
        client.post(
            f"/api/knowledge-bases/{kb_id}/documents/{doc_id}/jobs/{job_id}/cancel",
            headers=other_headers,
        ),
        client.post(
            f"/api/knowledge-bases/{kb_id}/documents/{doc_id}/jobs/{job_id}/retry",
            headers=other_headers,
        ),
        client.post(
            f"/api/knowledge-bases/{kb_id}/documents/{doc_id}/reconcile",
            headers=other_headers,
        ),
        client.post(
            f"/api/knowledge-bases/{kb_id}/documents/{doc_id}/versions/upload",
            files={"file": ("cross.txt", b"cross", "text/plain")},
            headers=other_headers,
        ),
        client.get(f"/api/knowledge-bases/{kb_id}/reindex-jobs", headers=other_headers),
        client.post(
            f"/api/knowledge-bases/{kb_id}/reindex",
            json={
                "embedding_provider": "fake",
                "embedding_model": "fake",
                "embedding_dim": 32,
            },
            headers=other_headers,
        ),
        client.post(
            "/api/retrieve",
            json={"kb_id": kb_id, "query": "年假"},
            headers=other_headers,
        ),
        client.post(
            "/api/chat",
            json={"kb_id": kb_id, "question": "年假？", "stream": False},
            headers=other_headers,
        ),
        client.get(
            f"/api/conversations/{conversation_id}/messages", headers=other_headers
        ),
        client.delete(f"/api/conversations/{conversation_id}", headers=other_headers),
    ]
    assert all(response.status_code == 404 for response in attempts)

    audit_rows = client.get("/api/admin/audit-logs", headers=other_headers)
    assert audit_rows.status_code == 200
    assert all(row["tenant_id"] == other_tenant_id for row in audit_rows.json())
    assert all(row["resource_id"] not in {kb_id, doc_id, conversation_id} for row in audit_rows.json())

    users = client.get("/api/admin/users", headers=other_headers)
    assert users.status_code == 200
    assert all(row["email"] != ADMIN_EMAIL for row in users.json())
    roles = client.get("/api/admin/roles", headers=other_headers)
    assert roles.status_code == 200
    assert roles.json()
    assert all(row["tenant_id"] == other_tenant_id for row in roles.json())

    owner_check = client.get(f"/api/knowledge-bases/{kb_id}", headers=owner_headers)
    assert owner_check.status_code == 200


def test_role_and_user_management_enforces_tenant_scope(client):
    admin_headers = _auth_headers(client)
    suffix = uuid4().hex[:8]

    permissions = client.get("/api/admin/permissions", headers=admin_headers)
    assert permissions.status_code == 200
    permission_codes = {row["code"] for row in permissions.json()}
    assert {"kb:read", "kb:write", "admin:manage"}.issubset(permission_codes)

    role = client.post(
        "/api/admin/roles",
        json={
            "name": f"viewer-{suffix}",
            "description": "只读知识库",
            "permissions": ["kb:read"],
        },
        headers=admin_headers,
    )
    assert role.status_code == 201, role.text
    role_id = role.json()["id"]

    email = f"viewer-{suffix}@example.com"
    created = client.post(
        "/api/admin/users",
        json={
            "email": email,
            "password": "ViewerPass123!",
            "display_name": "Viewer",
            "role_ids": [role_id],
            "is_superuser": True,
        },
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    user = created.json()
    assert user["is_superuser"] is False
    assert user["role_ids"] == [role_id]
    user_id = user["id"]

    viewer_headers = _auth_headers(client, email, "ViewerPass123!")
    assert client.get("/api/knowledge-bases", headers=viewer_headers).status_code == 200
    denied = client.post(
        "/api/knowledge-bases",
        json={"name": f"denied-{suffix}"},
        headers=viewer_headers,
    )
    assert denied.status_code == 403

    assigned_delete = client.delete(f"/api/admin/roles/{role_id}", headers=admin_headers)
    assert assigned_delete.status_code == 409

    updated_role = client.patch(
        f"/api/admin/roles/{role_id}",
        json={"permissions": ["kb:read", "kb:write"]},
        headers=admin_headers,
    )
    assert updated_role.status_code == 200
    allowed = client.post(
        "/api/knowledge-bases",
        json={"name": f"allowed-{suffix}"},
        headers=viewer_headers,
    )
    assert allowed.status_code == 201, allowed.text

    other_tenant_id, other_email, other_password = _create_tenant_admin(superuser=True)
    del other_tenant_id
    other_headers = _auth_headers(client, other_email, other_password)
    cross_role = client.post(
        "/api/admin/users",
        json={
            "email": f"cross-{suffix}@example.com",
            "password": "CrossRole123!",
            "role_ids": [role_id],
        },
        headers=other_headers,
    )
    assert cross_role.status_code == 400
    assert client.patch(
        f"/api/admin/users/{user_id}",
        json={"display_name": "cross update"},
        headers=other_headers,
    ).status_code == 404
    assert client.patch(
        f"/api/admin/roles/{role_id}",
        json={"description": "cross update"},
        headers=other_headers,
    ).status_code == 404

    unassigned = client.patch(
        f"/api/admin/users/{user_id}",
        json={"role_ids": []},
        headers=admin_headers,
    )
    assert unassigned.status_code == 200
    assert unassigned.json()["permissions"] == []
    assert client.get("/api/knowledge-bases", headers=viewer_headers).status_code == 403

    deactivated = client.patch(
        f"/api/admin/users/{user_id}",
        json={"is_active": False},
        headers=admin_headers,
    )
    assert deactivated.status_code == 200
    assert client.get("/api/auth/me", headers=viewer_headers).status_code == 401

    assert client.delete(f"/api/admin/roles/{role_id}", headers=admin_headers).status_code == 204
    system_roles = client.get("/api/admin/roles", headers=admin_headers).json()
    admin_role_id = next(row["id"] for row in system_roles if row["name"] == ADMIN_ROLE)
    assert client.delete(
        f"/api/admin/roles/{admin_role_id}", headers=admin_headers
    ).status_code == 409
    self_user_id = _login(client, ADMIN_EMAIL, ADMIN_PASSWORD)["user"]["id"]
    assert client.patch(
        f"/api/admin/users/{self_user_id}",
        json={"is_active": False},
        headers=admin_headers,
    ).status_code == 409
