"""外部 URL 抓取 SSRF 防护。

原则：任何用户输入的 URL 在请求前、以及每一次重定向后，都必须解析并确认
目标地址不是本机、内网、链路本地、保留地址或云厂商元数据地址。
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urljoin, urlparse

import httpx


class UnsafeUrlError(ValueError):
    """URL 不允许被服务端抓取。"""


@dataclass(frozen=True)
class FetchedUrl:
    url: str
    content: bytes
    content_type: str
    encoding: str

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding or "utf-8", errors="replace")


BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "metadata.azure.internal",
}

BLOCKED_IPS = {
    ipaddress.ip_address("169.254.169.254"),  # AWS/GCP/Azure 元数据常见地址
    ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud 元数据常见地址
}


def _normalized_host(host: str) -> str:
    return host.strip().strip("[]").rstrip(".").lower()


def _is_safe_public_ip(ip: ipaddress._BaseAddress) -> bool:
    if ip in BLOCKED_IPS:
        return False
    return bool(ip.is_global)


def _iter_resolved_ips(host: str) -> Iterable[ipaddress._BaseAddress]:
    try:
        yield ipaddress.ip_address(host)
        return
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrlError("URL 主机无法解析") from exc

    seen: set[str] = set()
    for info in infos:
        raw_ip = info[4][0]
        if raw_ip in seen:
            continue
        seen.add(raw_ip)
        yield ipaddress.ip_address(raw_ip)


def validate_public_http_url(url: str) -> str:
    """验证 URL 只能指向公网 HTTP/HTTPS 地址。"""

    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeUrlError("URL 需以 http:// 或 https:// 开头")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("URL 不允许包含用户名或密码")
    if not parsed.hostname:
        raise UnsafeUrlError("URL 缺少主机名")

    host = _normalized_host(parsed.hostname)
    if host in BLOCKED_HOSTS or host.endswith(".localhost"):
        raise UnsafeUrlError("URL 指向本机或元数据主机，已拒绝")

    try:
        # 触发 urllib 对非法端口的校验。
        _ = parsed.port
    except ValueError as exc:
        raise UnsafeUrlError("URL 端口非法") from exc

    resolved = list(_iter_resolved_ips(host))
    if not resolved:
        raise UnsafeUrlError("URL 主机无法解析")
    if any(not _is_safe_public_ip(ip) for ip in resolved):
        raise UnsafeUrlError("URL 指向内网、本机、链路本地或保留地址，已拒绝")
    return url.strip()


def fetch_public_url(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    max_redirects: int,
    user_agent: str = "Mozilla/5.0 (compatible; EnterpriseRAG/1.0)",
) -> FetchedUrl:
    """带 SSRF 防护、大小限制、超时和重定向限制地抓取 URL。"""

    current_url = validate_public_http_url(url)
    headers = {"User-Agent": user_agent}
    with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
        for redirect_count in range(max_redirects + 1):
            with client.stream("GET", current_url) as resp:
                if resp.is_redirect:
                    if redirect_count >= max_redirects:
                        raise UnsafeUrlError("URL 重定向次数超过限制")
                    location = resp.headers.get("location")
                    if not location:
                        raise UnsafeUrlError("URL 重定向缺少 Location")
                    current_url = validate_public_http_url(urljoin(current_url, location))
                    continue

                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                length = resp.headers.get("content-length")
                if length and int(length) > max_bytes:
                    raise UnsafeUrlError("URL 下载内容超过大小限制")

                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise UnsafeUrlError("URL 下载内容超过大小限制")
                    chunks.append(chunk)
                return FetchedUrl(
                    url=current_url,
                    content=b"".join(chunks),
                    content_type=content_type,
                    encoding=resp.encoding or "utf-8",
                )

    raise UnsafeUrlError("URL 抓取失败")
