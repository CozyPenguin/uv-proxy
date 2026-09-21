# uv proxy

uv proxy is a real async HTTP/HTTPS forward proxy with a smooth control-room UI. It uses uv for fast Python environments, httpx for pooled upstream connections, and a native asyncio listener for HTTP forwarding plus HTTPS CONNECT tunneling.

## Run locally

~~~powershell
uv sync
uv run uvicorn uv_proxy.main:app --reload
~~~

Open http://127.0.0.1:8000.

Configure your browser or client to use 127.0.0.1:8888 as its HTTP proxy. HTTPS requests use CONNECT automatically. The web UI is a control panel and probe client; it is not required for proxy traffic.

The UI also exposes POST /api/proxy for an inspected request and GET /proxy?url=https://example.com as a direct streaming helper. Private and reserved network targets are blocked by default; use an explicit allowlist with UV_PROXY_ALLOWED_HOSTS for a tighter deployment.

## Configuration

Copy .env.example to .env to set the control-panel address, proxy listener address, timeout, body preview limit, and target policy. The default client keeps up to 40 idle connections warm and allows up to 100 concurrent upstream connections.

Example with curl:

~~~powershell
curl.exe --proxy http://127.0.0.1:8888 https://example.com
~~~

## Tests

~~~powershell
uv run pytest
uv run ruff check .
~~~

## Container

~~~powershell
docker build -t uv-proxy .
docker run --rm -p 8000:8000 -p 8888:8888 uv-proxy
~~~

## Safety note

This project is intended as a focused, self-hosted proxy playground. Do not expose it to an untrusted network without authentication, rate limiting, request logging, and a deployment-specific hostname allowlist.
