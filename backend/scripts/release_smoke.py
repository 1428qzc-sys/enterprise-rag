"""可重复发布 smoke：真实 HTTP 长链路并在结束时清理数据。

覆盖 readiness、登录、建库、v1 入库、混合检索、SSE、真实引用、Markdown
导出、v2 更新、主动对账、旧向量不可检索、删除和指标。脚本失败时非零退出，
且不会打印访问令牌。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


FIXTURE_DIR = Path(__file__).resolve().parent / "eval_fixture"
RELEASE_FIXTURE_DIR = Path(__file__).resolve().parent / "release_fixture"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "http://127.0.0.1:19021"))
    parser.add_argument(
        "--frontend-url", default=os.getenv("FRONTEND_URL", "http://127.0.0.1:19020")
    )
    parser.add_argument("--email", default=os.getenv("ADMIN_EMAIL", "admin@example.com"))
    parser.add_argument("--password", default=os.getenv("ADMIN_PASSWORD", "ChangeMe123!"))
    parser.add_argument(
        "--tenant-slug", default=os.getenv("BOOTSTRAP_TENANT_SLUG", "demo")
    )
    parser.add_argument(
        "--v1", default=str(FIXTURE_DIR / "hr_policy.md")
    )
    parser.add_argument(
        "--v2",
        default=str(RELEASE_FIXTURE_DIR / "hr_policy_v2.md"),
    )
    parser.add_argument("--report", default="")
    parser.add_argument("--keep", action="store_true", help="保留 smoke 知识库供人工检查")
    return parser.parse_args()


def require(condition: Any, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def parse_sse(response: httpx.Response) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    event_name = "message"
    data_lines: list[str] = []
    event_id = ""

    def flush() -> None:
        nonlocal event_name, data_lines, event_id
        if data_lines:
            raw = "\n".join(data_lines)
            try:
                data: Any = json.loads(raw)
            except json.JSONDecodeError:
                data = raw
            events.append({"event": event_name, "id": event_id, "data": data})
        event_name = "message"
        data_lines = []
        event_id = ""

    for line in response.iter_lines():
        if not line:
            flush()
        elif line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("id:"):
            event_id = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
    flush()
    return events


def wait_document(
    client: httpx.Client,
    headers: dict[str, str],
    kb_id: str,
    document_id: str,
    expected_version: int,
    timeout: float = 60.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/knowledge-bases/{kb_id}/documents/{document_id}", headers=headers
        )
        if response.is_error:
            raise RuntimeError(
                "轮询文档失败："
                f"status={response.status_code}, "
                f"server={response.headers.get('server', '-')}, "
                f"request_id={response.headers.get('x-request-id', '-')}, "
                f"body={response.text[:500]!r}"
            )
        last = response.json()
        if last.get("status") == "failed":
            raise RuntimeError(f"文档入库失败：{last.get('error') or 'unknown'}")
        if last.get("status") == "done" and int(last.get("version") or 0) >= expected_version:
            return last
        time.sleep(0.5)
    raise TimeoutError(f"等待文档 v{expected_version} 就绪超时，最后状态：{last}")


def wait_job(
    client: httpx.Client,
    headers: dict[str, str],
    kb_id: str,
    document_id: str,
    job_id: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/knowledge-bases/{kb_id}/documents/{document_id}/jobs",
            headers=headers,
        )
        response.raise_for_status()
        match = next((job for job in response.json() if job["id"] == job_id), None)
        if match:
            last = match
            if match["status"] == "done":
                return match
            if match["status"] in {"failed", "canceled"}:
                raise RuntimeError(f"对账任务未成功：{match}")
        time.sleep(0.25)
    raise TimeoutError(f"等待任务完成超时，最后状态：{last}")


def stream_question(
    client: httpx.Client,
    headers: dict[str, str],
    kb_id: str,
    question: str,
) -> dict[str, Any]:
    request_id = uuid4().hex
    with client.stream(
        "POST",
        "/api/chat",
        json={
            "kb_id": kb_id,
            "question": question,
            "request_id": request_id,
            "stream": True,
        },
        headers=headers,
    ) as response:
        response.raise_for_status()
        events = parse_sse(response)
    names = [event["event"] for event in events]
    require("meta" in names, "SSE 缺少 meta 事件")
    require("sources" in names, "SSE 缺少 sources 事件")
    require("done" in names, "SSE 缺少 done 事件")
    require("error" not in names, f"SSE 返回错误事件：{events}")
    done = next(event["data"] for event in events if event["event"] == "done")
    require(done.get("request_id") == request_id, "SSE request_id 未保持一致")
    return {"done": done, "events": names, "request_id": request_id}


def source_matches_chunks(source: dict[str, Any], chunks: list[dict[str, Any]]) -> bool:
    return any(
        chunk["id"] == source["chunk_id"]
        and chunk["document_id"] == source["document_id"]
        and chunk["content"] == source["content"]
        and chunk.get("page") == source.get("page")
        for chunk in chunks
    )


def main() -> None:
    args = parse_args()
    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "frontend_url": args.frontend_url,
        "steps": {},
    }
    kb_id = ""
    deleted = False
    started = time.monotonic()
    timeout = httpx.Timeout(30.0, connect=10.0, read=90.0)
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=timeout) as client:
        try:
            ready = client.get("/api/health/ready")
            ready.raise_for_status()
            ready_payload = ready.json()
            require(ready_payload["status"] == "ready", f"readiness 未通过：{ready_payload}")
            require(ready_payload["mode"] == "mock", "发布 smoke 必须在明确的零密钥 Mock 模式运行")
            require(not ready_payload["failed_components"], "readiness 存在失败组件")
            report["steps"]["readiness"] = ready_payload["components"]

            if args.frontend_url:
                frontend = httpx.get(args.frontend_url, timeout=10.0)
                frontend.raise_for_status()
                require("Enterprise RAG" in frontend.text, "前端入口未返回应用页面")
                report["steps"]["frontend"] = frontend.status_code

            correlation_id = f"release-smoke:{uuid4().hex}"
            correlated = client.get("/api/health", headers={"X-Request-ID": correlation_id})
            correlated.raise_for_status()
            require(
                correlated.headers.get("X-Request-ID") == correlation_id,
                "X-Request-ID 未在响应中回传",
            )

            login = client.post(
                "/api/auth/login",
                json={
                    "tenant_slug": args.tenant_slug,
                    "email": args.email,
                    "password": args.password,
                },
            )
            login.raise_for_status()
            token = login.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            report["steps"]["login"] = login.status_code

            kb = client.post(
                "/api/knowledge-bases",
                json={
                    "name": f"Release Smoke {uuid4().hex[:8]}",
                    "description": "自动发布长链路；结束后删除",
                },
                headers=headers,
            )
            kb.raise_for_status()
            kb_id = kb.json()["id"]
            report["kb_id"] = kb_id

            v1_path = Path(args.v1).resolve()
            with v1_path.open("rb") as source:
                upload = client.post(
                    f"/api/knowledge-bases/{kb_id}/documents/upload",
                    files={"file": (v1_path.name, source, "text/markdown")},
                    headers=headers,
                )
            upload.raise_for_status()
            document_id = upload.json()["id"]
            document_v1 = wait_document(client, headers, kb_id, document_id, 1)
            require(document_v1["chunk_count"] > 0, "v1 未生成 Chunk")

            chunks_v1_response = client.get(
                f"/api/knowledge-bases/{kb_id}/documents/{document_id}/chunks",
                params={"version_id": document_v1["active_version_id"]},
                headers=headers,
            )
            chunks_v1_response.raise_for_status()
            chunks_v1 = chunks_v1_response.json()

            retrieve_v1 = client.post(
                "/api/retrieve",
                json={"kb_id": kb_id, "query": "正式员工每年有多少天带薪年假？", "top_k": 5},
                headers=headers,
            )
            retrieve_v1.raise_for_status()
            retrieval_v1 = retrieve_v1.json()
            require(retrieval_v1["results"], "v1 检索无结果")
            require(
                retrieval_v1["diagnostics"]["rerank"]["provider"] == "lexical",
                f"未执行 lexical 重排：{retrieval_v1['diagnostics']}",
            )

            chat_v1 = stream_question(
                client, headers, kb_id, "正式员工每年有多少天带薪年假？"
            )
            done_v1 = chat_v1["done"]
            require(done_v1["sources"], "v1 问答没有引用")
            require("[1]" in done_v1["answer"], "v1 回答缺少引用编号")
            require("18 天" in done_v1["answer"], "v1 回答未使用固定夹具事实")
            require(source_matches_chunks(done_v1["sources"][0], chunks_v1), "v1 引用不对应原文")

            exported = client.get(
                f"/api/conversations/{done_v1['conversation_id']}/export.md", headers=headers
            )
            exported.raise_for_status()
            require("### 引用" in exported.text and v1_path.name in exported.text, "Markdown 导出缺少引用")

            v2_path = Path(args.v2).resolve()
            with v2_path.open("rb") as source:
                version_upload = client.post(
                    f"/api/knowledge-bases/{kb_id}/documents/{document_id}/versions/upload",
                    files={"file": (v2_path.name, source, "text/markdown")},
                    headers=headers,
                )
            version_upload.raise_for_status()
            document_v2 = wait_document(client, headers, kb_id, document_id, 2)
            require(document_v2["consistency_status"] == "consistent", "v2 数据未收敛一致")

            versions_response = client.get(
                f"/api/knowledge-bases/{kb_id}/documents/{document_id}/versions",
                headers=headers,
            )
            versions_response.raise_for_status()
            versions = versions_response.json()
            require([version["version_number"] for version in versions] == [2, 1], "版本历史不完整")
            require(sum(bool(version["is_active"]) for version in versions) == 1, "活动版本数量不为 1")

            chunks_v2_response = client.get(
                f"/api/knowledge-bases/{kb_id}/documents/{document_id}/chunks",
                params={"version_id": document_v2["active_version_id"]},
                headers=headers,
            )
            chunks_v2_response.raise_for_status()
            chunks_v2 = chunks_v2_response.json()
            require(chunks_v2 and all(chunk["is_active"] for chunk in chunks_v2), "v2 Chunk 未激活")

            reconcile = client.post(
                f"/api/knowledge-bases/{kb_id}/documents/{document_id}/reconcile",
                headers=headers,
            )
            reconcile.raise_for_status()
            reconcile_done = wait_job(
                client, headers, kb_id, document_id, reconcile.json()["id"]
            )

            retrieve_v2 = client.post(
                "/api/retrieve",
                json={"kb_id": kb_id, "query": "正式员工现在每年有多少天带薪年假？", "top_k": 5},
                headers=headers,
            )
            retrieve_v2.raise_for_status()
            retrieval_v2 = retrieve_v2.json()
            require(retrieval_v2["results"], "v2 检索无结果")
            active_chunk_ids = {chunk["id"] for chunk in chunks_v2}
            old_chunk_ids = {chunk["id"] for chunk in chunks_v1}
            returned_ids = {item["chunk_id"] for item in retrieval_v2["results"]}
            require(returned_ids <= active_chunk_ids, "v2 检索返回非活动 Chunk")
            require(not returned_ids.intersection(old_chunk_ids), "v2 检索仍返回旧版本 Chunk")

            chat_v2 = stream_question(
                client, headers, kb_id, "正式员工现在每年有多少天带薪年假？"
            )
            done_v2 = chat_v2["done"]
            require(done_v2["sources"], "v2 问答没有引用")
            require("20 天" in done_v2["answer"] and "18 天" not in done_v2["answer"], "v2 回答事实错误")
            require(source_matches_chunks(done_v2["sources"][0], chunks_v2), "v2 引用不对应活动原文")

            delete_document = client.delete(
                f"/api/knowledge-bases/{kb_id}/documents/{document_id}", headers=headers
            )
            require(delete_document.status_code == 204, f"删除文档失败：{delete_document.text}")
            empty_retrieval = client.post(
                "/api/retrieve",
                json={"kb_id": kb_id, "query": "带薪年假", "top_k": 5},
                headers=headers,
            )
            empty_retrieval.raise_for_status()
            require(empty_retrieval.json()["results"] == [], "删除文档后仍能检索到结果")

            delete_kb = client.delete(f"/api/knowledge-bases/{kb_id}", headers=headers)
            require(delete_kb.status_code == 204, f"删除知识库失败：{delete_kb.text}")
            deleted = True
            missing = client.get(f"/api/knowledge-bases/{kb_id}", headers=headers)
            require(missing.status_code == 404, "删除知识库后仍可读取")

            metrics = client.get("/metrics")
            metrics.raise_for_status()
            require("enterprise_rag_http_requests_total" in metrics.text, "缺少 HTTP 指标")
            require("enterprise_rag_retrieval_stage_duration_seconds" in metrics.text, "缺少检索指标")

            report["steps"].update(
                {
                    "v1": {
                        "version": document_v1["version"],
                        "chunks": len(chunks_v1),
                        "answer_has_18_days": "18 天" in done_v1["answer"],
                        "citation_verified": True,
                    },
                    "v2": {
                        "version": document_v2["version"],
                        "chunks": len(chunks_v2),
                        "answer_has_20_days": "20 天" in done_v2["answer"],
                        "old_chunks_returned": False,
                        "citation_verified": True,
                    },
                    "reconcile": {
                        "status": reconcile_done["status"],
                        "progress": reconcile_done["progress"],
                    },
                    "delete": {"document": 204, "knowledge_base": 204, "post_delete": 404},
                    "metrics": True,
                }
            )
            report["status"] = "passed"
        finally:
            if kb_id and not deleted and not args.keep:
                try:
                    if "headers" in locals():
                        client.delete(f"/api/knowledge-bases/{kb_id}", headers=headers)
                except Exception:
                    pass

    report["duration_seconds"] = round(time.monotonic() - started, 3)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
