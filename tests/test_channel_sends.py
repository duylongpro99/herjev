"""Sends asked for in the portal: the watcher claims them from the store, delivers the
ticket's stored alert with its own channels and records the result."""

import time

from herjev.aggregator import Aggregator
from herjev.deciders.rules import RulesDecider
from herjev.formatting import attach_link, render_text
from herjev.models import Alert, Analysis, Decision
from herjev.pipeline import SEND_TTL_SECONDS, Pipeline, Route
from herjev.store import Store, alert_from_row

from conftest import make_incident
from test_pipeline import ListSource, Sink

BASE = "https://portal.example"


def _setup(tmp_path, pg_dsn):
    store = Store(pg_dsn)
    inc = make_incident()
    analysis = Analysis(summary="db is down", probable_causes=["pg crashed"], provider="fake")
    original = Alert(inc, Decision(notify=False, urgency=1, category="database", reason="r", provider="rules"),
                     analysis)
    store.record(original, [], [], duration_ms=0)
    sinks = {"auto": Sink("auto"), "teams": Sink("teams"), "bad": Sink("bad", fail=True)}
    p = Pipeline([ListSource([])], RulesDecider(), None, sinks, [Route(channels=["auto"])],
                 Aggregator(window_seconds=5), store=store, status_file=tmp_path / "status.json",
                 link_base=BASE, manual_channels={"teams"})
    return p, sinks, original


def _request(pg_admin, channel, incident_id="inc1", age=0.0, fingerprint="fp1"):
    return pg_admin.execute(
        "INSERT INTO herjev.channel_sends (fingerprint, incident_id, channel, requested_at) "
        "VALUES (%s, %s, %s, %s) RETURNING id", (fingerprint, incident_id, channel, time.time() - age)).fetchone()["id"]


def _rows(pg_admin):
    return {r["id"]: r for r in pg_admin.execute("SELECT * FROM herjev.channel_sends").fetchall()}


async def test_portal_send_delivers_the_stored_alert_to_a_manual_channel(tmp_path, pg_dsn, pg_admin):
    p, sinks, original = _setup(tmp_path, pg_dsn)
    sid = _request(pg_admin, "teams")
    await p._send_requested()
    [alert] = sinks["teams"].got
    assert sinks["auto"].got == []
    # The same message an automatic send of this occurrence would have had, link included.
    # Only the first-seen time is estimated (the window of make_incident matches the aggregator's).
    def lines(a):
        return [l for l in render_text(a).splitlines() if not l.startswith("thời gian:")]
    assert lines(alert) == lines(attach_link(original, BASE))
    assert abs(alert.incident.first_seen - original.incident.first_seen) < 1
    assert alert.link == f"{BASE}/tickets/fp1?occ=inc1"
    assert alert.analysis.summary == "db is down" and alert.to_dict()["incident"]["state"]["samples"]
    row = _rows(pg_admin)[sid]
    assert row["status"] == "sent" and row["error"] is None and row["finished_at"] is not None
    # Sent once: the next sync finds nothing to claim.
    await p._send_requested()
    assert len(sinks["teams"].got) == 1


async def test_portal_send_failures_and_expiry_are_recorded(tmp_path, pg_dsn, pg_admin):
    p, sinks, _ = _setup(tmp_path, pg_dsn)
    failing = _request(pg_admin, "bad")
    other = _request(pg_admin, "slack")
    gone = _request(pg_admin, "teams", incident_id="pruned")
    mismatched = _request(pg_admin, "teams", fingerprint="fp-other")
    stale = _request(pg_admin, "teams", age=SEND_TTL_SECONDS + 1)
    await p._send_requested()
    rows = _rows(pg_admin)
    assert (rows[failing]["status"], rows[failing]["error"]) == ("failed", "boom")
    # Another watcher's channel: left for it, and expires if none takes it.
    assert rows[other]["status"] == "pending"
    assert rows[gone]["status"] == "failed" and "no longer stored" in rows[gone]["error"]
    assert rows[mismatched]["status"] == "failed" and "not part of ticket" in rows[mismatched]["error"]
    assert rows[stale]["status"] == "expired" and "in time" in rows[stale]["error"]
    assert sinks["teams"].got == [] and sinks["bad"].got == []
    assert any("portal send" in e["error"] for e in p.stats.errors)


async def test_a_claimed_send_is_not_claimed_again(pg_dsn, pg_admin):
    store = Store(pg_dsn)
    sid = _request(pg_admin, "teams")
    assert [r["id"] for r in store.take_sends(["teams"], SEND_TTL_SECONDS)] == [sid]
    assert store.take_sends(["teams"], SEND_TTL_SECONDS) == []
    assert _rows(pg_admin)[sid]["status"] == "sending"
    # A watcher that stopped mid-send leaves it open; it expires with that reason.
    pg_admin.execute("UPDATE herjev.channel_sends SET requested_at = 0")
    assert store.take_sends(["teams"], SEND_TTL_SECONDS) == []
    assert "stopped" in _rows(pg_admin)[sid]["error"]


async def test_a_dry_run_leaves_the_send_queue_alone(tmp_path, pg_dsn, pg_admin):
    p, sinks, _ = _setup(tmp_path, pg_dsn)
    p.dry_run = True
    sid = _request(pg_admin, "teams")
    await p._send_requested()
    assert _rows(pg_admin)[sid]["status"] == "pending" and sinks["teams"].got == []


def test_alert_from_row_survives_an_unreadable_analysis(pg_dsn):
    store = Store(pg_dsn)
    inc = make_incident()
    store.record(Alert(inc, Decision(notify=True, urgency=2)), ["a"], [], duration_ms=1500)
    row = store.get_incident("inc1")
    row["analysis"] = {"summary": "x", "removed_field": 1}
    alert = alert_from_row(row, window_seconds=20)
    assert alert.analysis is None and alert.decision.urgency == 2
    assert abs(alert.incident.first_seen - (row["created_at"] - 21.5)) < 1e-6


def test_prune_drops_old_send_requests(pg_dsn, pg_admin):
    store = Store(pg_dsn)
    old = _request(pg_admin, "teams", age=40 * 86400)
    new = _request(pg_admin, "teams")
    store.prune(30)
    assert set(_rows(pg_admin)) == {new} and old != new
