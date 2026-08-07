"""生产安全基线回归：SSRF、审计日志与安全响应头。"""

from __future__ import annotations

import socket

import pytest

from app.security_network import UnsafeUrlError, validate_public_http_url

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "ChangeMe123!"


def _login(client, email=ADMIN_EMAIL, password=ADMIN_PASSWORD):
    resp = client.post(
        "/api/auth/login",
        json={"tenant_slug": "demo", "email": email, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth_headers(client):
    token = _login(client)["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create_kb(client, name="安全测试知识库"):
    resp = client.post(
        "/api/knowledge-bases",
        json={"name": name, "description": "security pytest"},
        headers=_auth_headers(client),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "http://172.16.0.10/",
        "http://192.168.1.10/",
        "http://[::1]/",
    ],
)
def test_validate_public_http_url_blocks_private_targets(url):
    with pytest.raises(UnsafeUrlError):
        validate_public_http_url(url)


def test_validate_public_http_url_allows_public_https(monkeypatch):
    def fake_getaddrinfo(host, *_args, **_kwargs):
        assert host == "example.com"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert validate_public_http_url("https://example.com/docs") == "https://example.com/docs"


def test_url_ingest_blocks_ssrf_and_records_audit(client):
    kb_id = _create_kb(client)
    headers = _auth_headers(client)
    resp = client.post(
        f"/api/knowledge-bases/{kb_id}/documents/url",
        json={"url": "http://127.0.0.1/admin"},
        headers=headers,
    )
    assert resp.status_code == 400

    logs = client.get("/api/admin/audit-logs", headers=headers)
    assert logs.status_code == 200, logs.text
    assert any(
        row["action"] == "doc.ingest_url" and row["outcome"] == "denied"
        for row in logs.json()
    )


def test_security_headers_are_returned(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
    assert "camera=()" in resp.headers["Permissions-Policy"]


@pytest.mark.parametrize(
    "filename,content,mime",
    [
        ("fake.pdf", b"not a pdf", "application/pdf"),
        ("fake.docx", b"not a zip", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("fake.xlsx", b"PK" + bytes([3, 4]) + b"broken", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("binary.txt", b"text" + bytes([0]) + b"binary", "text/plain"),
    ],
)
def test_upload_rejects_extension_content_mismatch(client, filename, content, mime):
    kb_id = _create_kb(client, f"文件校验-{filename}")
    response = client.post(
        f"/api/knowledge-bases/{kb_id}/documents/upload",
        files={"file": (filename, content, mime)},
        headers=_auth_headers(client),
    )
    assert response.status_code == 400


def test_upload_size_limit_is_enforced_while_streaming(client, monkeypatch):
    from app.api import documents as documents_api

    kb_id = _create_kb(client, "上传大小限制")
    staging_dir = documents_api.Path(documents_api.settings.upload_dir, ".staging")
    before = set(staging_dir.glob("*")) if staging_dir.exists() else set()
    monkeypatch.setattr(documents_api.settings, "max_upload_mb", 0)
    response = client.post(
        f"/api/knowledge-bases/{kb_id}/documents/upload",
        files={"file": ("large.txt", b"x", "text/plain")},
        headers=_auth_headers(client),
    )
    assert response.status_code == 413
    after = set(staging_dir.glob("*")) if staging_dir.exists() else set()
    assert after == before
