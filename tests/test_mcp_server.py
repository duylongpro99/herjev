"""The MCP endpoint Hermes calls instead of running herjev commands in a shell."""

import json
import time

import httpx
import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from herjev import mcp_server
from herjev.models import Alert, Decision, Level
from herjev.store import Store

from conftest import make_incident

TOKEN = "s3cret"


@pytest.fixture
async def endpoint(tmp_path, monkeypatch, pg_dsn, unused_tcp_port):
    monkeypatch.setenv("HERJEV_HOME", str(tmp_path))
    store = Store()
    store.record(Alert(make_incident(), Decision(notify=True, urgency=2, reason="jev")), ["agent"], [])
    warn = make_incident(level=Level.WARN, msg="WARN slow query")
    warn.id, warn.fingerprint = "inc2", "fp2"
    store.record(Alert(warn, Decision(notify=False, reason="noise")), [], [])
    cfg = {"decider": {"type": "rules"}, "analyzer": {"type": "none"}}
    server = await mcp_server.start({"port": unused_tcp_port, "token": TOKEN}, cfg, store)
    try:
        yield f"http://127.0.0.1:{unused_tcp_port}/mcp", store
    finally:
        await mcp_server.stop(server)
        store.close()


async def _call(url, tool, **args):
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}) as http, \
            streamable_http_client(url, http_client=http) as (read, write), \
            ClientSession(read, write) as session:
        await session.initialize()
        if tool is None:
            return sorted(t.name for t in (await session.list_tools()).tools)
        result = await session.call_tool(tool, args)
        text = result.content[0].text
        return ("error: " + text) if result.is_error else text


async def test_requests_without_the_token_are_refused(endpoint):
    url, _ = endpoint
    async with httpx.AsyncClient() as http:
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}):
            resp = await http.post(url, json={}, headers=headers)
            assert resp.status_code == 401, headers


async def test_tools_are_the_oncall_commands(endpoint):
    url, _ = endpoint
    assert await _call(url, None) == ["analyze", "incident", "incidents", "mute", "status"]


async def test_incident_lookup_and_listing(endpoint):
    url, _ = endpoint
    row = json.loads(await _call(url, "incident", id="fp1"))
    assert row["id"] == "inc1" and row["state"]["context"]
    assert (await _call(url, "incident", id="nope")).startswith("error: ")
    rows = json.loads(await _call(url, "incidents", min_level="error"))
    assert [r["id"] for r in rows] == ["inc1"]
    assert (await _call(url, "incidents", min_level="loud")).startswith("error: ")


async def test_mute_resolves_the_fingerprint(endpoint):
    url, store = endpoint
    out = json.loads(await _call(url, "mute", id="inc1", minutes=5, reason="deploy"))
    assert out == {"muted": "fp1", "minutes": 5, "known_incident": True}
    assert set(store.active_mutes()) == {"fp1"}


async def test_status_reads_the_watcher_status_file(endpoint, tmp_path):
    url, _ = endpoint
    assert "watcher not running" in await _call(url, "status")
    (tmp_path / "status.json").write_text(json.dumps({"incidents": 2, "updated_at": time.time()}))
    data = json.loads(await _call(url, "status"))
    assert data["incidents"] == 2 and data["age_seconds"] <= 1


async def test_analyze_triages_pasted_logs(endpoint):
    url, _ = endpoint
    assert await _call(url, "analyze", logs="all good\n") == "no warn/error/fatal/panic lines found"
    out = json.loads(await _call(url, "analyze", logs="FATAL out of memory\n", service="api"))
    assert out[0]["incident"]["level"] == "fatal"


async def test_refuses_to_start_without_a_token(unused_tcp_port):
    with pytest.raises(SystemExit, match="token"):
        await mcp_server.start({"port": unused_tcp_port}, {}, None)
