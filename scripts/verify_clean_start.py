"""Verify a no-cache, empty-volume Compose start and core release flow."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWED_PORTS = range(19020, 19030)
ARTIFACTS_DIR = REPO_ROOT / "artifacts"
FIXED_EVAL_REPORT = ARTIFACTS_DIR / "fixed-eval-report.json"
LIGHTHOUSE_REPORT = ARTIFACTS_DIR / "lighthouse.json"
SCREENSHOTS_DIR = REPO_ROOT / "docs" / "screenshots"
SCREENSHOT_NAMES = (
    "chat-1440x900.png",
    "chat-768x1024.png",
    "chat-375x812.png",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enterprise RAG clean-start verifier")
    parser.add_argument("--project", default=f"enterprise-rag-clean-{uuid4().hex[:8]}")
    parser.add_argument("--frontend-port", type=int, default=19020)
    parser.add_argument("--backend-port", type=int, default=19021)
    parser.add_argument("--qdrant-port", type=int, default=19022)
    parser.add_argument("--postgres-port", type=int, default=19023)
    parser.add_argument("--redis-port", type=int, default=19024)
    parser.add_argument("--max-build-seconds", type=float, default=1200.0)
    parser.add_argument("--max-core-seconds", type=float, default=600.0)
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / "artifacts" / "clean-start-report.json",
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_ports(ports: dict[str, int]) -> None:
    values = list(ports.values())
    if len(set(values)) != len(values):
        raise ValueError("clean-start 端口必须互不相同")
    invalid = [port for port in values if port not in ALLOWED_PORTS]
    if invalid:
        raise ValueError(f"端口超出 19020-19029：{invalid}")
    for name, port in ports.items():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError(f"{name} 端口 {port} 已被占用") from exc


def require_executable(*names: str) -> str:
    for name in names:
        executable = shutil.which(name)
        if executable:
            return executable
    raise RuntimeError(f"缺少必需命令：{' / '.join(names)}")


def compose_environment(ports: dict[str, int]) -> dict[str, str]:
    env = os.environ.copy()
    env_file = REPO_ROOT / ".env.example"
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        env.pop(line.split("=", 1)[0].strip(), None)
    for key in [key for key in env if key.startswith("COMPOSE_")]:
        env.pop(key, None)
    env.update(
        {
            "FRONTEND_HOST_PORT": str(ports["frontend"]),
            "BACKEND_HOST_PORT": str(ports["backend"]),
            "QDRANT_HOST_PORT": str(ports["qdrant"]),
            "POSTGRES_HOST_PORT": str(ports["postgres"]),
            "REDIS_HOST_PORT": str(ports["redis"]),
        }
    )
    return env


def run_step(
    report: dict[str, Any],
    name: str,
    command: list[str],
    *,
    env: dict[str, str],
    capture: bool = False,
    cwd: Path = REPO_ROOT,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    started = time.monotonic()
    print(f"[clean-start] {name}", flush=True)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or "").strip() if capture else ""
        report["steps"][name] = {
            "status": "failed",
            "duration_seconds": round(time.monotonic() - started, 3),
            "return_code": exc.returncode,
            "command": command,
            "cwd": str(cwd),
            **({"output": output} if output else {}),
        }
        if output:
            print(output, flush=True)
        raise
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "").strip() if isinstance(exc.stdout, str) else ""
        report["steps"][name] = {
            "status": "failed",
            "duration_seconds": round(time.monotonic() - started, 3),
            "timeout_seconds": timeout,
            "command": command,
            "cwd": str(cwd),
            **({"output": output} if output else {}),
        }
        if output:
            print(output, flush=True)
        raise
    duration = round(time.monotonic() - started, 3)
    item: dict[str, Any] = {
        "status": "passed",
        "duration_seconds": duration,
        "command": command,
        "cwd": str(cwd),
    }
    if capture:
        item["output"] = (completed.stdout or "").strip()
        if completed.stdout:
            print(completed.stdout.rstrip(), flush=True)
    report["steps"][name] = item
    return completed


def inspect_project(project: str, kind: str) -> list[str]:
    if kind == "containers":
        command = [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.Names}}",
        ]
    elif kind == "volumes":
        command = [
            "docker",
            "volume",
            "ls",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.Name}}",
        ]
    elif kind == "networks":
        command = [
            "docker",
            "network",
            "ls",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.Name}}",
        ]
    else:
        raise ValueError(f"未知 Docker 资源类型：{kind}")
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return [line for line in completed.stdout.splitlines() if line.strip()]


def write_report(path: Path, report: dict[str, Any]) -> None:
    path = path.resolve()
    if REPO_ROOT not in path.parents:
        raise ValueError("报告必须写入 enterprise-rag 仓库内")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    ports = {
        "frontend": args.frontend_port,
        "backend": args.backend_port,
        "qdrant": args.qdrant_port,
        "postgres": args.postgres_port,
        "redis": args.redis_port,
    }
    report: dict[str, Any] = {
        "status": "running",
        "started_at": utc_now(),
        "project": args.project,
        "build_no_cache": True,
        "empty_project_volumes": True,
        "ports": ports,
        "max_build_seconds": args.max_build_seconds,
        "max_core_flow_seconds": args.max_core_seconds,
        "steps": {},
    }
    env = compose_environment(ports)
    compose = [
        "docker",
        "compose",
        "--env-file",
        ".env.example",
        "-p",
        args.project,
    ]
    started = time.monotonic()
    error = ""
    exit_code = 0
    try:
        require_ports(ports)
        require_executable("docker")
        npm = require_executable("npm.cmd", "npm")
        require_executable("npx.cmd", "npx")
        existing_resources = {
            kind: inspect_project(args.project, kind)
            for kind in ("containers", "volumes", "networks")
        }
        if any(existing_resources.values()):
            raise RuntimeError(f"Compose 项目 {args.project} 已存在，不能证明空环境")

        run_step(
            report,
            "build_no_cache",
            [*compose, "build", "--no-cache"],
            env=env,
            timeout=args.max_build_seconds,
        )
        core_started = time.monotonic()
        run_step(
            report,
            "start_and_wait",
            [*compose, "up", "-d", "--wait"],
            env=env,
            timeout=300,
        )
        run_step(
            report,
            "migration_current",
            [*compose, "exec", "-T", "backend", "alembic", "current"],
            env=env,
            capture=True,
            timeout=60,
        )
        run_step(
            report,
            "release_smoke",
            [
                *compose,
                "exec",
                "-T",
                "backend",
                "python",
                "scripts/release_smoke.py",
                "--base-url",
                "http://127.0.0.1:8000",
                "--frontend-url",
                "http://frontend",
            ],
            env=env,
            capture=True,
            timeout=240,
        )
        core_seconds = round(time.monotonic() - core_started, 3)
        report["core_flow_seconds"] = core_seconds
        report["core_flow_under_budget"] = core_seconds <= args.max_core_seconds
        report["cold_checkout_flow_seconds"] = round(time.monotonic() - started, 3)
        if not report["core_flow_under_budget"]:
            raise RuntimeError(
                f"空卷启动与核心流程耗时 {core_seconds}s，"
                f"超过 {args.max_core_seconds}s"
            )

        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        run_step(
            report,
            "fixed_evaluation",
            [
                *compose,
                "exec",
                "-T",
                "backend",
                "python",
                "scripts/evaluate.py",
                "--api",
                "http://127.0.0.1:8000",
                "--bootstrap",
                "--provision-cross-tenant",
                "--report",
                "/app/data/fixed-eval-report.json",
            ],
            env=env,
            capture=True,
            timeout=360,
        )
        run_step(
            report,
            "copy_fixed_eval_report",
            [
                *compose,
                "cp",
                "backend:/app/data/fixed-eval-report.json",
                str(FIXED_EVAL_REPORT.relative_to(REPO_ROOT)),
            ],
            env=env,
            capture=True,
            timeout=60,
        )
        fixed_evaluation = json.loads(FIXED_EVAL_REPORT.read_text(encoding="utf-8"))
        if not fixed_evaluation.get("passed"):
            raise RuntimeError("固定评估报告未通过全部门槛")
        report["fixed_evaluation"] = {
            "report": str(FIXED_EVAL_REPORT.relative_to(REPO_ROOT)),
            "metrics": fixed_evaluation.get("metrics", {}),
            "gates": fixed_evaluation.get("gates", {}),
        }

        browser_env = env.copy()
        browser_env.update(
            {
                "E2E_BASE_URL": f"http://127.0.0.1:{args.frontend_port}",
                "E2E_CAPTURE_DIR": str(SCREENSHOTS_DIR),
                "LIGHTHOUSE_URL": f"http://127.0.0.1:{args.frontend_port}",
                "LIGHTHOUSE_REPORT": str(LIGHTHOUSE_REPORT),
            }
        )
        screenshots_started_at = time.time()
        run_step(
            report,
            "playwright_e2e",
            [npm, "run", "e2e"],
            env=browser_env,
            capture=True,
            cwd=REPO_ROOT / "frontend",
            timeout=240,
        )
        screenshot_evidence = []
        for name in SCREENSHOT_NAMES:
            path = SCREENSHOTS_DIR / name
            if not path.is_file() or path.stat().st_size == 0:
                raise RuntimeError(f"Playwright 截图缺失或为空：{path}")
            if path.stat().st_mtime < screenshots_started_at - 1:
                raise RuntimeError(f"Playwright 截图不是本轮生成：{path}")
            screenshot_evidence.append(
                {
                    "path": str(path.relative_to(REPO_ROOT)),
                    "bytes": path.stat().st_size,
                }
            )
        report["screenshots"] = screenshot_evidence

        run_step(
            report,
            "lighthouse",
            [npm, "run", "lighthouse"],
            env=browser_env,
            capture=True,
            cwd=REPO_ROOT / "frontend",
            timeout=180,
        )
        lighthouse = json.loads(LIGHTHOUSE_REPORT.read_text(encoding="utf-8"))
        report["lighthouse_scores"] = {
            name: round(float(lighthouse["categories"][name]["score"]), 4)
            for name in ("performance", "accessibility", "best-practices")
        }
        run_step(
            report,
            "compose_ps",
            [*compose, "ps", "--format", "json"],
            env=env,
            capture=True,
            timeout=60,
        )
        report["quality_flow_seconds"] = round(time.monotonic() - started, 3)
        report["status"] = "passed"
    except (
        OSError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        error = str(exc)
        report["status"] = "failed"
        report["error"] = error
        exit_code = 1
        try:
            run_step(
                report,
                "failure_logs",
                [*compose, "logs", "--no-color", "--tail=300"],
                env=env,
                capture=True,
                timeout=60,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as log_exc:
            report["failure_logs_error"] = str(log_exc)
    finally:
        cleanup_started = time.monotonic()
        cleanup = subprocess.run(
            [*compose, "down", "-v", "--remove-orphans", "--rmi", "local"],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
        )
        leftovers = {
            "containers": inspect_project(args.project, "containers"),
            "volumes": inspect_project(args.project, "volumes"),
            "networks": inspect_project(args.project, "networks"),
        }
        report["cleanup"] = {
            "status": (
                "passed"
                if cleanup.returncode == 0
                and not leftovers["containers"]
                and not leftovers["volumes"]
                and not leftovers["networks"]
                else "failed"
            ),
            "duration_seconds": round(time.monotonic() - cleanup_started, 3),
            "return_code": cleanup.returncode,
            "leftovers": leftovers,
        }
        if report["cleanup"]["status"] != "passed":
            report["status"] = "failed"
            report["error"] = report.get("error") or "clean-start 资源清理不完整"
            exit_code = 1
        report["finished_at"] = utc_now()
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        write_report(args.report, report)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if error:
        print(error, file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
