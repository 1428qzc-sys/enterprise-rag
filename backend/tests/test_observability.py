"""Request ID、结构化日志、健康探针与指标回归。"""

from __future__ import annotations

import json
import logging

import pytest


def test_request_id_is_validated_and_propagated(client):
    supplied = client.get("/api/health", headers={"X-Request-ID": "release-check:123"})
    assert supplied.status_code == 200
    assert supplied.headers["X-Request-ID"] == "release-check:123"

    generated = client.get("/api/health", headers={"X-Request-ID": "x" * 200})
    assert generated.status_code == 200
    assert generated.headers["X-Request-ID"] != "x" * 200
    assert len(generated.headers["X-Request-ID"]) == 32


def test_metrics_export_http_and_retrieval_signals(client, db_session, seeded_kb):
    from app.services.retrieval import retrieve_with_diagnostics

    kb, _, _ = seeded_kb
    result = retrieve_with_diagnostics(db_session, kb, "年假有多少天", top_k=2)
    assert result.chunks
    assert client.get("/api/health").status_code == 200

    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "enterprise_rag_http_requests_total" in response.text
    assert 'route="/api/health"' in response.text
    assert "enterprise_rag_retrieval_requests_total" in response.text
    assert 'stage="vector"' in response.text
    assert 'stage="bm25"' in response.text
    assert 'stage="fusion"' in response.text
    assert 'stage="rerank"' in response.text


def test_readiness_checks_real_local_dependencies(client):
    response = client.get("/api/health/ready")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["mode"] == "mock"
    assert payload["failed_components"] == []
    assert payload["components"]["database"]["status"] == "ok"
    assert payload["components"]["vector_store"]["status"] == "ok"
    assert payload["components"]["embedding"]["status"] == "ok"
    assert payload["components"]["llm"]["status"] == "ok"
    assert payload["components"]["redis"] == {
        "status": "disabled",
        "backend": "memory",
        "required": False,
    }


def test_readiness_is_not_false_green_when_vector_store_fails(client, monkeypatch):
    from app.api import health as health_api

    class BrokenVectorStore:
        def health(self):
            raise ConnectionError("do not expose backend URL or credentials")

    monkeypatch.setattr(health_api, "get_vector_store", lambda: BrokenVectorStore())
    response = client.get("/api/health/ready")
    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["failed_components"] == ["vector_store"]
    assert payload["components"]["vector_store"]["error"] == "ConnectionError"
    assert "credentials" not in response.text


def test_json_log_formatter_redacts_credentials_and_pii():
    from app.observability import JsonLogFormatter

    record = logging.LogRecord(
        name="release-test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=(
            "authorization=Bearer abc.def password=Secret123 "
            "api_key=provider-key admin@example.com"
        ),
        args=(),
        exc_info=None,
    )
    record.request_id = "req-safe"
    payload = json.loads(JsonLogFormatter().format(record))
    encoded = json.dumps(payload)
    assert payload["request_id"] == "req-safe"
    assert "abc.def" not in encoded
    assert "Secret123" not in encoded
    assert "provider-key" not in encoded
    assert "admin@example.com" not in encoded
    assert "REDACTED" in encoded


def test_production_runtime_rejects_demo_security_settings():
    from app.config import Settings

    unsafe = Settings(_env_file=None, environment="production")
    with pytest.raises(RuntimeError, match="生产配置校验失败"):
        unsafe.validate_runtime()

    safe = Settings(
        _env_file=None,
        environment="production",
        auth_secret_key="a-unique-production-secret-with-40-characters",
        bootstrap_admin_password="A-unique-admin-password-2026!",
        cors_origins="https://rag.example.test",
        database_auto_create=False,
        database_url="postgresql+psycopg://rag:unique-password@postgres/enterprise_rag",
        vector_backend="qdrant",
        rate_limit_backend="redis",
        rate_limit_redis_failure_mode="fail_closed",
        embedding_provider="ollama",
        llm_provider="ollama",
    )
    safe.validate_runtime()


def test_production_runtime_rejects_mock_and_single_process_backends():
    from app.config import Settings

    unsafe = Settings(
        _env_file=None,
        environment="production",
        auth_secret_key="a-unique-production-secret-with-40-characters",
        bootstrap_admin_password="A-unique-admin-password-2026!",
        cors_origins="https://rag.example.test",
        database_auto_create=False,
        database_url="postgresql+psycopg://rag:unique-password@postgres/enterprise_rag",
        vector_backend="memory",
        rate_limit_backend="memory",
        rate_limit_redis_failure_mode="fail_open",
        embedding_provider="fake",
        llm_provider="echo",
    )
    with pytest.raises(RuntimeError) as caught:
        unsafe.validate_runtime()

    message = str(caught.value)
    assert "VECTOR_BACKEND" in message
    assert "Redis fail_closed" in message
    assert "fake Embedding" in message
    assert "echo LLM" in message
