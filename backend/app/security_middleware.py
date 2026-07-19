"""FastAPI 安全中间件：响应头、超时和基础限流。"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def apply_security_headers(response: Any) -> Any:
    for key, value in SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    return response


class InMemoryRateLimitMiddleware:
    """单进程固定窗口限流。

    生产多副本部署时应切换到网关或 Redis 限流；这里提供应用层兜底，防止单机
    演示环境被暴力登录或压测流量直接打穿。
    """

    def __init__(self, app: ASGIApp, requests_per_minute: int = 600) -> None:
        self.app = app
        self.requests_per_minute = max(1, int(requests_per_minute))
        self._buckets: dict[tuple[str, int], int] = defaultdict(int)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_ip = client[0] if client else "unknown"
        now_minute = int(time.time() // 60)
        key = (client_ip, now_minute)

        # 顺手清理旧窗口，避免长进程内存无限增长。
        if len(self._buckets) > 10_000:
            for bucket_key in list(self._buckets):
                if bucket_key[1] < now_minute - 2:
                    self._buckets.pop(bucket_key, None)

        self._buckets[key] += 1
        if self._buckets[key] > self.requests_per_minute:
            response = JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后再试"},
                headers={"Retry-After": "60"},
            )
            apply_security_headers(response)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
