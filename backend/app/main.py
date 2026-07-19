"""FastAPI 应用入口。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import anyio.to_thread
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from . import __version__
from .api import admin, auth, chat, documents, health, knowledge_bases
from .config import settings
from .database import init_db
from .security_middleware import InMemoryRateLimitMiddleware, apply_security_headers


@asynccontextmanager
async def lifespan(app: FastAPI):
    anyio.to_thread.current_default_thread_limiter().total_tokens = settings.worker_thread_tokens
    init_db()
    yield


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
)
app.add_middleware(
    InMemoryRateLimitMiddleware,
    requests_per_minute=settings.rate_limit_requests_per_minute,
)


@app.middleware("http")
async def security_baseline_middleware(request, call_next):
    try:
        response = await asyncio.wait_for(
            call_next(request),
            timeout=settings.request_timeout_seconds,
        )
    except asyncio.TimeoutError:
        response = JSONResponse(status_code=504, content={"detail": "请求处理超时"})
    return apply_security_headers(response)

for r in (health.router, auth.router, admin.router, knowledge_bases.router, documents.router, chat.router):
    app.include_router(r, prefix=settings.api_prefix)


@app.get("/", tags=["system"])
def root() -> dict:
    return {
        "name": settings.app_name,
        "version": __version__,
        "docs": "/docs",
        "health": f"{settings.api_prefix}/health",
    }
