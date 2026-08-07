"""FastAPI 应用入口。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from time import perf_counter

import anyio.to_thread
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from . import __version__
from .api import admin, auth, chat, documents, health, knowledge_bases
from .config import settings
from .database import init_db
from .observability import (
    HTTP_REQUESTS_IN_PROGRESS,
    REQUEST_ID_HEADER,
    bind_request_id,
    configure_logging,
    metrics_response,
    record_http_request,
    reset_request_id,
    resolve_request_id,
    route_template,
)
from .security_middleware import RateLimitMiddleware, apply_security_headers
from .services import bm25_index
from .services.ingestion import process_ingestion_job, recover_incomplete_jobs
from .services.reindexing import process_reindex_job, recover_reindex_jobs

configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_runtime()
    anyio.to_thread.current_default_thread_limiter().total_tokens = settings.worker_thread_tokens
    warmup_started = perf_counter()
    await asyncio.to_thread(bm25_index.warmup)
    logger.info(
        "retrieval_warmup_complete",
        extra={
            "event": "startup_warmup",
            "duration_ms": round((perf_counter() - warmup_started) * 1000, 3),
        },
    )
    init_db()
    recovery_tasks = [
        asyncio.create_task(asyncio.to_thread(process_ingestion_job, job_id))
        for job_id in recover_incomplete_jobs()
    ]
    recovery_tasks.extend(
        asyncio.create_task(asyncio.to_thread(process_reindex_job, job_id))
        for job_id in recover_reindex_jobs()
    )
    app.state.ingestion_recovery_tasks = recovery_tasks
    try:
        yield
    finally:
        if recovery_tasks:
            await asyncio.gather(*recovery_tasks, return_exceptions=True)


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description="企业知识库 RAG 问答系统：多格式接入、混合检索+重排、流式问答与引用溯源。",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[REQUEST_ID_HEADER],
)
app.add_middleware(
    RateLimitMiddleware,
    requests_per_minute=settings.rate_limit_requests_per_minute,
    backend=settings.rate_limit_backend,
    redis_url=settings.redis_url,
    redis_failure_mode=settings.rate_limit_redis_failure_mode,
    key_prefix=settings.rate_limit_key_prefix,
)


@app.middleware("http")
async def request_observability_middleware(request: Request, call_next):
    request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
    request.state.request_id = request_id
    context_token = bind_request_id(request_id)
    method = request.method.upper()
    started = perf_counter()
    HTTP_REQUESTS_IN_PROGRESS.labels(method=method).inc()
    try:
        try:
            response = await asyncio.wait_for(
                call_next(request),
                timeout=settings.request_timeout_seconds,
            )
        except asyncio.TimeoutError:
            response = JSONResponse(
                status_code=504,
                content={"detail": "请求处理超时", "request_id": request_id},
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "unhandled_request_error",
                extra={"event": "http_error", "error_type": type(exc).__name__},
            )
            response = JSONResponse(
                status_code=500,
                content={"detail": "服务内部错误", "request_id": request_id},
            )
        duration_seconds = perf_counter() - started
        route = route_template(request.scope)
        record_http_request(method, route, response.status_code, duration_seconds)
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "http_request",
            extra={
                "event": "http_request",
                "method": method,
                "route": route,
                "status_code": response.status_code,
                "duration_ms": round(duration_seconds * 1000, 3),
            },
        )
        return apply_security_headers(response)
    finally:
        HTTP_REQUESTS_IN_PROGRESS.labels(method=method).dec()
        reset_request_id(context_token)

for r in (health.router, auth.router, admin.router, knowledge_bases.router, documents.router, chat.router):
    app.include_router(r, prefix=settings.api_prefix)


@app.get("/metrics", tags=["system"], include_in_schema=False)
def metrics():
    return metrics_response()


@app.get("/", tags=["system"])
def root() -> dict:
    return {
        "name": settings.app_name,
        "version": __version__,
        "docs": "/docs",
        "health": f"{settings.api_prefix}/health",
    }
