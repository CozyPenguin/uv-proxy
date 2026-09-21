import httpx
import pytest

from uv_proxy.config import Settings
from uv_proxy.proxy import ProxyBlockedTarget, ProxyService


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
