from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from .config import Settings
from .proxy import HOP_BY_HOP_HEADERS, ProxyError, ProxyService

MAX_HEADER_BYTES = 64 * 1024
MAX_TUNNEL_CHUNK = 64 * 1024
STATUS_REASONS = {
    400: "Bad Request",
    403: "Forbidden",
    413: "Payload Too Large",
    502: "Bad Gateway",
    504: "Gateway Timeout",
}


class ForwardProxyServer:
    """A small HTTP/1.1 forward proxy with CONNECT tunneling."""

    def __init__(self, settings: Settings, service: ProxyService) -> None:
        self.settings = settings
        self.service = service
        self.server: asyncio.Server | None = None

    async def start(self) -> None:
        self.server = await asyncio.start_server(
            self.handle_client,
            host=self.settings.proxy_host,
            port=self.settings.proxy_port,
            limit=MAX_HEADER_BYTES,
        )

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    @property
    def address(self) -> tuple[str, int]:
        if not self.server or not self.server.sockets:
            return self.settings.proxy_host, self.settings.proxy_port
        host, port = self.server.sockets[0].getsockname()[:2]
        return host, port

    async def handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            header_block = await reader.readuntil(b"\r\n\r\n")
            method, target, headers = self.parse_request_head(header_block)
            if method.upper() == "CONNECT":
                await self.handle_connect(reader, writer, target)
            else:
                await self.handle_http(reader, writer, method, target, headers)
        except asyncio.LimitOverrunError:
            await self.send_error(writer, ProxyError("Request headers are too large.", 413))
        except asyncio.IncompleteReadError:
            pass
        except ProxyError as exc:
            await self.send_error(writer, exc)
        except (ConnectionError, asyncio.CancelledError):
            pass
        except Exception:
            await self.send_error(writer, ProxyError("The proxy could not complete that request."))
        finally:
            if not writer.is_closing():
                writer.close()
                await writer.wait_closed()

    async def handle_connect(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        target: str,
    ) -> None:
        host, port = self.parse_authority(target)
        await self.service.validate_target(self.target_url(host, port))
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(host, port)
        except TimeoutError as exc:
            raise ProxyError("The upstream connection timed out.", 504) from exc
        except OSError as exc:
            raise ProxyError("Could not connect to the upstream target.") from exc

        writer.write(b"HTTP/1.1 200 Connection Established\r\n")
        writer.write(b"Proxy-Agent: uv-proxy\r\n\r\n")
        await writer.drain()
        await self.tunnel(writer, reader, upstream_reader, upstream_writer)

    async def handle_http(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        method: str,
        target: str,
        headers: dict[str, str],
    ) -> None:
        url = target
        if not target.startswith(("http://", "https://")):
            host = headers.get("host")
            if not host:
                raise ProxyError("The request is missing a Host header.", 400)
            url = f"http://{host}{target}"
        parsed = urlparse(url)
        if parsed.scheme != "http":
            raise ProxyError("Use CONNECT for HTTPS proxy requests.", 400)
        await self.service.validate_target(url)
        body = await self.read_body(reader, headers)
        clean_headers = self.service.clean_request_headers(headers)

        try:
            async with self.service.client.stream(
                method,
                url,
                headers=clean_headers,
                content=body,
            ) as response:
                writer.write(
                    f"HTTP/1.1 {response.status_code} {response.reason_phrase}\r\n".encode()
                )
                for key, value in response.headers.multi_items():
                    if key.lower() not in HOP_BY_HOP_HEADERS:
                        writer.write(f"{key}: {value}\r\n".encode())
                writer.write(b"connection: close\r\n\r\n")
                await writer.drain()
                async for chunk in response.aiter_raw():
                    writer.write(chunk)
                    await writer.drain()
        except TimeoutError as exc:
            raise ProxyError("The upstream connection timed out.", 504) from exc
        except OSError as exc:
            raise ProxyError("Could not reach the upstream target.") from exc

    async def read_body(self, reader: asyncio.StreamReader, headers: dict[str, str]) -> bytes:
        length_header = headers.get("content-length")
        if length_header:
            try:
                length = int(length_header)
            except ValueError as exc:
                raise ProxyError("Invalid Content-Length header.", 400) from exc
            if length < 0 or length > self.settings.max_body_bytes:
                raise ProxyError("Request body exceeds the configured limit.", 413)
            return await reader.readexactly(length)
        if headers.get("transfer-encoding", "").lower() == "chunked":
            return await self.read_chunked_body(reader)
        return b""

    async def read_chunked_body(self, reader: asyncio.StreamReader) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            size_line = await reader.readline()
            try:
                size = int(size_line.strip().split(b";", 1)[0], 16)
            except ValueError as exc:
                raise ProxyError("Invalid chunked request body.", 400) from exc
            if size == 0:
                while await reader.readline() != b"\r\n":
                    pass
                return b"".join(chunks)
            total += size
            if total > self.settings.max_body_bytes:
                raise ProxyError("Request body exceeds the configured limit.", 413)
            chunks.append(await reader.readexactly(size))
            if await reader.readexactly(2) != b"\r\n":
                raise ProxyError("Invalid chunked request body.", 400)

    @staticmethod
    def parse_request_head(data: bytes) -> tuple[str, str, dict[str, str]]:
        lines = data.decode("latin-1").split("\r\n")
        request_parts = lines[0].split(" ", 2)
        if len(request_parts) != 3 or not request_parts[2].startswith("HTTP/"):
            raise ProxyError("Malformed proxy request.", 400)
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line:
                break
            if ":" not in line:
                raise ProxyError("Malformed proxy header.", 400)
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        return request_parts[0], request_parts[1], headers

    @staticmethod
    def parse_authority(authority: str) -> tuple[str, int]:
        if authority.startswith("["):
            host_end = authority.find("]")
            if host_end < 0 or authority[host_end + 1 : host_end + 2] != ":":
                raise ProxyError("CONNECT requires host:port.", 400)
            host = authority[1:host_end]
            port_text = authority[host_end + 2 :]
        else:
            host, separator, port_text = authority.rpartition(":")
            if not separator:
                raise ProxyError("CONNECT requires host:port.", 400)
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ProxyError("CONNECT requires a numeric port.", 400) from exc
        if not host or not 1 <= port <= 65535:
            raise ProxyError("CONNECT target is invalid.", 400)
        return host, port

    @staticmethod
    def target_url(host: str, port: int) -> str:
        formatted_host = f"[{host}]" if ":" in host else host
        return f"http://{formatted_host}:{port}/"

    @staticmethod
    async def tunnel(
        client_writer: asyncio.StreamWriter,
        client_reader: asyncio.StreamReader,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        async def client_to_upstream() -> None:
            while data := await client_reader.read(MAX_TUNNEL_CHUNK):
                upstream_writer.write(data)
                await upstream_writer.drain()

        async def upstream_to_client() -> None:
            while data := await upstream_reader.read(MAX_TUNNEL_CHUNK):
                client_writer.write(data)
                await client_writer.drain()

        await asyncio.gather(
            client_to_upstream(),
            upstream_to_client(),
            return_exceptions=True,
        )
        if not upstream_writer.is_closing():
            upstream_writer.close()
            await upstream_writer.wait_closed()

    @staticmethod
    async def send_error(writer: asyncio.StreamWriter, error: ProxyError) -> None:
        if writer.is_closing():
            return
        reason = STATUS_REASONS.get(error.status_code, "Proxy Error")
        body = f"{error.status_code} {reason}\n{error}\n".encode()
        writer.write(
            f"HTTP/1.1 {error.status_code} {reason}\r\n"
            f"content-type: text/plain; charset=utf-8\r\n"
            f"content-length: {len(body)}\r\n"
            "connection: close\r\n\r\n".encode()
        )
        writer.write(body)
        await writer.drain()
