"""FastAPI 应用入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api import admin, auth, chat, documents, health, knowledge_bases
from .config import settings
from .database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
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
