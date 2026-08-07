"""memory/Redis 限流契约与故障策略。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import User
from app.security import create_access_token
from app.security_middleware import RateLimitMiddleware


class SharedFakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def eval(self, _script: str, _keys: int, key: str, _ttl: int) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


class BrokenRedis:
    async def eval(self, *_args):
        raise ConnectionError("redis unavailable")


def _app(**middleware_options) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, **middleware_options)

    @app.get("/limited")
    def limited() -> dict:
        return {"ok": True}

    return app


def _tenant_token(tenant_id: str) -> str:
    user = User(
        id=f"user-{tenant_id}",
        tenant_id=tenant_id,
        email=f"{tenant_id}@example.com",
        password_hash="unused",
    )
    return create_access_token(user)


def test_memory_rate_limit_returns_quota_and_security_headers():
    with TestClient(_app(requests_per_minute=1, backend="memory")) as client:
        first = client.get("/limited")
        second = client.get("/limited")

    assert first.status_code == 200
    assert first.headers["X-RateLimit-Backend"] == "memory"
    assert first.headers["X-RateLimit-Remaining"] == "0"
    assert second.status_code == 429
    assert second.headers["Retry-After"]
    assert second.headers["X-Content-Type-Options"] == "nosniff"


def test_redis_counter_is_shared_across_application_replicas():
    redis = SharedFakeRedis()
    options = {
        "requests_per_minute": 1,
        "backend": "redis",
        "redis_client": redis,
        "redis_failure_mode": "fail_closed",
    }
    with TestClient(_app(**options)) as replica_a, TestClient(_app(**options)) as replica_b:
        assert replica_a.get("/limited").status_code == 200
        blocked = replica_b.get("/limited")
    assert blocked.status_code == 429
    assert blocked.headers["X-RateLimit-Backend"] == "redis"


def test_redis_failure_modes_are_explicit():
    with TestClient(
        _app(
            requests_per_minute=1,
            backend="redis",
            redis_client=BrokenRedis(),
            redis_failure_mode="fail_open",
        )
    ) as fail_open:
        assert fail_open.get("/limited").status_code == 200

    with TestClient(
        _app(
            requests_per_minute=1,
            backend="redis",
            redis_client=BrokenRedis(),
            redis_failure_mode="fail_closed",
        )
    ) as fail_closed:
        response = fail_closed.get("/limited")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_valid_tokens_get_tenant_scoped_quotas():
    redis = SharedFakeRedis()
    with TestClient(
        _app(requests_per_minute=1, backend="redis", redis_client=redis)
    ) as client:
        tenant_a = {"Authorization": f"Bearer {_tenant_token('tenant-a')}"}
        tenant_b = {"Authorization": f"Bearer {_tenant_token('tenant-b')}"}
        assert client.get("/limited", headers=tenant_a).status_code == 200
        assert client.get("/limited", headers=tenant_b).status_code == 200
        assert client.get("/limited", headers=tenant_a).status_code == 429


def test_random_invalid_tokens_cannot_reset_ip_quota():
    redis = SharedFakeRedis()
    with TestClient(
        _app(requests_per_minute=1, backend="redis", redis_client=redis)
    ) as client:
        first = client.get("/limited", headers={"Authorization": "Bearer invalid-one"})
        second = client.get("/limited", headers={"Authorization": "Bearer invalid-two"})
    assert first.status_code == 200
    assert second.status_code == 429
