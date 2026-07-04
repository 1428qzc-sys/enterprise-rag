"""健康检查与运行时信息。"""

from __future__ import annotations

from fastapi import APIRouter

from .. import __version__
from ..config import settings

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": __version__,
        "environment": settings.environment,
        "vector_backend": settings.vector_backend,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "rerank_enabled": settings.rerank_enabled,
    }
