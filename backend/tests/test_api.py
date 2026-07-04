"""端到端 API 测试（离线：fake embedding + echo LLM + memory 向量库）。

覆盖：建库 → 上传文档 → 异步入库完成 → 检索预览 → RAG 问答（含引用）→ 会话/消息。
"""

DOC_TEXT = (
    "员工福利手册。\n"
    "公司为全体正式员工提供每年 15 天带薪年假，年假可在自然年度内灵活安排。\n"
    "差旅报销流程：员工需在 OA 系统提交发票与出差申请，由直属主管审批后财务打款。\n"
    "试用期为 3 个月，试用期员工享有法定节假日与病假。\n"
)


def _create_kb(client, name="测试知识库"):
    resp = client.post("/api/knowledge-bases", json={"name": name, "description": "pytest"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _upload_text(client, kb_id, filename="handbook.txt", text=DOC_TEXT):
    resp = client.post(
        f"/api/knowledge-bases/{kb_id}/documents/upload",
        files={"file": (filename, text.encode("utf-8"), "text/plain")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _wait_done(client, kb_id, doc_id, tries=20):
    status = None
    for _ in range(tries):
        r = client.get(f"/api/knowledge-bases/{kb_id}/documents/{doc_id}")
        assert r.status_code == 200
        data = r.json()
        status = data["status"]
        if status in ("done", "failed"):
            return data
    return {"status": status}


def test_kb_crud(client):
    kb_id = _create_kb(client, "库A")
    # 列表
    r = client.get("/api/knowledge-bases")
    assert r.status_code == 200
    assert any(kb["id"] == kb_id for kb in r.json())
    # 详情
    r = client.get(f"/api/knowledge-bases/{kb_id}")
    assert r.status_code == 200
    # 更新
    r = client.patch(f"/api/knowledge-bases/{kb_id}", json={"description": "updated"})
    assert r.status_code == 200 and r.json()["description"] == "updated"
    # 删除
    r = client.delete(f"/api/knowledge-bases/{kb_id}")
    assert r.status_code == 204
    r = client.get(f"/api/knowledge-bases/{kb_id}")
    assert r.status_code == 404


def test_ingest_and_query_flow(client):
    kb_id = _create_kb(client, "库B")
    doc_id = _upload_text(client, kb_id)

    doc = _wait_done(client, kb_id, doc_id)
    assert doc["status"] == "done", doc
    assert doc["chunk_count"] >= 1

    # 检索预览
    r = client.post("/api/retrieve", json={"kb_id": kb_id, "query": "年假有多少天"})
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) >= 1
    assert any("年假" in item["content"] for item in results)
    assert results[0]["document_name"] == "handbook.txt"

    # 非流式问答
    r = client.post(
        "/api/chat", json={"kb_id": kb_id, "question": "年假有多少天？", "stream": False}
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["answer"]
    assert len(payload["sources"]) >= 1
    conv_id = payload["conversation_id"]

    # 会话与消息
    r = client.get(f"/api/knowledge-bases/{kb_id}/conversations")
    assert r.status_code == 200 and any(c["id"] == conv_id for c in r.json())

    r = client.get(f"/api/conversations/{conv_id}/messages")
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
    )
    assert resp.status_code == 400


def test_chat_stream_sse(client):
    kb_id = _create_kb(client, "库D")
    doc_id = _upload_text(client, kb_id)
    _wait_done(client, kb_id, doc_id)

    with client.stream(
        "POST", "/api/chat", json={"kb_id": kb_id, "question": "报销流程是怎样的？", "stream": True}
    ) as resp:
        assert resp.status_code == 200
        body = "".join(chunk for chunk in resp.iter_text())
    assert "event: sources" in body
    assert "event: token" in body
    assert "event: done" in body
