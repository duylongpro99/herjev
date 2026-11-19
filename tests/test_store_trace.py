import json
import pytest
import time

from herjev.aggregator import Aggregator
from herjev.analyzers.openai_compat import OpenAICompatAnalyzer
from herjev.models import Level, LogEvent
from herjev.pipeline import Pipeline, Route
from herjev.registry import create
from herjev.sources.http_poll import LokiSource
from herjev.store import Store

from conftest import Recorder, make_incident
from test_deciders import JEV_RESPONSE
from test_pipeline import Sink

ADVICE = {"summary": "db down", "probable_causes": ["pg crashed"], "recommended_actions": ["restart pg"]}
COMPLETION = {"model": "m", "choices": [{"message": {"content": json.dumps(ADVICE)}, "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

def _pipeline(tmp_path, dsn, decider, analyzer=None, **kw):
    return Pipeline([], decider, analyzer, {"a": Sink("a")}, [Route(channels=["a"])],
                    Aggregator(window_seconds=0), store=Store(dsn),
                    status_file=tmp_path / "status.json", **kw)


def _steps(db, incident_id):
    return db.execute("SELECT * FROM herjev.incident_steps WHERE incident_id = %s ORDER BY seq",
                      (incident_id,)).fetchall()


async def test_steps_persisted_in_pipeline_order(tmp_path, pg_dsn, pg_admin):
    jev, llm = Recorder(JEV_RESPONSE), Recorder(COMPLETION)
    decider = create("decider", {"type": "chain", "deciders": [
        {"type": "jev", "api_key": "k", "transport": jev.transport}, {"type": "rules"}]})
    p = _pipeline(tmp_path, pg_dsn, decider, OpenAICompatAnalyzer(api_key="o", transport=llm.transport))
    inc = make_incident()
    await p.process(inc)

    rows = _steps(pg_admin, inc.id)
    assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
    stages = [r["stage"] for r in rows]
    collapsed = [s for i, s in enumerate(stages) if i == 0 or stages[i - 1] != s]
    assert collapsed == ["aggregate", "decide", "decide:jev", "policy", "decision", "llm:openai",
                         "analyze", "route", "deliver"]
    jev_steps = [r for r in rows if r["stage"] == "decide:jev"]
    assert [r["message"].split(" ")[0] for r in jev_steps[1:]] == ["→", "←", "interpreted"]
    response = jev_steps[2]
    assert response["elapsed_ms"] is not None and json.loads(response["body"])["answers"]
    # Static blocks (question definitions, system prompt) are stored once and referenced by key.
    blobs = [r["blob"] for r in rows if r["blob"]]
    assert len(blobs) == 2
    stored = {r["key"]: r["body"] for r in pg_admin.execute("SELECT key, body FROM herjev.trace_blobs")}
    assert set(blobs) == set(stored) and "should_notify" in stored[jev_steps[0]["blob"]]

    row = p.store.get(inc.id)
    assert row["outcome"] == "notified" and row["project"] == "api"
    assert row["decision_raw"]["answers"] and row["duration_ms"] >= 0
    await p.aclose()


async def test_not_notified_and_rate_limited_outcomes(tmp_path, pg_dsn, pg_admin):
    p = _pipeline(tmp_path, pg_dsn, create("decider", {"type": "rules"}), max_alerts_per_hour=0)
    warn = make_incident(level=Level.WARN, count=1)
    warn.id = "warn1"
    await p.process(warn)
    err = make_incident()
    await p.process(err)
    assert p.store.get("warn1")["outcome"] == "not_notified"
    assert p.store.get(err.id)["outcome"] == "rate_limited"
    assert _steps(pg_admin, err.id)[-1]["stage"] == "outcome"


async def test_cooldown_suppressed_incident_is_recorded(tmp_path, pg_dsn, pg_admin):
    p = _pipeline(tmp_path, pg_dsn, create("decider", {"type": "rules"}))
    line = "ERROR could not connect to postgres"
    p.ingest(LogEvent(message=line, level=Level.ERROR, source="api", raw=line))
    [first] = p.aggregator.flush()
    await p.process(first)
    p.ingest(LogEvent(message=line, level=Level.ERROR, source="api", raw=line))
    assert p.aggregator.flush() == []
    await p._record_suppressed()

    rows = [r for r in p.store.recent() if r["id"] != first.id]
    assert len(rows) == 1 and rows[0]["outcome"] == "suppressed"
    assert rows[0]["decider"] == "cooldown" and not rows[0]["notify"]
    [st] = _steps(pg_admin, rows[0]["id"])
    assert st["stage"] == "aggregate" and "cooldown" in st["message"]
    assert p.aggregator.pop_suppressed() == []

    # Further repeats in the same cooldown period add to that row instead of new rows.
    p.ingest(LogEvent(message=line, level=Level.ERROR, source="api", raw=line))
    p.aggregator.flush()
    await p._record_suppressed()
    rows = [r for r in p.store.recent() if r["id"] != first.id]
    assert len(rows) == 1 and rows[0]["count"] == 2


async def test_muted_repeats_share_one_suppressed_row(tmp_path, pg_dsn, pg_admin):
    p = _pipeline(tmp_path, pg_dsn, create("decider", {"type": "rules"}))
    line = "ERROR could not connect to postgres"
    p.ingest(LogEvent(message=line, level=Level.ERROR, source="api", raw=line))
    [first] = p.aggregator.flush()
    p.store.mute(first.fingerprint, 3600)
    for _ in range(3):
        await p._apply_store_mutes()  # re-applied every second by the flush loop
        p.ingest(LogEvent(message=line, level=Level.ERROR, source="api", raw=line))
        assert p.aggregator.flush() == []
        await p._record_suppressed()
    [row] = p.store.recent()
    assert row["outcome"] == "suppressed" and row["count"] == 3
    assert "muted for another" in row["decision_reason"] and "-" not in row["decision_reason"].split("another")[1]


async def test_mute_sync_does_not_block_the_event_loop_on_a_busy_store(tmp_path, pg_dsn, pg_admin):
    import asyncio
    import threading
    p = _pipeline(tmp_path, pg_dsn, create("decider", {"type": "rules"}))
    release, held = threading.Event(), threading.Event()

    def hold_every_connection():
        with p.store._pool.connection(), p.store._pool.connection():
            held.set()
            release.wait()
    holder = threading.Thread(target=hold_every_connection)
    holder.start()
    try:
        assert held.wait(5)
        sync = asyncio.create_task(p._apply_store_mutes())
        started = time.monotonic()
        await asyncio.sleep(0.05)  # the loop keeps running while the sync waits for a connection
        assert time.monotonic() - started < 0.5 and not sync.done()
    finally:
        release.set()
        holder.join()
    await sync



async def test_a_stuck_store_does_not_hold_back_flushing(tmp_path, pg_dsn, pg_admin):
    import asyncio
    import threading
    p = _pipeline(tmp_path, pg_dsn, create("decider", {"type": "rules"}))
    release, held = threading.Event(), threading.Event()

    def hold_every_connection():
        with p.store._pool.connection(), p.store._pool.connection():
            held.set()
            release.wait()
    holder = threading.Thread(target=hold_every_connection)
    holder.start()
    loop = None
    try:
        assert held.wait(5)
        loop = asyncio.create_task(p._flush_loop())
        await asyncio.sleep(1.2)  # the first tick starts a store sync that cannot get a connection
        line = "FATAL out of memory"
        p.ingest(LogEvent(message=line, level=Level.FATAL, source="api", raw=line))
        await asyncio.sleep(1.2)  # the next tick still flushes it
        assert len(p.channels["a"].got) == 1  # delivered while the store is still stuck
    finally:
        if loop:
            loop.cancel()
        release.set()
        holder.join()
    await p.drain()

def _loki_incident(project_cfg: str = "", labels: dict | None = None):
    src = LokiSource("loki", url="http://loki", query="{}", project=project_cfg)
    events = src._parse({"data": {"result": [
        {"stream": labels or {}, "values": [[str(time.time_ns()), "ERROR boom"]]}]}})
    agg = Aggregator(window_seconds=0)
    for ev in events:
        agg.add(ev)
    [inc] = agg.flush()
    return inc


def test_project_resolution_order():
    assert _loki_incident("shop", {"project": "other"}).project == "shop"
    assert _loki_incident("", {"project": "other"}).project == "other"
    assert _loki_incident().project == "loki"


async def test_retention_prunes_old_incidents_and_steps(tmp_path, pg_dsn, pg_admin):
    p = _pipeline(tmp_path, pg_dsn, create("decider", {"type": "rules"}))
    old, new = make_incident(), make_incident()
    new.id = "new1"
    await p.process(old)
    await p.process(new)
    pg_admin.execute("UPDATE herjev.incidents SET created_at = %s WHERE id = %s", (time.time() - 40 * 86400, old.id))
    assert p.store.prune(30) == 1
    assert p.store.get(old.id) is None and _steps(pg_admin, old.id) == []
    assert p.store.get("new1") and _steps(pg_admin, "new1")


def test_loki_service_names_map_coolify_ids():
    src = LokiSource("loki", url="http://loki", query="{}", strip_service_ids=True,
                     service_names={"k3v9q2m7x1c8b4n6z0p5r2t7": "web-api"})

    def service(label: str) -> str:
        [ev] = src._parse({"data": {"result": [
            {"stream": {"service_name": label}, "values": [[str(time.time_ns()), "ERROR boom"]]}]}})
        return ev.service

    assert service("k3v9q2m7x1c8b4n6z0p5r2t7") == "web-api"
    assert service("k3v9q2m7x1c8b4n6z0p5r2t7-proxy") == "web-api-proxy"
    assert service("jobs-scheduler-t4y7u1i9o3p6a2s8d5f0g3h1") == "jobs-scheduler"
    assert service("m8w2d5h1j6f4s9a3g7l0q2e5") == "m8w2d5h1j6f4s9a3g7l0q2e5"
    assert service("coolify-proxy") == "coolify-proxy"
    assert LokiSource("loki", url="http://loki", query="{}")._service_name(
        "jobs-scheduler-t4y7u1i9o3p6a2s8d5f0g3h1") == "jobs-scheduler-t4y7u1i9o3p6a2s8d5f0g3h1"


def test_loki_services_select_only_the_listed_services():
    src = LokiSource("loki", url="http://loki", query='{job="docker"} |~ "error"',
                     strip_service_ids=True, services=["web-api", "jobs-scheduler"],
                     service_names={"k3v9q2m7x1c8b4n6z0p5r2t7": "web-api"})
    assert src.query == ('{job="docker", service_name=~"web-api|web-api-[a-z0-9]{24}|'
                         'k3v9q2m7x1c8b4n6z0p5r2t7|k3v9q2m7x1c8b4n6z0p5r2t7-[a-z0-9]{24}|'
                         'jobs-scheduler|jobs-scheduler-[a-z0-9]{24}"} |~ "error"')
    now = str(time.time_ns())
    events = src._parse({"data": {"result": [
        {"stream": {"service_name": label}, "values": [[now, "ERROR boom"]]}
        for label in ("k3v9q2m7x1c8b4n6z0p5r2t7", "jobs-scheduler-t4y7u1i9o3p6a2s8d5f0g3h1", "coolify")]}})
    assert sorted(ev.service for ev in events) == ["jobs-scheduler", "web-api"]
    with pytest.raises(ValueError):
        LokiSource("loki", url="http://loki", query='|~ "error"', services=["x"])


def test_loki_portal_choices_override_the_config_and_return_to_it():
    src = LokiSource("loki", url="http://loki", query='{job="docker"}', strip_service_ids=True,
                     services=["worker"], service_names={"k3v9q2m7x1c8b4n6z0p5r2t7": "web-api"})
    src.labels = ["k3v9q2m7x1c8b4n6z0p5r2t7", "worker-t4y7u1i9o3p6a2s8d5f0g3h1", "m8w2d5h1j6f4s9a3g7l0q2e5"]
    assert src.catalog() == [
        {"label": "k3v9q2m7x1c8b4n6z0p5r2t7", "name": "web-api", "watched": False},
        {"label": "worker-t4y7u1i9o3p6a2s8d5f0g3h1", "name": "worker", "watched": True},
        {"label": "m8w2d5h1j6f4s9a3g7l0q2e5", "name": "m8w2d5h1j6f4s9a3g7l0q2e5", "watched": False}]

    src.apply_choices([{"label": "m8w2d5h1j6f4s9a3g7l0q2e5", "watched": True, "display_name": "admin-web"},
                       {"label": "worker-t4y7u1i9o3p6a2s8d5f0g3h1", "watched": False, "display_name": None}])
    assert src.services == ["admin-web"]
    assert src.query == ('{job="docker", service_name=~"admin-web|admin-web-[a-z0-9]{24}|'
                         'm8w2d5h1j6f4s9a3g7l0q2e5|m8w2d5h1j6f4s9a3g7l0q2e5-[a-z0-9]{24}"}')
    [ev] = src._parse({"data": {"result": [{"stream": {"service_name": "m8w2d5h1j6f4s9a3g7l0q2e5"},
                                            "values": [[str(time.time_ns()), "ERROR boom"]]}]}})
    assert ev.service == "admin-web"
    # The catalog keeps the config's defaults; the portal applies its own rows on top.
    assert src.catalog()[2]["name"] == "m8w2d5h1j6f4s9a3g7l0q2e5"

    src.apply_choices([])
    assert src.services == ["worker"] and "worker-[a-z0-9]{24}" in src.query


async def test_pipeline_applies_portal_choices_and_publishes_the_catalog(tmp_path, pg_dsn, pg_admin):
    src = LokiSource("coolify", url="http://loki", query='{job="docker"}', strip_service_ids=True)
    src.labels = ["worker-t4y7u1i9o3p6a2s8d5f0g3h1", "api-t4y7u1i9o3p6a2s8d5f0g3h1"]
    p = _pipeline(tmp_path, pg_dsn, create("decider", {"type": "rules"}))
    p.sources = [src]
    pg_admin.execute("INSERT INTO herjev.source_services (source, label, watched) VALUES "
                     "('coolify', 'api-t4y7u1i9o3p6a2s8d5f0g3h1', true), ('other', 'x', true)")
    await p._apply_service_choices()
    assert src.services == ["api"]
    p._write_status()
    await p._status_task
    body = pg_admin.execute("SELECT body FROM herjev.watcher_status").fetchone()["body"]
    assert body["services"]["coolify"][1] == {"label": "api-t4y7u1i9o3p6a2s8d5f0g3h1", "name": "api", "watched": True}
