import asyncio

from herjev.aggregator import Aggregator
from herjev.analyzers.base import Analyzer
from herjev.deciders.rules import RulesDecider
from herjev.models import Analysis, Level
from herjev.notifiers.base import Notifier
from herjev.parsing import parse_line
from herjev.pipeline import Pipeline, Route
from herjev.sources.base import Source
from herjev.store import Store


class ListSource(Source):
    type_name = "list"

    def __init__(self, lines):
        super().__init__("mem", service="api")
        self.lines = lines

    async def stream(self):
        for l in self.lines:
            ev = self.line(l)
            if ev:
                yield ev
        await asyncio.sleep(3600)


class FakeAnalyzer(Analyzer):
    type_name = "fake"

    async def complete_json(self, system, payload, schema):
        assert "incident" in payload and "triage" in payload
        return {"summary": "db is down", "probable_causes": ["pg crashed"],
                "recommended_actions": ["restart pg"]}


class Sink(Notifier):
    type_name = "sink"

    def __init__(self, name, fail=False):
        super().__init__(name)
        self.got, self.fail = [], fail

    async def send(self, alert):
        if self.fail:
            raise RuntimeError("boom")
        self.got.append(alert)


def build(tmp_path, dsn, lines, routes=None, **kw):
    sinks = {"a": Sink("a"), "b": Sink("b"), "bad": Sink("bad", fail=True)}
    p = Pipeline([ListSource(lines)], RulesDecider(), FakeAnalyzer(), sinks,
                 routes or [Route(channels=["a"])], Aggregator(window_seconds=0.1),
                 store=Store(dsn), status_file=tmp_path / "status.json", **kw)
    return p, sinks


async def test_end_to_end_alert_with_analysis(tmp_path, pg_dsn):
    p, sinks = build(tmp_path, pg_dsn, [
        "INFO connecting to db",
        "ERROR could not connect to postgres at 10.0.0.5:5432",
        "ERROR could not connect to postgres at 10.0.0.6:5432",
        "DEBUG noise",
    ])
    await p.run(duration=1.5)
    [alert] = sinks["a"].got
    assert alert.incident.count == 2
    assert alert.analysis.summary == "db is down"
    assert alert.decision.category == "database"
    rows = p.store.recent()
    assert rows[0]["notify"] and rows[0]["analysis"]["summary"] == "db is down"
    assert rows[0]["channels"] == ["a"]
    assert (tmp_path / "status.json").exists()
    assert p.stats.alerts_sent == 1 and p.stats.events["error"] == 2


async def test_routing_by_level_and_failed_channel_isolated(tmp_path, pg_dsn):
    routes = [Route(channels=["b", "bad"], levels={Level.PANIC}), Route(channels=["a"], levels={Level.WARN})]
    p, sinks = build(tmp_path, pg_dsn, ["panic: runtime error: index out of range", "WARN slow"], routes=routes)
    await p.run(duration=1.5)
    assert len(sinks["b"].got) == 1 and sinks["b"].got[0].incident.level == Level.PANIC
    assert sinks["a"].got == []   # single WARN below rules threshold → not notified
    row = [r for r in p.store.recent() if r["level"] == "panic"][0]
    assert any("bad" in e for e in row["delivery_errors"])


async def test_decider_outage_falls_back_to_level(tmp_path, pg_dsn):
    class Broken(RulesDecider):
        async def decide(self, incident):
            raise RuntimeError("jev down")
    p, sinks = build(tmp_path, pg_dsn, ["FATAL out of memory"])
    p.decider = Broken()
    await p.run(duration=1.5)
    [alert] = sinks["a"].got
    assert alert.decision.provider == "fallback" and alert.decision.urgency == 2


async def test_rate_limit(tmp_path, pg_dsn):
    p, sinks = build(tmp_path, pg_dsn, [f"ERROR distinct failure kind {chr(97+i)}" for i in range(5)],
                     max_alerts_per_hour=2)
    await p.run(duration=1.5)
    assert len(sinks["a"].got) == 2 and p.stats.suppressed == 3


async def test_from_config_builds_everything(tmp_path, monkeypatch, pg_dsn):
    from herjev.config import parse_config
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    cfg = parse_config({
        "sources": [{"name": "f", "type": "file", "path": str(tmp_path / "x.log")},
                    {"name": "s", "type": "ssh", "target": "u@h", "paths": ["/var/log/a.log"]}],
        "decider": {"type": "chain", "deciders": [{"type": "jev"}, {"type": "rules"}]},
        "analyzer": {"type": "gemini"},
        "channels": {"t": {"type": "webhook", "url": "http://127.0.0.1:9/hook"}, "c": {"type": "console"}},
        "routes": [{"match": {"levels": ["panic"]}, "channels": ["t"]}],
        "pipeline": {"store_dsn": pg_dsn},
    })
    p = Pipeline.from_config(cfg)
    assert p.sources[1].build_argv()[-1] == "tail -n0 -F /var/log/a.log"
    assert p.sources[1].build_argv()[-2] == "u@h"
    await p.aclose()


def _channels_cfg(channels, routes=None):
    from herjev.config import parse_config
    return parse_config({"sources": [{"name": "x", "type": "file", "path": "/tmp/x"}],
                         "channels": channels, "routes": routes or [], "pipeline": {"store": False}})


def test_disabled_channel_is_not_built_or_routed(monkeypatch):
    import pytest
    monkeypatch.setenv("HOOK_ENABLED", "false")
    cfg = _channels_cfg({"c": {"type": "console"},
                         "hook": {"type": "webhook", "url": "http://127.0.0.1:9/h", "enabled": "${HOOK_ENABLED}"}})
    p = Pipeline.from_config(cfg, dry_run=True)
    assert list(p.channels) == ["c"] and [r.channels for r in p.routes] == [["c"]]

    p = Pipeline.from_config(_channels_cfg({"c": {"type": "console", "enabled": False},
                                            "d": {"type": "console"}},
                                           [{"match": {"levels": ["panic"]}, "channels": ["c"]},
                                            {"channels": ["c", "d"]}]), dry_run=True)
    # The panic-only route had nothing left and is dropped instead of sending everywhere.
    assert list(p.channels) == ["d"] and [r.channels for r in p.routes] == [["d"]]

    with pytest.raises(ValueError, match="unknown channel 'nope'"):
        Pipeline.from_config(_channels_cfg({"c": {"type": "console", "enabled": False}},
                                           [{"channels": ["nope"]}]), dry_run=True)
    monkeypatch.setenv("HOOK_ENABLED", "")
    assert list(Pipeline.from_config(_channels_cfg(
        {"c": {"type": "console"}, "hook": {"type": "console", "enabled": "${HOOK_ENABLED:-true}"}}),
        dry_run=True).channels) == ["c"]

    with pytest.raises(ValueError, match="enabled must be true or false"):
        _channels_cfg({"c": {"type": "console", "enabled": "maybe"}})


def test_manual_channel_is_built_but_not_routed(monkeypatch, tmp_path):
    import json
    import pytest
    monkeypatch.setenv("TEAMS_AUTO", "off")
    channels = {"c": {"type": "console"}, "teams": {"type": "console", "auto": "${TEAMS_AUTO}"}}
    p = Pipeline.from_config(_channels_cfg(channels), dry_run=True)
    assert list(p.channels) == ["c", "teams"] and [r.channels for r in p.routes] == [["c"]]
    assert p.manual_channels == {"teams"}
    p.status_file = tmp_path / "status.json"
    p._write_status()
    assert json.loads(p.status_file.read_text())["channels"] == [
        {"name": "c", "type": "console", "auto": True}, {"name": "teams", "type": "console", "auto": False}]

    # A route left with only manual channels is dropped, like one with only disabled channels.
    p = Pipeline.from_config(_channels_cfg(channels, [{"match": {"levels": ["panic"]}, "channels": ["teams"]},
                                                      {"channels": ["c", "teams"]}]), dry_run=True)
    assert [r.channels for r in p.routes] == [["c"]]

    # Every channel manual: no route at all, so nothing goes out on its own.
    p = Pipeline.from_config(_channels_cfg({"c": {"type": "console", "auto": False}}), dry_run=True)
    assert list(p.channels) == ["c"] and p.routes == []

    with pytest.raises(ValueError, match="auto must be true or false"):
        _channels_cfg({"c": {"type": "console", "auto": "sometimes"}})


def test_status_reports_the_image_version(monkeypatch, tmp_path):
    import json
    p = Pipeline.from_config(_channels_cfg({"c": {"type": "console"}}), dry_run=True)
    p.status_file = tmp_path / "status.json"
    monkeypatch.delenv("HERJEV_VERSION", raising=False)
    p._write_status()
    assert json.loads(p.status_file.read_text())["version"] is None
    monkeypatch.setenv("HERJEV_VERSION", "965276c56171b5e00498ceae4df003e555a0b539")
    p._write_status()
    assert json.loads(p.status_file.read_text())["version"] == "965276c56171b5e00498ceae4df003e555a0b539"
