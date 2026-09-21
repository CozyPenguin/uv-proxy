# uv proxy

uv proxy is a small async HTTP proxy with a smooth control-room UI. It uses uv for fast Python environments and httpx for HTTP/2 support, pooled keep-alive connections, and a compact async request path.

## Run locally

~~~powershell
uv sync
uv run uvicorn uv_proxy.main:app --reload
~~~

Open http://127.0.0.1:8000.

The UI sends inspection requests to POST /api/proxy. A streaming pass-through endpoint is also available at /proxy?url=https://example.com for clients that need the original response body. Private and reserved network targets are blocked by default; use an explicit allowlist with UV_PROXY_ALLOWED_HOSTS for a tighter deployment.

## Configuration

Copy .env.example to .env to set the bind address, timeout, body preview limit, and target policy. The default client keeps up to 40 idle connections warm and allows up to 100 concurrent upstream connections.

## Tests

~~~powershell
uv run pytest
uv run ruff check .
~~~

## Container

~~~powershell
docker build -t uv-proxy .
docker run --rm -p 8000:8000 uv-proxy
~~~

## Safety note

This project is intended as a focused, self-hosted proxy playground. Do not expose it to an untrusted network without authentication, rate limiting, request logging, and a deployment-specific hostname allowlist.
