from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import get_settings
from .proxy import ProxyError, ProxyService, iter_response
from .server import ForwardProxyServer

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
settings = get_settings()


class ProxyRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = Field(default="", max_length=524_288)


class ProxyResponse(BaseModel):
    status: int
    status_text: str
    headers: dict[str, str]
    body: str
    bytes_received: int
    elapsed_ms: int
    truncated: bool


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.proxy = ProxyService(settings)
    app.state.forward_proxy = ForwardProxyServer(settings, app.state.proxy)
    await app.state.forward_proxy.start()
    try:
        yield
    finally:
        await app.state.forward_proxy.stop()
        await app.state.proxy.close()


app = FastAPI(title="uv proxy", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health(request: Request) -> dict[str, str | int]:
    proxy: ForwardProxyServer = request.app.state.forward_proxy
    proxy_host, proxy_port = proxy.address
    return {
        "status": "ok",
        "service": "uv-proxy",
        "proxy_host": proxy_host,
        "proxy_port": proxy_port,
    }


@app.post("/api/proxy", response_model=ProxyResponse)
async def api_proxy(payload: ProxyRequest, request: Request) -> ProxyResponse:
    service: ProxyService = request.app.state.proxy
    try:
        result = await service.request(
            payload.url,
            payload.method,
            payload.headers,
            payload.body.encode("utf-8"),
        )
    except ProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return ProxyResponse(
        status=result.status,
        status_text=result.status_text,
        headers=result.headers,
        body=result.body,
        bytes_received=result.bytes_received,
        elapsed_ms=result.elapsed_ms,
        truncated=result.truncated,
    )


@app.api_route(
    "/proxy",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
async def direct_proxy(request: Request, url: str) -> StreamingResponse:
    service: ProxyService = request.app.state.proxy
    try:
        response, stream_context = await service.stream(
            url,
            request.method,
            dict(request.headers),
            await request.body(),
        )
    except ProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    response_headers = service.clean_response_headers(response.headers)
    return StreamingResponse(
        iter_response(response, stream_context),
        status_code=response.status_code,
        headers=response_headers,
    )
