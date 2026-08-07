"""安全响应头与单机/Redis 多副本限流中间件。"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from hashlib import sha256
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .security import decode_access_token

logger = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

_REDIS_INCREMENT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


def apply_security_headers(response: Any) -> Any:
    for key, value in SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    return response


def _headers(scope: Scope) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


def _quota_subject(scope: Scope) -> str:
    headers = _headers(scope)
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        try:
            tenant_id = str(decode_access_token(token).get("tenant_id") or "")
            if tenant_id:
                return f"tenant:{tenant_id}"
        except ValueError:
            # 无效凭证由认证层拒绝；限流仍回退 IP，不能靠随机 Token 绕过配额。
            pass
    client = scope.get("client")
    client_ip = client[0] if client else "unknown"
    return f"ip:{sha256(client_ip.encode('utf-8')).hexdigest()[:24]}"


class MemoryRateLimiter:
    """单进程固定窗口计数器，仅用于本地开发与 Redis 的测试替身。"""

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, int], int] = defaultdict(int)

    async def increment(self, key: str, window: int, ttl_seconds: int) -> int:
        del ttl_seconds
        bucket = (key, window)
        if len(self._buckets) > 10_000:
            for old in list(self._buckets):
                if old[1] < window - 2:
                    self._buckets.pop(old, None)
        self._buckets[bucket] += 1
        return self._buckets[bucket]


class RedisRateLimiter:
    """使用 Lua 保证 INCR 与首次 EXPIRE 在多个应用副本间原子执行。"""

    def __init__(self, redis_url: str, client: Any | None = None) -> None:
        if client is None:
            from redis.asyncio import Redis

            client = Redis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
            )
        self.client = client

    async def increment(self, key: str, window: int, ttl_seconds: int) -> int:
        del window
        result = await self.client.eval(_REDIS_INCREMENT_SCRIPT, 1, key, ttl_seconds)
        return int(result)


class RateLimitMiddleware:
    """可在多副本间共享配额的固定窗口限流。"""

    _EXEMPT_PATHS = {"/api/health/live", "/api/health/ready", "/metrics"}

    def __init__(
        self,
        app: ASGIApp,
        requests_per_minute: int = 600,
        backend: str = "memory",
        redis_url: str = "redis://localhost:6379/0",
        redis_failure_mode: str = "fail_open",
        key_prefix: str = "enterprise-rag:ratelimit",
        redis_client: Any | None = None,
    ) -> None:
        self.app = app
        self.requests_per_minute = max(1, int(requests_per_minute))
        self.backend_name = backend
        self.redis_failure_mode = redis_failure_mode
        self.key_prefix = key_prefix.rstrip(":")
        if backend == "redis":
            self.limiter = RedisRateLimiter(redis_url, redis_client)
        elif backend == "memory":
            self.limiter = MemoryRateLimiter()
        else:
            raise ValueError(f"unsupported rate limit backend: {backend}")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        now = time.time()
        window = int(now // 60)
        retry_after = max(1, 60 - int(now % 60))
        subject = _quota_subject(scope)
        key = f"{self.key_prefix}:{subject}:{window}"
        try:
            current = await self.limiter.increment(key, window, retry_after + 1)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "rate_limit_backend_error backend=%s mode=%s error=%s",
                self.backend_name,
                self.redis_failure_mode,
                type(exc).__name__,
            )
            if self.backend_name == "redis" and self.redis_failure_mode == "fail_closed":
                response = JSONResponse(
                    status_code=503,
                    content={"detail": "请求限流服务暂不可用，请稍后重试"},
                    headers={"Retry-After": "5", "X-RateLimit-Backend": "redis"},
                )
                apply_security_headers(response)
                await response(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return

        if current > self.requests_per_minute:
            response = JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后再试"},
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(self.requests_per_minute),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Backend": self.backend_name,
                },
            )
            apply_security_headers(response)
            await response(scope, receive, send)
            return

        async def send_with_quota(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-ratelimit-limit", str(self.requests_per_minute).encode("ascii")),
                        (
                            b"x-ratelimit-remaining",
                            str(max(0, self.requests_per_minute - current)).encode("ascii"),
                        ),
                        (b"x-ratelimit-backend", self.backend_name.encode("ascii")),
                    ]
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_quota)


class InMemoryRateLimitMiddleware(RateLimitMiddleware):
    """向后兼容旧导入；新代码应直接使用 ``RateLimitMiddleware``。"""

    def __init__(self, app: ASGIApp, requests_per_minute: int = 600) -> None:
        super().__init__(app, requests_per_minute=requests_per_minute, backend="memory")
