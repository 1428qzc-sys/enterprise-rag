"""存活、就绪检查与不含敏感配置的运行时信息。"""

from __future__ import annotations

from time import perf_counter
from typing import Callable

import httpx
from fastapi import APIRouter, Response
from sqlalchemy import text

from .. import __version__
from ..config import settings
from ..core.embeddings import make_embeddings
from ..core.vector_store import get_vector_store
from ..database import engine

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
        "mode": (
            "mock"
            if settings.embedding_provider == "fake" or settings.llm_provider == "echo"
            else "model"
        ),
        "probes": {"live": "/api/health/live", "ready": "/api/health/ready"},
    }


@router.get("/health/live")
def live() -> dict:
    return {"status": "ok", "app": settings.app_name, "version": __version__}


def _timed_check(check: Callable[[], None]) -> dict:
    started = perf_counter()
    try:
        check()
        return {"status": "ok", "latency_ms": round((perf_counter() - started) * 1000, 3)}
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "latency_ms": round((perf_counter() - started) * 1000, 3),
            "error": type(exc).__name__,
        }


def _check_database() -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def _check_vector_store() -> None:
    if not get_vector_store().health():
        raise RuntimeError("vector store health returned false")


def _check_redis() -> None:
    from redis import Redis

    client = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.readiness_timeout_seconds,
        socket_timeout=settings.readiness_timeout_seconds,
    )
    try:
        if not client.ping():
            raise RuntimeError("redis ping returned false")
    finally:
        client.close()


def _check_openai_compatible(base_url: str, api_key: str) -> None:
    if not api_key:
        raise RuntimeError("provider key is not configured")
    if not settings.health_external_checks:
        return
    with httpx.Client(timeout=settings.readiness_timeout_seconds) as client:
        response = client.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()


def _check_ollama() -> None:
    if not settings.health_external_checks:
        return
    with httpx.Client(timeout=settings.readiness_timeout_seconds) as client:
        response = client.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags")
        response.raise_for_status()


def _check_embedding() -> None:
    if settings.embedding_provider == "fake":
        vector = make_embeddings().embed_query("readiness")
        if len(vector) != settings.embedding_dim:
            raise RuntimeError("fake embedding dimension mismatch")
    elif settings.embedding_provider == "ollama":
        _check_ollama()
    else:
        _check_openai_compatible(settings.embedding_base_url, settings.embedding_api_key)


def _check_llm() -> None:
    if settings.llm_provider == "echo":
        return
    if settings.llm_provider == "ollama":
        _check_ollama()
    else:
        _check_openai_compatible(settings.llm_base_url, settings.llm_api_key)


@router.get("/health/ready")
def ready(response: Response) -> dict:
    components = {
        "database": _timed_check(_check_database),
        "vector_store": _timed_check(_check_vector_store),
        "embedding": _timed_check(_check_embedding),
        "llm": _timed_check(_check_llm),
    }
    if settings.rate_limit_backend == "redis":
        components["redis"] = _timed_check(_check_redis)
    else:
        components["redis"] = {
            "status": "disabled",
            "backend": "memory",
            "required": False,
        }
    failed = [name for name, detail in components.items() if detail["status"] == "error"]
    status = "ready" if not failed else "not_ready"
    if failed:
        response.status_code = 503
    return {
        "status": status,
        "app": settings.app_name,
        "version": __version__,
        "environment": settings.environment,
        "mode": (
            "mock"
            if settings.embedding_provider == "fake" or settings.llm_provider == "echo"
            else "model"
        ),
        "components": components,
        "failed_components": failed,
    }
