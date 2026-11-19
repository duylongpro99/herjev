from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import Any, AsyncIterator

from ..models import LogEvent
from ..registry import register
from .base import Source

log = logging.getLogger(__name__)


@register("source", "webhook")
class WebhookSource(Source):
    """Listen for logs pushed over HTTP (Fluent Bit / Vector / Logstash
    ``http`` outputs, or any app).

    ``POST <path>`` with JSON object, JSON array, ``{"logs": [...]}``,
    NDJSON, or text/plain lines. Optional ``token`` requires
    ``Authorization: Bearer <token>``. Implemented on asyncio streams so it
    adds no dependencies.
    """

    def __init__(self, name: str, bind: str = "0.0.0.0", port: int = 8787,
                 path: str = "/logs", token: str | None = None,
                 max_body: int = 5_000_000, **kw):
        super().__init__(name, **kw)
        self.bind, self.port, self.path = bind, port, path
        self.token = token or None
        self.max_body = max_body
        self.queue: asyncio.Queue[LogEvent] = asyncio.Queue(maxsize=50_000)
        self.bound_port: int | None = None

    def ingest(self, body: bytes, content_type: str) -> int:
        text = body.decode("utf-8", "replace")
        items: list[Any]
        if "json" in content_type and "ndjson" not in content_type:
            data = json.loads(text)
            if isinstance(data, dict):
                inner = data.get("logs") or data.get("records") or data.get("entries")
                items = inner if isinstance(inner, list) else [data]
            else:
                items = data if isinstance(data, list) else [data]
        else:
            items = text.splitlines()
        n = 0
        for it in items:
            ev = self.record(it) if isinstance(it, dict) else self.line(str(it))
            if ev is None:
                continue
            try:
                self.queue.put_nowait(ev)
                n += 1
            except asyncio.QueueFull:
                log.warning("webhook source %s queue full; dropping events", self.name)
                break
        return n

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status, body_out = 200, b'{"ok":true}'
        try:
            request_line = (await reader.readline()).decode("latin-1").strip()
            method, target, _ = (request_line.split(" ", 2) + ["", ""])[:3]
            headers: dict[str, str] = {}
            while True:
                h = (await reader.readline()).decode("latin-1")
                if h in ("\r\n", "\n", ""):
                    break
                k, _, v = h.partition(":")
                headers[k.strip().lower()] = v.strip()
            length = int(headers.get("content-length", "0") or 0)
            if method == "GET" and target.split("?")[0] == "/healthz":
                body_out = b'{"ok":true}'
            elif method != "POST" or target.split("?")[0] != self.path:
                status, body_out = 404, b'{"error":"not found"}'
            elif self.token and not hmac.compare_digest(
                headers.get("authorization", ""), f"Bearer {self.token}"
            ):
                status, body_out = 401, b'{"error":"unauthorized"}'
            elif length > self.max_body:
                status, body_out = 413, b'{"error":"too large"}'
            else:
                body = await reader.readexactly(length) if length else b""
                n = self.ingest(body, headers.get("content-type", "text/plain"))
                body_out = json.dumps({"ok": True, "accepted": n}).encode()
        except (ValueError, asyncio.IncompleteReadError) as exc:
            status, body_out = 400, json.dumps({"error": str(exc)}).encode()
        reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 413: "Payload Too Large"}[status]
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body_out)}\r\nConnection: close\r\n\r\n".encode() + body_out
        )
        try:
            await writer.drain()
        finally:
            writer.close()

    async def stream(self) -> AsyncIterator[LogEvent]:
        if not self.token and self.bind not in ("127.0.0.1", "localhost", "::1"):
            log.warning("webhook source %s listens on %s without a token — anyone who can reach "
                        "the port can inject logs; set `token:`", self.name, self.bind)
        server = await asyncio.start_server(self._handle, self.bind, self.port)
        self.bound_port = server.sockets[0].getsockname()[1]
        log.info("webhook source %s listening on %s:%s%s", self.name, self.bind, self.bound_port, self.path)
        async with server:
            while True:
                yield await self.queue.get()


class _SyslogProtocol(asyncio.DatagramProtocol):
    def __init__(self, src: "SyslogSource"):
        self.src = src

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.src.on_datagram(data, addr)


@register("source", "syslog")
class SyslogSource(Source):
    """Listen for RFC3164/5424 syslog over UDP. The ``<PRI>`` severity maps to levels."""

    _SEV = {0: "panic", 1: "fatal", 2: "fatal", 3: "error", 4: "warn", 5: "info", 6: "info", 7: "debug"}

    def __init__(self, name: str, bind: str = "0.0.0.0", port: int = 5514, **kw):
        super().__init__(name, **kw)
        self.bind, self.port = bind, port
        self.queue: asyncio.Queue[LogEvent] = asyncio.Queue(maxsize=50_000)

    def on_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        text = data.decode("utf-8", "replace").strip()
        level = None
        if text.startswith("<") and ">" in text[:5]:
            pri, _, text = text[1:].partition(">")
            if pri.isdigit():
                level = self._SEV[int(pri) % 8]
        ev = self.line(text)
        if ev is None:
            return
        if level:
            from ..models import Level
            ev.level = Level.parse(level)
        ev.host = ev.host or addr[0]
        try:
            self.queue.put_nowait(ev)
        except asyncio.QueueFull:
            pass

    async def stream(self) -> AsyncIterator[LogEvent]:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _SyslogProtocol(self), local_addr=(self.bind, self.port))
        try:
            while True:
                yield await self.queue.get()
        finally:
            transport.close()
