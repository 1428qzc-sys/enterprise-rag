"""Enterprise RAG 三类持久数据的可重复备份/恢复演练。

演练对象：PostgreSQL 元数据、Qdrant collection snapshot、上传原文件。
恢复目标使用隔离数据库与临时容器，只暴露 19025/19026，结束后自动清理。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:19021")
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:19022")
    parser.add_argument("--restore-qdrant-port", type=int, default=19025)
    parser.add_argument("--restore-backend-port", type=int, default=19026)
    parser.add_argument(
        "--compose-project",
        default=os.getenv("COMPOSE_PROJECT_NAME", PROJECT_ROOT.name),
        help="源栈 Compose 项目名；默认使用仓库目录名",
    )
    parser.add_argument(
        "--compose-env-file",
        default=os.getenv("COMPOSE_ENV_FILE", ""),
        help="源栈使用的 Compose env 文件；标准 .env 启动时留空",
    )
    parser.add_argument("--postgres-container", default="")
    parser.add_argument("--backend-container", default="")
    parser.add_argument("--network", default="")
    parser.add_argument("--backend-image", default="")
    parser.add_argument("--qdrant-image", default="")
    parser.add_argument("--postgres-user", default="rag")
    parser.add_argument("--postgres-password", default="ragpwd-local-demo")
    parser.add_argument("--postgres-database", default="enterprise_rag")
    parser.add_argument("--email", default="admin@example.com")
    parser.add_argument("--password", default="ChangeMe123!")
    parser.add_argument("--tenant-slug", default="demo")
    parser.add_argument("--fixture", default="backend/scripts/eval_fixture/hr_policy.md")
    parser.add_argument("--report", default="artifacts/backup-restore-report.json")
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_command(
    command: list[str],
    *,
    stdin_path: Path | None = None,
    stdout_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    stdin_handle = stdin_path.open("rb") if stdin_path else None
    stdout_handle = stdout_path.open("wb") if stdout_path else subprocess.PIPE
    try:
        completed = subprocess.run(
            command,
            stdin=stdin_handle,
            stdout=stdout_handle,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
    finally:
        if stdin_handle:
            stdin_handle.close()
        if stdout_path and stdout_handle:
            stdout_handle.close()
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")[-4000:]
        raise RuntimeError(
            f"命令失败 exit={completed.returncode}: {command[0]} {command[1] if len(command) > 1 else ''}; "
            f"stderr={stderr!r}"
        )
    if stdout_path:
        return ""
    assert isinstance(completed.stdout, bytes)
    return completed.stdout.decode("utf-8", errors="replace").strip()


def _compose_command(args: argparse.Namespace) -> list[str]:
    command = ["docker", "compose", "--project-directory", str(PROJECT_ROOT)]
    if args.compose_env_file:
        command.extend(["--env-file", str(Path(args.compose_env_file).resolve())])
    command.extend(["-p", args.compose_project])
    return command


def _service_container(args: argparse.Namespace, service: str) -> str:
    container = run_command([*_compose_command(args), "ps", "-q", service])
    require(bool(container), f"Compose 服务未运行：{service}")
    return container


def _inspect_container(container: str) -> dict[str, Any]:
    payload = json.loads(run_command(["docker", "inspect", container]))
    require(isinstance(payload, list) and bool(payload), f"无法检查容器：{container}")
    return payload[0]


def resolve_source_stack(args: argparse.Namespace) -> None:
    """解析标准或自定义 Compose 项目的实际运行对象。"""
    args.postgres_container = args.postgres_container or _service_container(args, "postgres")
    args.backend_container = args.backend_container or _service_container(args, "backend")
    qdrant_container = _service_container(args, "qdrant")

    backend_info = _inspect_container(args.backend_container)
    qdrant_info = _inspect_container(qdrant_container)
    args.backend_image = args.backend_image or str(backend_info["Config"]["Image"])
    args.qdrant_image = args.qdrant_image or str(qdrant_info["Config"]["Image"])
    networks = backend_info.get("NetworkSettings", {}).get("Networks", {})
    require(bool(networks), "后端容器没有可用 Docker 网络")
    args.network = args.network or next(iter(networks))


def docker_exec(container: str, *arguments: str) -> str:
    return run_command(["docker", "exec", container, *arguments])


def wait_http(url: str, *, timeout: float, expected_status: int = 200) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last = "尚未发起请求"
    with httpx.Client(timeout=5.0, trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(url)
                last = f"status={response.status_code} body={response.text[:500]!r}"
                if response.status_code == expected_status:
                    try:
                        return response.json()
                    except json.JSONDecodeError:
                        return {"status_code": response.status_code, "body": response.text[:500]}
            except httpx.HTTPError as exc:
                last = type(exc).__name__
            time.sleep(0.5)
    raise TimeoutError(f"等待 {url} 超时：{last}")


def wait_document(
    client: httpx.Client,
    headers: dict[str, str],
    kb_id: str,
    document_id: str,
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/knowledge-bases/{kb_id}/documents/{document_id}", headers=headers
        )
        response.raise_for_status()
        last = response.json()
        if last["status"] == "failed":
            raise RuntimeError(f"夹具入库失败：{last.get('error') or 'unknown'}")
        if last["status"] == "done" and last["consistency_status"] == "consistent":
            return last
        time.sleep(0.5)
    raise TimeoutError(f"等待夹具入库超时：{last}")


def login(
    client: httpx.Client, tenant_slug: str, email: str, password: str
) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"tenant_slug": tenant_slug, "email": email, "password": password},
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def psql_scalar(container: str, user: str, database: str, sql: str) -> str:
    return docker_exec(
        container,
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        user,
        "-d",
        database,
        "-At",
        "-c",
        sql,
    )


def copy_uploads(container: str, target_data_dir: Path) -> None:
    target_data_dir.mkdir(parents=True, exist_ok=True)
    run_command(
        ["docker", "cp", f"{container}:/app/data/uploads", str(target_data_dir)]
    )
    require((target_data_dir / "uploads").is_dir(), "uploads 目录未复制到备份目录")


def create_upload_archive(data_dir: Path, archive_path: Path) -> None:
    with tarfile.open(archive_path, mode="w:gz") as archive:
        archive.add(data_dir, arcname="data", recursive=True)


def relative_upload_path(stored_path: str) -> PurePosixPath:
    """把数据库中的上传路径收敛为相对 `/app/data` 的安全路径。"""
    stored_posix_path = PurePosixPath(stored_path)
    require(".." not in stored_posix_path.parts, f"上传路径包含上级目录：{stored_path}")
    if stored_posix_path.is_absolute():
        try:
            relative_upload = stored_posix_path.relative_to("/app/data")
        except ValueError as exc:
            raise RuntimeError(f"上传绝对路径不在 /app/data：{stored_path}") from exc
    else:
        normalized_parts = tuple(
            part for part in stored_posix_path.parts if part not in {".", ""}
        )
        require(
            len(normalized_parts) >= 2 and normalized_parts[0] == "data",
            f"上传相对路径不在 data：{stored_path}",
        )
        relative_upload = PurePosixPath(*normalized_parts[1:])
    require(
        len(relative_upload.parts) >= 2 and relative_upload.parts[0] == "uploads",
        f"上传路径不在 uploads：{stored_path}",
    )
    return relative_upload


def safe_extract(archive_path: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, mode="r:gz") as archive:
        root = destination.resolve()
        for member in archive.getmembers():
            member_path = (destination / member.name).resolve()
            if root not in member_path.parents and member_path != root:
                raise RuntimeError(f"拒绝不安全的归档路径：{member.name}")
        if hasattr(tarfile, "data_filter"):
            archive.extractall(destination, filter="data")
        else:  # Python 3.11 兼容；上方已逐项完成路径穿越校验。
            archive.extractall(destination)
    restored = destination / "data"
    require(restored.is_dir(), "uploads 归档恢复后缺少 data 目录")
    return restored


def cleanup_container(name: str) -> None:
    subprocess.run(
        ["docker", "rm", "-f", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    for port in (args.restore_qdrant_port, args.restore_backend_port):
        require(19020 <= port <= 19029, f"恢复端口 {port} 超出 19020-19029")
    require(
        args.restore_qdrant_port != args.restore_backend_port,
        "恢复 Qdrant 与后端端口不能相同",
    )
    resolve_source_stack(args)

    suffix = uuid4().hex[:8]
    restore_database = f"enterprise_rag_restore_{suffix}"
    qdrant_container = f"enterprise-rag-restore-qdrant-{suffix}"
    backend_container = f"enterprise-rag-restore-backend-{suffix}"
    qdrant_alias = f"qdrant-restore-{suffix}"
    fixture_path = Path(args.fixture).resolve()
    report_path = Path(args.report).resolve()
    started = time.monotonic()
    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "ports": {
            "source_backend": args.base_url,
            "source_qdrant": args.qdrant_url,
            "restore_qdrant": args.restore_qdrant_port,
            "restore_backend": args.restore_backend_port,
        },
        "restore_database": restore_database,
        "source_stack": {
            "compose_project": args.compose_project,
            "backend_image": args.backend_image,
            "qdrant_image": args.qdrant_image,
        },
        "steps": {},
    }
    kb_id = ""
    snapshot_name = ""
    source_headers: dict[str, str] = {}
    restore_db_created = False

    try:
        with tempfile.TemporaryDirectory(prefix="enterprise-rag-backup-drill-") as temp_name:
            temp = Path(temp_name)
            pg_dump_path = temp / "postgres.dump"
            snapshot_path = temp / "collection.snapshot"
            upload_data_dir = temp / "upload-backup-data"
            upload_archive_path = temp / "uploads.tar.gz"
            restore_root = temp / "restored-files"

            source_timeout = httpx.Timeout(30.0, connect=10.0, read=90.0)
            with httpx.Client(
                base_url=args.base_url.rstrip("/"), timeout=source_timeout, trust_env=False
            ) as source:
                ready = source.get("/api/health/ready")
                ready.raise_for_status()
                require(ready.json()["status"] == "ready", "源栈 readiness 未通过")
                source_headers = login(
                    source, args.tenant_slug, args.email, args.password
                )

                create_kb = source.post(
                    "/api/knowledge-bases",
                    json={
                        "name": f"Backup Drill {suffix}",
                        "description": "备份恢复演练夹具；结束后自动删除",
                    },
                    headers=source_headers,
                )
                create_kb.raise_for_status()
                kb_id = create_kb.json()["id"]

                with fixture_path.open("rb") as fixture:
                    upload = source.post(
                        f"/api/knowledge-bases/{kb_id}/documents/upload",
                        files={"file": (fixture_path.name, fixture, "text/markdown")},
                        headers=source_headers,
                    )
                upload.raise_for_status()
                document_id = upload.json()["id"]
                document = wait_document(source, source_headers, kb_id, document_id)
                active_version_id = document["active_version_id"]

                kb_response = source.get(f"/api/knowledge-bases/{kb_id}", headers=source_headers)
                kb_response.raise_for_status()
                kb = kb_response.json()
                collection = kb["vector_collection"] or f"kb_{kb_id}"

                chunks_response = source.get(
                    f"/api/knowledge-bases/{kb_id}/documents/{document_id}/chunks",
                    params={"version_id": active_version_id},
                    headers=source_headers,
                )
                chunks_response.raise_for_status()
                chunks = chunks_response.json()
                require(chunks and all(chunk["is_active"] for chunk in chunks), "源 Chunk 未激活")
                active_chunk_ids = {chunk["id"] for chunk in chunks}

                retrieval = source.post(
                    "/api/retrieve",
                    json={
                        "kb_id": kb_id,
                        "query": "正式员工每年有多少天带薪年假？",
                        "top_k": 5,
                    },
                    headers=source_headers,
                )
                retrieval.raise_for_status()
                source_results = retrieval.json()["results"]
                require(source_results and "18 天" in source_results[0]["content"], "源检索夹具事实不正确")
                report["steps"]["seed"] = {
                    "kb_id": kb_id,
                    "document_id": document_id,
                    "active_version_id": active_version_id,
                    "collection": collection,
                    "active_chunks": len(chunks),
                    "source_retrieval": "verified",
                }

                with httpx.Client(
                    base_url=args.qdrant_url.rstrip("/"), timeout=90.0, trust_env=False
                ) as qdrant:
                    created = qdrant.post(f"/collections/{collection}/snapshots")
                    created.raise_for_status()
                    snapshot_name = created.json()["result"]["name"]
                    downloaded = qdrant.get(
                        f"/collections/{collection}/snapshots/{snapshot_name}"
                    )
                    downloaded.raise_for_status()
                    snapshot_path.write_bytes(downloaded.content)

                with pg_dump_path.open("wb") as dump_output:
                    completed = subprocess.run(
                        [
                            "docker",
                            "exec",
                            args.postgres_container,
                            "pg_dump",
                            "-U",
                            args.postgres_user,
                            "-d",
                            args.postgres_database,
                            "-Fc",
                            "--no-owner",
                            "--no-privileges",
                        ],
                        stdout=dump_output,
                        stderr=subprocess.PIPE,
                        check=False,
                    )
                if completed.returncode != 0:
                    raise RuntimeError(
                        "pg_dump 失败："
                        + completed.stderr.decode("utf-8", errors="replace")[-4000:]
                    )
                run_command(
                    ["docker", "exec", "-i", args.postgres_container, "pg_restore", "--list"],
                    stdin_path=pg_dump_path,
                )

                stored_path = psql_scalar(
                    args.postgres_container,
                    args.postgres_user,
                    args.postgres_database,
                    f"SELECT stored_path FROM document_version WHERE id = '{active_version_id}';",
                )
                relative_upload = relative_upload_path(stored_path)
                copy_uploads(args.backend_container, upload_data_dir)
                copied_upload = upload_data_dir.joinpath(*relative_upload.parts)
                require(copied_upload.is_file(), f"备份中缺少上传文件：{relative_upload}")
                require(
                    sha256_file(copied_upload) == document["content_hash"],
                    "备份上传文件 SHA-256 与数据库 content_hash 不一致",
                )
                create_upload_archive(upload_data_dir, upload_archive_path)

                report["steps"]["backup"] = {
                    "postgres": {
                        "format": "custom",
                        "bytes": pg_dump_path.stat().st_size,
                        "sha256": sha256_file(pg_dump_path),
                        "catalog_verified": True,
                    },
                    "qdrant": {
                        "snapshot_name": snapshot_name,
                        "bytes": snapshot_path.stat().st_size,
                        "sha256": sha256_file(snapshot_path),
                        "source_image": args.qdrant_image,
                    },
                    "uploads": {
                        "bytes": upload_archive_path.stat().st_size,
                        "sha256": sha256_file(upload_archive_path),
                        "document_sha256": document["content_hash"],
                    },
                }

            # PostgreSQL 逻辑恢复到同一实例中的隔离数据库。
            docker_exec(
                args.postgres_container,
                "createdb",
                "-U",
                args.postgres_user,
                restore_database,
            )
            restore_db_created = True
            run_command(
                [
                    "docker",
                    "exec",
                    "-i",
                    args.postgres_container,
                    "pg_restore",
                    "-U",
                    args.postgres_user,
                    "-d",
                    restore_database,
                    "--no-owner",
                    "--no-privileges",
                    "--exit-on-error",
                ],
                stdin_path=pg_dump_path,
            )
            restored_counts = {
                "knowledge_bases": int(
                    psql_scalar(
                        args.postgres_container,
                        args.postgres_user,
                        restore_database,
                        f"SELECT COUNT(*) FROM knowledge_base WHERE id = '{kb_id}';",
                    )
                ),
                "documents": int(
                    psql_scalar(
                        args.postgres_container,
                        args.postgres_user,
                        restore_database,
                        f"SELECT COUNT(*) FROM document WHERE id = '{document_id}' AND active_version_id = '{active_version_id}';",
                    )
                ),
                "active_chunks": int(
                    psql_scalar(
                        args.postgres_container,
                        args.postgres_user,
                        restore_database,
                        f"SELECT COUNT(*) FROM chunk WHERE document_id = '{document_id}' AND version_id = '{active_version_id}' AND is_active = true;",
                    )
                ),
            }
            require(restored_counts["knowledge_bases"] == 1, "恢复数据库缺少目标知识库")
            require(restored_counts["documents"] == 1, "恢复数据库缺少活动文档版本")
            require(
                restored_counts["active_chunks"] == len(active_chunk_ids),
                "恢复数据库活动 Chunk 数量不一致",
            )

            restored_data_dir = safe_extract(upload_archive_path, restore_root)
            restored_upload = restored_data_dir.joinpath(*relative_upload.parts)
            require(restored_upload.is_file(), "uploads 归档恢复后缺少原文件")
            require(
                sha256_file(restored_upload) == document["content_hash"],
                "恢复上传文件 SHA-256 不一致",
            )

            cleanup_container(qdrant_container)
            qdrant_run = run_command(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    qdrant_container,
                    "--network",
                    args.network,
                    "--network-alias",
                    qdrant_alias,
                    "-p",
                    f"127.0.0.1:{args.restore_qdrant_port}:6333",
                    args.qdrant_image,
                ]
            )
            require(bool(qdrant_run), "恢复 Qdrant 容器未返回 ID")
            restore_qdrant_url = f"http://127.0.0.1:{args.restore_qdrant_port}"
            wait_http(f"{restore_qdrant_url}/healthz", timeout=60.0)
            with httpx.Client(base_url=restore_qdrant_url, timeout=90.0, trust_env=False) as qdrant:
                with snapshot_path.open("rb") as snapshot_file:
                    uploaded = qdrant.post(
                        f"/collections/{collection}/snapshots/upload",
                        params={"priority": "snapshot"},
                        files={
                            "snapshot": (
                                snapshot_name,
                                snapshot_file,
                                "application/octet-stream",
                            )
                        },
                    )
                uploaded.raise_for_status()
                count_response = qdrant.post(
                    f"/collections/{collection}/points/count",
                    json={"exact": True},
                )
                count_response.raise_for_status()
                restored_vector_count = int(count_response.json()["result"]["count"])
                require(
                    restored_vector_count == len(active_chunk_ids),
                    "恢复 Qdrant 向量数量与活动 Chunk 不一致",
                )
                for chunk_id in active_chunk_ids:
                    point = qdrant.get(
                        f"/collections/{collection}/points/{chunk_id}"
                    )
                    point.raise_for_status()
                    require(
                        point.json()["result"]["payload"]["document_id"] == document_id,
                        "恢复 Qdrant payload 的 document_id 不一致",
                    )

            cleanup_container(backend_container)
            process_env = os.environ.copy()
            restored_database_url = (
                f"postgresql+psycopg://{args.postgres_user}:{args.postgres_password}"
                f"@postgres:5432/{restore_database}"
            )
            runtime_env = {
                "ENVIRONMENT": "development",
                "DATABASE_URL": restored_database_url,
                "DATABASE_AUTO_CREATE": "false",
                "VECTOR_BACKEND": "qdrant",
                "QDRANT_URL": f"http://{qdrant_alias}:6333",
                "RATE_LIMIT_BACKEND": "redis",
                "RATE_LIMIT_REDIS_FAILURE_MODE": "fail_closed",
                "REDIS_URL": "redis://redis:6379/1",
                "AUTH_SECRET_KEY": "local-demo-auth-secret-change-before-production",
                "BOOTSTRAP_ADMIN_EMAIL": args.email,
                "BOOTSTRAP_ADMIN_PASSWORD": args.password,
                "EMBEDDING_PROVIDER": "fake",
                "EMBEDDING_MODEL": "fake",
                "EMBEDDING_DIM": "256",
                "LLM_PROVIDER": "echo",
                "LLM_MODEL": "echo",
                "RERANK_ENABLED": "true",
                "RERANK_PROVIDER": "lexical",
                "CORS_ORIGINS": f"http://127.0.0.1:{args.restore_backend_port}",
            }
            process_env.update(runtime_env)
            docker_arguments = [
                "docker",
                "run",
                "-d",
                "--name",
                backend_container,
                "--network",
                args.network,
                "-p",
                f"127.0.0.1:{args.restore_backend_port}:8000",
                "-v",
                f"{restored_data_dir}:/app/data",
            ]
            for key in runtime_env:
                docker_arguments.extend(["-e", key])
            docker_arguments.append(args.backend_image)
            backend_run = run_command(docker_arguments, env=process_env)
            require(bool(backend_run), "恢复后端容器未返回 ID")

            restore_backend_url = f"http://127.0.0.1:{args.restore_backend_port}"
            restored_ready = wait_http(
                f"{restore_backend_url}/api/health/ready", timeout=120.0
            )
            require(restored_ready.get("status") == "ready", "恢复后端 readiness 未通过")
            with httpx.Client(
                base_url=restore_backend_url,
                timeout=httpx.Timeout(30.0, connect=10.0, read=90.0),
                trust_env=False,
            ) as restored:
                restored_headers = login(
                    restored, args.tenant_slug, args.email, args.password
                )
                kb_check = restored.get(
                    f"/api/knowledge-bases/{kb_id}", headers=restored_headers
                )
                kb_check.raise_for_status()
                document_check = restored.get(
                    f"/api/knowledge-bases/{kb_id}/documents/{document_id}",
                    headers=restored_headers,
                )
                document_check.raise_for_status()
                require(
                    document_check.json()["consistency_status"] == "consistent",
                    "恢复后文档一致性状态异常",
                )
                restored_chunks_response = restored.get(
                    f"/api/knowledge-bases/{kb_id}/documents/{document_id}/chunks",
                    params={"version_id": active_version_id},
                    headers=restored_headers,
                )
                restored_chunks_response.raise_for_status()
                restored_chunks = restored_chunks_response.json()
                require(
                    {item["id"] for item in restored_chunks} == active_chunk_ids,
                    "恢复 API 返回的 Chunk ID 与备份前不一致",
                )

                restored_retrieval = restored.post(
                    "/api/retrieve",
                    json={
                        "kb_id": kb_id,
                        "query": "正式员工每年有多少天带薪年假？",
                        "top_k": 5,
                    },
                    headers=restored_headers,
                )
                restored_retrieval.raise_for_status()
                results = restored_retrieval.json()["results"]
                require(results and results[0]["chunk_id"] in active_chunk_ids, "恢复检索未命中活动 Chunk")
                require("18 天" in results[0]["content"], "恢复检索事实不正确")

                restored_chat = restored.post(
                    "/api/chat",
                    json={
                        "kb_id": kb_id,
                        "question": "正式员工每年有多少天带薪年假？",
                        "request_id": uuid4().hex,
                        "stream": False,
                    },
                    headers=restored_headers,
                )
                restored_chat.raise_for_status()
                answer = restored_chat.json()
                require("18 天" in answer["answer"] and "[1]" in answer["answer"], "恢复问答事实或引用错误")
                require(answer["sources"], "恢复问答没有引用")
                require(answer["sources"][0]["chunk_id"] in active_chunk_ids, "恢复问答引用非活动 Chunk")

            report["steps"]["restore"] = {
                "postgres": {**restored_counts, "database": "isolated"},
                "qdrant": {
                    "image": args.qdrant_image,
                    "points": restored_vector_count,
                    "priority": "snapshot",
                },
                "uploads": {"sha256_verified": True},
                "backend": {
                    "readiness": "ready",
                    "login": 200,
                    "knowledge_base": 200,
                    "document": 200,
                    "retrieval_fact": "18 天",
                    "chat_citation": "verified",
                },
            }
            report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        if snapshot_name and report.get("steps", {}).get("seed", {}).get("collection"):
            try:
                collection = report["steps"]["seed"]["collection"]
                with httpx.Client(
                    base_url=args.qdrant_url.rstrip("/"), timeout=30.0, trust_env=False
                ) as qdrant:
                    qdrant.delete(
                        f"/collections/{collection}/snapshots/{snapshot_name}"
                    )
            except Exception:
                report.setdefault("cleanup_warnings", []).append("source_snapshot_cleanup_failed")
        cleanup_container(backend_container)
        cleanup_container(qdrant_container)
        if restore_db_created:
            try:
                docker_exec(
                    args.postgres_container,
                    "dropdb",
                    "-U",
                    args.postgres_user,
                    "--force",
                    restore_database,
                )
            except Exception:
                report.setdefault("cleanup_warnings", []).append("restore_database_cleanup_failed")
        if kb_id and source_headers:
            try:
                with httpx.Client(
                    base_url=args.base_url.rstrip("/"), timeout=30.0, trust_env=False
                ) as source:
                    source.delete(f"/api/knowledge-bases/{kb_id}", headers=source_headers)
            except Exception:
                report.setdefault("cleanup_warnings", []).append("source_fixture_cleanup_failed")
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
