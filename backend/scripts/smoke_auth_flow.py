"""生产化 smoke：登录 → 建库 → 上传 → 检索 → 问答 → 后台创建用户。

示例：
    python backend/scripts/smoke_auth_flow.py --base-url http://127.0.0.1:18086

脚本只输出状态摘要，不打印 access token。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--email", default=os.getenv("ADMIN_EMAIL", "admin@example.com"))
    parser.add_argument("--password", default=os.getenv("ADMIN_PASSWORD", "ChangeMe123!"))
    parser.add_argument(
        "--sample",
        default=str(Path(__file__).resolve().parents[2] / "sample-docs" / "员工手册.md"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stamp = str(int(time.time()))
    with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
        login = client.post("/api/auth/login", json={"email": args.email, "password": args.password})
        login.raise_for_status()
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        kb = client.post(
            "/api/knowledge-bases",
            json={"name": f"Prod Smoke {stamp}", "description": "auth tenant smoke"},
            headers=headers,
        )
        kb.raise_for_status()
        kb_id = kb.json()["id"]

        sample_path = Path(args.sample)
        with sample_path.open("rb") as fh:
            doc = client.post(
                f"/api/knowledge-bases/{kb_id}/documents/upload",
                files={"file": (sample_path.name, fh, "text/markdown")},
                headers=headers,
            )
        doc.raise_for_status()
        doc_id = doc.json()["id"]

        doc_status = ""
        chunk_count = 0
        for _ in range(30):
            current = client.get(f"/api/knowledge-bases/{kb_id}/documents/{doc_id}", headers=headers)
            current.raise_for_status()
            payload = current.json()
            doc_status = payload["status"]
            chunk_count = payload.get("chunk_count", 0)
            if doc_status in ("done", "failed"):
                break
            time.sleep(1)
        if doc_status != "done":
            raise RuntimeError(f"document status is {doc_status}")

        retrieve = client.post(
            "/api/retrieve",
            json={"kb_id": kb_id, "query": "年假有多少天"},
            headers=headers,
        )
        retrieve.raise_for_status()

        chat = client.post(
            "/api/chat",
            json={"kb_id": kb_id, "question": "年假有多少天？", "stream": False},
            headers=headers,
        )
        chat.raise_for_status()

        created_user = client.post(
            "/api/admin/users",
            json={
                "email": f"smoke{stamp}@example.com",
                "password": "UserPass123!",
                "display_name": "Smoke User",
            },
            headers=headers,
        )
        created_user.raise_for_status()

        print(
            json.dumps(
                {
                    "login": login.status_code,
                    "kb": kb.status_code,
                    "doc_status": doc_status,
                    "chunk_count": chunk_count,
                    "retrieve_count": len(retrieve.json()["results"]),
                    "chat_sources": len(chat.json()["sources"]),
                    "admin_user": created_user.status_code,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
