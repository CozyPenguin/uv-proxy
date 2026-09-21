from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .config import Settings

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
FORWARD_REQUEST_HEADERS = HOP_BY_HOP_HEADERS | {"host", "content-length"}
VISIBLE_RESPONSE_HEADERS = {
    "cache-control",
    "content-type",
    "etag",
    "last-modified",
    "location",
    "server",
    "vary",
    "x-request-id",
}


class ProxyError(Exception):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProxyBlockedTarget(ProxyError):
    def __init__(self, message: str = "That target is not allowed by the proxy.") -> None:
        super().__init__(message, status_code=403)


@dataclass(slots=True)
class ProxyResult:
    status: int
    status_text: str
    headers: dict[str, str]
    body: str
    bytes_received: int
    elapsed_ms: int
    truncated: bool


class ProxyService:
    """Small async proxy service with a pooled, HTTP/2-capable client."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            http2=True,
            follow_redirects=True,
            timeout=httpx.Timeout(settings.request_timeout),
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=40,
                keepalive_expiry=30,
            ),
            headers={"user-agent": "uv-proxy/0.1"},
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def request(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> ProxyResult:
        await self.validate_target(url)
        request_headers = self.clean_request_headers(headers or {})
        started = time.perf_counter()
        try:
            response = await self.client.request(method, url, headers=request_headers, content=body)
            raw_body = response.content
        except httpx.TimeoutException as exc:
            raise ProxyError("The upstream connection timed out.", 504) from exc
        except httpx.RequestError as exc:
            raise ProxyError(f"Could not reach the upstream: {exc}") from exc

        elapsed_ms = max(1, round((time.perf_counter() - started) * 1000))
        preview = raw_body[: self.settings.max_body_bytes]
        return ProxyResult(
            status=response.status_code,
            status_text=response.reason_phrase,
            headers=self.clean_response_headers(response.headers),
            body=preview.decode("utf-8", errors="replace"),
            bytes_received=len(raw_body),
            elapsed_ms=elapsed_ms,
            truncated=len(raw_body) > len(preview),
        )

    async def stream(
        self,
        url: str,
        method: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[httpx.Response, object]:
        await self.validate_target(url)
        request_headers = self.clean_request_headers(headers)
        stream_context = self.client.stream(method, url, headers=request_headers, content=body)
        try:
            response = await stream_context.__aenter__()
        except httpx.TimeoutException as exc:
            raise ProxyError("The upstream connection timed out.", 504) from exc
        except httpx.RequestError as exc:
            raise ProxyError(f"Could not reach the upstream: {exc}") from exc
        return response, stream_context

    async def validate_target(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ProxyError("Enter a complete http:// or https:// URL.", 400)
        if parsed.username or parsed.password:
            raise ProxyError("URLs with embedded credentials are not supported.", 400)

        hostname = parsed.hostname.lower().rstrip(".")
        patterns = self.settings.allowed_host_patterns
        if patterns and not any(self.host_matches(hostname, pattern) for pattern in patterns):
            raise ProxyBlockedTarget("That hostname is not in the configured allowlist.")
        if self.settings.allow_private_targets:
            return
        if self.is_literal_private_ip(hostname):
            raise ProxyBlockedTarget("Private, loopback, and reserved IPs are blocked.")

        try:
            addresses = await asyncio.to_thread(self.resolve_addresses, hostname)
        except (OSError, socket.gaierror) as exc:
            raise ProxyError("The target hostname could not be resolved.", 400) from exc
        if any(self.is_private_ip(address) for address in addresses):
            raise ProxyBlockedTarget("The target resolves to a private or reserved network.")

    @staticmethod
    def resolve_addresses(hostname: str) -> set[str]:
        return {item[4][0] for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)}

    @staticmethod
    def is_literal_private_ip(hostname: str) -> bool:
        try:
            return ProxyService.is_private_ip(hostname)
        except ValueError:
            return False

    @staticmethod
    def is_private_ip(address: str) -> bool:
        ip = ipaddress.ip_address(address)
        return any(
            (
                ip.is_private,
                ip.is_loopback,
                ip.is_link_local,
                ip.is_reserved,
                ip.is_multicast,
                ip.is_unspecified,
            )
        )

    @staticmethod
    def host_matches(hostname: str, pattern: str) -> bool:
        pattern = pattern.lstrip("*.")
        return hostname == pattern or hostname.endswith(f".{pattern}")

    @staticmethod
    def clean_request_headers(headers: dict[str, str]) -> dict[str, str]:
        return {
            key: value
            for key, value in headers.items()
            if key.lower() not in FORWARD_REQUEST_HEADERS
        }

    @staticmethod
    def clean_response_headers(headers: httpx.Headers) -> dict[str, str]:
        return {
            key: value
            for key, value in headers.items()
            if key.lower() in VISIBLE_RESPONSE_HEADERS
        }


async def iter_response(response: httpx.Response, stream_context: object) -> AsyncIterator[bytes]:
    try:
        async for chunk in response.aiter_raw():
            yield chunk
    finally:
        await stream_context.__aexit__(None, None, None)  # type: ignore[attr-defined]
