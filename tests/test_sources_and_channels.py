import asyncio
import json

import httpx

from herjev.config import parse_config
from herjev.models import Level
from herjev.registry import create
from herjev.sources.file import FileSource
from herjev.sources.listen import WebhookSource

import pytest


async def test_webhook_source_over_real_socket():
    src = WebhookSource("push", bind="127.0.0.1", port=0, token="t")
    it = src.stream().__aiter__()
    first = asyncio.create_task(it.__anext__())
    while src.bound_port is None:
        await asyncio.sleep(0.01)
    base = f"http://127.0.0.1:{src.bound_port}"
    async with httpx.AsyncClient() as c:
        r = await c.post(base + "/logs", json=[{"level": "error", "msg": "a"}])
        assert r.status_code == 401
        r = await c.post(base + "/logs", headers={"Authorization": "Bearer t"},
                         json={"logs": [{"level": "error", "msg": "db down", "host": "h1"},
                                        {"level": "info", "msg": "ok"}]})
        assert r.json() == {"ok": True, "accepted": 2}
        r = await c.post(base + "/logs", headers={"Authorization": "Bearer t", "Content-Type": "text/plain"},
                         content="panic: boom\nINFO fine\n")
        assert r.json()["accepted"] == 2
    ev = await asyncio.wait_for(first, 2)
    assert ev.level == Level.ERROR and ev.host == "h1"
    rest = [await it.__anext__() for _ in range(3)]
    assert rest[1].level == Level.PANIC
    await it.aclose()


async def test_file_source_tails_appends(tmp_path):
    p = tmp_path / "a.log"
    p.write_text("ERROR old line\n")
    src = FileSource("f", str(p), poll_interval=0.05)
    it = src.stream().__aiter__()
    nxt = asyncio.create_task(it.__anext__())
    await asyncio.sleep(0.2)
    with p.open("a") as f:
        f.write("FATAL new line\n")
    ev = await asyncio.wait_for(nxt, 2)
    assert ev.message == "FATAL new line"   # old content skipped by default
    await it.aclose()


async def test_command_source_streams():
    src = create("source", {"type": "command", "name": "c", "restart": False,
                            "command": ["printf", "ERROR a\\nWARN b\\n"]})
    events = [e async for e in src.stream()]
    assert [e.level for e in events] == [Level.ERROR, Level.WARN]


def test_env_substitution(monkeypatch):
    monkeypatch.setenv("TOK", "secret")
    cfg = parse_config({"sources": [{"name": "x", "type": "file", "path": "${NOPE:-/tmp/x}"}],
                        "channels": {"c": {"type": "webhook", "url": "https://x", "headers": {"A": "${TOK}"}}}})
    assert cfg["sources"][0]["path"] == "/tmp/x"
    assert cfg["channels"]["c"]["headers"]["A"] == "secret"


def test_unknown_type_lists_available():
    with pytest.raises(ValueError, match="available"):
        create("notifier", {"type": "carrier-pigeon"}, name="x")
