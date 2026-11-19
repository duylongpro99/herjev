"""HTTP MCP endpoint through which an agent (e.g. Hermes) runs herjev's on-call tools.

``herjev run`` serves it when ``mcp_server.enabled`` is true, at
``http://<host>:<port>/mcp`` (streamable HTTP). Every request must carry
``Authorization: Bearer <mcp_server.token>``; the endpoint refuses to start
without a token. The tools only read incidents, triage pasted logs and mute
fingerprints, so an agent gets no shell and no file access through it.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import socket
import sys
import time
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .paths import status_path
from .store import Store

LEVELS = ("warn", "error", "fatal", "panic")


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def build_server(cfg: dict[str, Any], store: Store | None) -> MCPServer:
    """The MCP tools, bound to this instance's config and incident store."""
    server = MCPServer("herjev", instructions=(
        "herjev watches server logs and alerts on warn/error/fatal/panic incidents. "
        "Look incidents up by the id or fingerprint shown in an alert."))

    def need_store() -> Store:
        if store is None:
            raise ToolError("this herjev instance runs without an incident store")
        return store

    @server.tool(structured_output=False)
    def status() -> str:
        """Is monitoring alive? Watcher status: uptime, events, incidents, alerts sent, recent errors."""
        path = status_path(cfg)
        if not path.exists():
            raise ToolError(f"watcher not running (no {path})")
        data = json.loads(path.read_text())
        data["age_seconds"] = round(time.time() - data.get("updated_at", 0))
        return _json(data)

    @server.tool(structured_output=False)
    async def incidents(limit: int = 10, hours: float | None = None, min_level: str | None = None,
                        notified_only: bool = False) -> str:
        """Recent incidents, newest first. min_level: warn, error, fatal or panic."""
        if min_level is not None and min_level not in LEVELS:
            raise ToolError(f"min_level must be one of {', '.join(LEVELS)}")
        rows = await asyncio.to_thread(need_store().recent, limit=max(1, min(limit, 100)), since_hours=hours,
                                       notified_only=notified_only, min_level=min_level)
        return _json(rows)

    @server.tool(structured_output=False)
    async def incident(id: str) -> str:
        """One incident in full (samples, context lines, triage, analysis) by id or fingerprint."""
        row = await asyncio.to_thread(need_store().get, id)
        if row is None:
            raise ToolError(f"no incident {id!r}")
        return _json(row)

    @server.tool(structured_output=False)
    async def analyze(logs: str, host: str = "", service: str = "") -> str:
        """Triage and analyze pasted log lines without alerting anyone."""
        from .adhoc import triage_text
        alerts = await triage_text(cfg, logs, host=host, service=service)
        if not alerts:
            return "no warn/error/fatal/panic lines found"
        return _json([a.to_dict() for a in alerts])

    @server.tool(structured_output=False)
    async def mute(id: str, minutes: float = 60, reason: str = "") -> str:
        """Silence alerts for an incident's fingerprint. Only call after the user confirms."""
        st = need_store()
        row = await asyncio.to_thread(st.get, id)
        fp = row["fingerprint"] if row else id
        await asyncio.to_thread(st.mute, fp, minutes * 60, reason)
        return _json({"muted": fp, "minutes": minutes, "known_incident": row is not None})

    return server


def require_token(app, token: str):
    """ASGI wrapper that answers 401 unless the request carries the bearer token."""
    expected = f"Bearer {token}".encode()

    async def guarded(scope, receive, send):
        if scope["type"] == "http":
            supplied = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(supplied, expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await app(scope, receive, send)

    return guarded


class _Uvicorn(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):  # `herjev run` owns Ctrl+C and SIGTERM
        yield


async def start(mcp_cfg: dict[str, Any], cfg: dict[str, Any], store: Store | None) -> _Uvicorn:
    """Start serving in the background; stop with ``server.should_exit = True``."""
    token = str(mcp_cfg.get("token") or "")
    if not token:
        raise SystemExit("herjev: mcp_server needs a token (mcp_server.token, e.g. ${HERJEV_MCP_TOKEN})")
    host, port = mcp_cfg.get("host", "127.0.0.1"), int(mcp_cfg.get("port", 8789))
    app = build_server(cfg, store).streamable_http_app(stateless_http=True, json_response=True, host=host)
    try:
        sock = socket.create_server((host, port), reuse_port=False)
    except OSError as exc:
        raise SystemExit(f"herjev: mcp_server could not start on {host}:{port}: {exc}") from None
    server = _Uvicorn(uvicorn.Config(require_token(app, token), log_level="warning", lifespan="on"))
    server.task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started and not server.task.done():
        await asyncio.sleep(0.05)
    if server.task.done():
        server.task.result()
    print(f"herjev mcp on http://{host}:{port}/mcp", file=sys.stderr)
    return server


async def stop(server: _Uvicorn) -> None:
    server.should_exit = True
    await server.task
