"""请求关联、结构化日志与 Prometheus 指标。

日志只记录路由模板、状态和耗时，不记录请求体、完整 URL、Token、Prompt 或文档内容。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_PAIR_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|password|secret|token)\s*[=:]\s*[^\s,;]+"
)
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")

HTTP_REQUESTS_TOTAL = Counter(
    "enterprise_rag_http_requests_total",
    "HTTP requests completed by route template and status.",
    ("method", "route", "status"),
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "enterprise_rag_http_request_duration_seconds",
    "HTTP request duration by route template.",
    ("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1, 2, 5, 10, 30, 60),
)
HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "enterprise_rag_http_requests_in_progress",
    "HTTP requests currently executing.",
    ("method",),
)
RETRIEVAL_REQUESTS_TOTAL = Counter(
    "enterprise_rag_retrieval_requests_total",
    "Hybrid retrieval requests by final outcome.",
    ("outcome",),
)
RETRIEVAL_STAGE_DURATION_SECONDS = Histogram(
    "enterprise_rag_retrieval_stage_duration_seconds",
    "Hybrid retrieval stage duration.",
    ("stage", "status"),
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1, 2, 5),
)

_LOG_FIELDS = (
    "event",
    "request_id",
    "method",
    "route",
    "status_code",
    "duration_ms",
    "error_type",
    "backend",
    "mode",
    "component",
    "outcome",
)


def _redact(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = _BEARER_RE.sub("Bearer [REDACTED]", value)
    text = _SECRET_PAIR_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = _EMAIL_RE.sub("[EMAIL]", text)
    return text[:1024]


class JsonLogFormatter(logging.Formatter):
    """稳定的单行 JSON formatter；仅输出白名单字段。"""

    def format(self, record: logging.LogRecord) -> str:
        request_id = getattr(record, "request_id", None) or request_id_context.get()
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact(record.getMessage()),
            "request_id": _redact(request_id),
        }
        for field in _LOG_FIELDS:
            value = getattr(record, field, None)
            if value not in (None, ""):
                payload[field] = _redact(value)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    """统一应用与 Uvicorn 日志格式，并关闭重复 access log。"""

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for name in ("uvicorn", "uvicorn.error", "fastapi"):
        target = logging.getLogger(name)
        target.handlers = []
        target.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("httpx", "httpcore", "openai", "qdrant_client", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


def resolve_request_id(value: str | None) -> str:
    candidate = (value or "").strip()
    return candidate if _REQUEST_ID_RE.fullmatch(candidate) else uuid4().hex


def bind_request_id(request_id: str) -> Token[str]:
    return request_id_context.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    request_id_context.reset(token)


def route_template(scope: Mapping[str, Any]) -> str:
    route = scope.get("route")
    if route is None:
        return "unmatched"
    path = str(scope.get("path") or "unmatched")
    for name, value in dict(scope.get("path_params") or {}).items():
        path = re.sub(
            rf"(?<=/){re.escape(str(value))}(?=/|$)",
            "{" + str(name) + "}",
            path,
            count=1,
        )
    return path


def record_http_request(method: str, route: str, status_code: int, duration_seconds: float) -> None:
    labels = {"method": method, "route": route}
    HTTP_REQUESTS_TOTAL.labels(**labels, status=str(status_code)).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(**labels).observe(max(0.0, duration_seconds))


def observe_retrieval(diagnostics: Mapping[str, Any]) -> None:
    outcome = "degraded" if diagnostics.get("degraded") else "ok"
    RETRIEVAL_REQUESTS_TOTAL.labels(outcome=outcome).inc()
    for stage in ("vector", "bm25", "fusion", "rerank"):
        detail = diagnostics.get(stage)
        if not isinstance(detail, Mapping):
            continue
        elapsed_ms = detail.get("elapsed_ms")
        if isinstance(elapsed_ms, (int, float)):
            RETRIEVAL_STAGE_DURATION_SECONDS.labels(
                stage=stage,
                status=str(detail.get("status") or "unknown"),
            ).observe(max(0.0, float(elapsed_ms) / 1000.0))


def metrics_response() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
