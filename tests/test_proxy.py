import httpx
import pytest

from uv_proxy.config import Settings
from uv_proxy.proxy import ProxyBlockedTarget, ProxyError, ProxyService
from uv_proxy.server import ForwardProxyServer


@pytest.mark.asyncio
async def test_request_returns_payload_and_metrics() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-test"] == "yes"
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "x-request-id": "demo"},
            content=b'{"ok":true}',
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        service = ProxyService(Settings(allow_private_targets=True), client=client)
        result = await service.request(
            "https://example.com/demo",
            headers={"x-test": "yes", "connection": "close"},
        )

    assert result.status == 200
    assert result.body == '{"ok":true}'
    assert result.bytes_received == 11
    assert result.elapsed_ms >= 1
    assert result.headers["x-request-id"] == "demo"


@pytest.mark.asyncio
async def test_private_literal_target_is_blocked_by_default() -> None:
    service = ProxyService(Settings())
    with pytest.raises(ProxyBlockedTarget):
        await service.validate_target("http://127.0.0.1:8080/secret")
    await service.close()


def test_forward_proxy_parses_absolute_http_requests() -> None:
    method, target, headers = ForwardProxyServer.parse_request_head(
        b"GET http://example.com/hello HTTP/1.1\r\nHost: example.com\r\n\r\n"
    )

    assert method == "GET"
    assert target == "http://example.com/hello"
    assert headers == {"host": "example.com"}


def test_connect_authority_requires_valid_port() -> None:
    assert ForwardProxyServer.parse_authority("example.com:443") == ("example.com", 443)
    with pytest.raises(ProxyError):
        ForwardProxyServer.parse_authority("example.com")
