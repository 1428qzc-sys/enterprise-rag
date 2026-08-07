"""端到端 API 测试（离线：fake embedding + echo LLM + memory 向量库）。

覆盖：建库 → 上传文档 → 异步入库完成 → 检索预览 → RAG 问答（含引用）→ 会话/消息。
"""

DOC_TEXT = (
    "员工福利手册。\n"
    "公司为全体正式员工提供每年 15 天带薪年假，年假可在自然年度内灵活安排。\n"
    "差旅报销流程：员工需在 OA 系统提交发票与出差申请，由直属主管审批后财务打款。\n"
    "试用期为 3 个月，试用期员工享有法定节假日与病假。\n"
)


ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "ChangeMe123!"


def _login(client, email=ADMIN_EMAIL, password=ADMIN_PASSWORD):
    payload = {"email": email, "password": password}
    if email == ADMIN_EMAIL:
        payload["tenant_slug"] = "demo"
    resp = client.post("/api/auth/login", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth_headers(client, email=ADMIN_EMAIL, password=ADMIN_PASSWORD):
    token = _login(client, email, password)["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create_kb(client, name="测试知识库"):
    resp = client.post(
        "/api/knowledge-bases",
        json={"name": name, "description": "pytest"},
        headers=_auth_headers(client),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _upload_text(client, kb_id, filename="handbook.txt", text=DOC_TEXT):
    resp = client.post(
        f"/api/knowledge-bases/{kb_id}/documents/upload",
        files={"file": (filename, text.encode("utf-8"), "text/plain")},
        headers=_auth_headers(client),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _wait_done(client, kb_id, doc_id, tries=20):
    status = None
    for _ in range(tries):
        r = client.get(f"/api/knowledge-bases/{kb_id}/documents/{doc_id}", headers=_auth_headers(client))
        assert r.status_code == 200
        data = r.json()
        status = data["status"]
        if status in ("done", "failed"):
            return data
    return {"status": status}


def test_auth_login_and_me(client):
    payload = _login(client)
    assert payload["token_type"] == "bearer"
    assert payload["access_token"]
    assert payload["user"]["email"] == ADMIN_EMAIL
    assert "admin:manage" in payload["user"]["permissions"]

    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {payload['access_token']}"})
    assert r.status_code == 200
    assert r.json()["tenant"]["slug"] == "demo"


def test_unauthenticated_business_requests_are_rejected(client):
    r = client.get("/api/knowledge-bases")
    assert r.status_code == 401
    r = client.post("/api/retrieve", json={"kb_id": "missing", "query": "test"})
    assert r.status_code == 401


def test_kb_crud(client):
    kb_id = _create_kb(client, "库A")
    headers = _auth_headers(client)
    # 列表
    r = client.get("/api/knowledge-bases", headers=headers)
    assert r.status_code == 200
    assert any(kb["id"] == kb_id for kb in r.json())
    # 详情
    r = client.get(f"/api/knowledge-bases/{kb_id}", headers=headers)
    assert r.status_code == 200
    # 更新
    r = client.patch(f"/api/knowledge-bases/{kb_id}", json={"description": "updated"}, headers=headers)
    assert r.status_code == 200 and r.json()["description"] == "updated"
    # 删除
    r = client.delete(f"/api/knowledge-bases/{kb_id}", headers=headers)
    assert r.status_code == 204
    r = client.get(f"/api/knowledge-bases/{kb_id}", headers=headers)
    assert r.status_code == 404


def test_ingest_and_query_flow(client):
    kb_id = _create_kb(client, "库B")
    headers = _auth_headers(client)
    doc_id = _upload_text(client, kb_id)

    doc = _wait_done(client, kb_id, doc_id)
    assert doc["status"] == "done", doc
    assert doc["chunk_count"] >= 1

    # 检索预览
    r = client.post("/api/retrieve", json={"kb_id": kb_id, "query": "年假有多少天"}, headers=headers)
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) >= 1
    assert any("年假" in item["content"] for item in results)
    assert results[0]["document_name"] == "handbook.txt"

    # 非流式问答
    r = client.post(
        "/api/chat",
        json={"kb_id": kb_id, "question": "年假有多少天？", "stream": False},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["answer"]
    assert len(payload["sources"]) >= 1
    conv_id = payload["conversation_id"]

    # 会话与消息
    r = client.get(f"/api/knowledge-bases/{kb_id}/conversations", headers=headers)
    assert r.status_code == 200 and any(c["id"] == conv_id for c in r.json())

    r = client.get(f"/api/conversations/{conv_id}/messages", headers=headers)
    assert r.status_code == 200
    msgs = r.json()
    roles = [m["role"] for m in msgs]
    assert "user" in roles and "assistant" in roles
    assistant = [m for m in msgs if m["role"] == "assistant"][0]
    assert len(assistant["sources"]) >= 1


def test_upload_rejects_unsupported_type(client):
    kb_id = _create_kb(client, "库C")
    resp = client.post(
        f"/api/knowledge-bases/{kb_id}/documents/upload",
        files={"file": ("bad.exe", b"MZ...", "application/octet-stream")},
        headers=_auth_headers(client),
    )
    assert resp.status_code == 400


def test_chat_stream_sse(client):
    kb_id = _create_kb(client, "库D")
    doc_id = _upload_text(client, kb_id)
    _wait_done(client, kb_id, doc_id)
    headers = _auth_headers(client)

    with client.stream(
        "POST",
        "/api/chat",
        json={"kb_id": kb_id, "question": "报销流程是怎样的？", "stream": True},
        headers=headers,
    ) as resp:
        assert resp.status_code == 200
        body = "".join(chunk for chunk in resp.iter_text())
    assert "event: sources" in body
    assert "event: token" in body
    assert "event: done" in body


def test_chat_request_id_replays_without_duplicate_messages(client):
    from uuid import uuid4

    kb_id = _create_kb(client, "幂等问答库")
    doc_id = _upload_text(client, kb_id)
    _wait_done(client, kb_id, doc_id)
    headers = _auth_headers(client)
    request_id = uuid4().hex
    body = {
        "kb_id": kb_id,
        "question": "年假有多少天？",
        "request_id": request_id,
        "stream": False,
    }

    first = client.post("/api/chat", json=body, headers=headers)
    second = client.post("/api/chat", json=body, headers=headers)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["conversation_id"] == first.json()["conversation_id"]
    assert second.json()["answer"] == first.json()["answer"]
    assert second.json()["diagnostics"] == {"cached": True}

    messages = client.get(
        f"/api/conversations/{first.json()['conversation_id']}/messages",
        headers=headers,
    ).json()
    turn_messages = [message for message in messages if message["request_id"] == request_id]
    assert [message["role"] for message in turn_messages] == ["user", "assistant"]

    with client.stream(
        "POST",
        "/api/chat",
        json={**body, "conversation_id": first.json()["conversation_id"], "stream": True},
        headers=headers,
    ) as response:
        replay = "".join(response.iter_text())
    assert response.status_code == 200
    assert '"cached": true' in replay
    assert "event: done" in replay

    exported = client.get(
        f"/api/conversations/{first.json()['conversation_id']}/export.md",
        headers=headers,
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/markdown")
    assert exported.headers["content-disposition"].endswith('.md"')
    assert "## 用户" in exported.text
    assert "## 助手" in exported.text
    assert "### 引用" in exported.text
    assert "handbook.txt" in exported.text
    assert "年假" in exported.text


def test_cross_tenant_kb_is_not_visible(client):
    from sqlmodel import Session

    from app.database import engine
    from app.models import Tenant, User, UserRole
    from app.security import _ensure_role, hash_password

    headers = _auth_headers(client)
    kb_id = _create_kb(client, "默认租户知识库")

    other_email = "tenant2@example.com"
    other_password = "Tenant2Pass!"
    with Session(engine) as session:
        tenant = Tenant(name="Tenant Two", slug="tenant-two")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        user = User(
            tenant_id=tenant.id,
            email=other_email,
            display_name="Tenant Two User",
            password_hash=hash_password(other_password),
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        role = _ensure_role(
            session,
            tenant.id,
            "member",
            ["kb:read", "kb:write", "doc:read", "doc:write", "chat:use", "conversation:delete"],
        )
        session.add(UserRole(user_id=user.id, role_id=role.id))
        session.commit()

    other_headers = _auth_headers(client, other_email, other_password)
    r = client.get(f"/api/knowledge-bases/{kb_id}", headers=other_headers)
    assert r.status_code == 404
    r = client.post("/api/retrieve", json={"kb_id": kb_id, "query": "年假"}, headers=other_headers)
    assert r.status_code == 404
    r = client.get("/api/knowledge-bases", headers=other_headers)
    assert r.status_code == 200
    assert all(kb["id"] != kb_id for kb in r.json())

    # 原租户仍可访问，证明不是对象被删除或损坏。
    r = client.get(f"/api/knowledge-bases/{kb_id}", headers=headers)
    assert r.status_code == 200
