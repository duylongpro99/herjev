"""The Postgres store against a real server: role privileges, schema check,
reconnects, and values Postgres would otherwise reject."""

import time

import psycopg
import pytest
from psycopg import sql

from herjev.models import Alert, Decision
from herjev.store import Store

from conftest import ADMIN_DSN, make_incident


def _record(store, **kw):
    inc = make_incident(**kw)
    store.record(Alert(inc, Decision(notify=True)), ["a"], [])
    return inc


def test_portal_role_reads_everything_but_only_writes_tickets_and_send_requests(pg_dsn, pg_portal_dsn):
    _record(Store(pg_dsn))
    with psycopg.connect(pg_portal_dsn, autocommit=True) as portal:
        for table in ("incidents", "incident_steps", "trace_blobs", "mutes", "tickets", "watcher_status",
                      "channel_sends", "source_services"):
            portal.execute(sql.SQL("SELECT * FROM herjev.{}").format(sql.Identifier(table)))
        portal.execute("INSERT INTO herjev.tickets (fingerprint, status, updated_at) VALUES ('fp1', 'review', 0)")
        portal.execute("UPDATE herjev.tickets SET status = 'done' WHERE fingerprint = 'fp1'")
        # A send request starts pending; the portal cannot choose or change its status.
        status, at = portal.execute("INSERT INTO herjev.channel_sends (fingerprint, incident_id, channel) "
                                    "VALUES ('fp1', 'inc1', 'teams') RETURNING status, requested_at").fetchone()
        assert status == "pending" and abs(at - time.time()) < 60
        # The portal chooses and renames services; it never removes a row.
        portal.execute("INSERT INTO herjev.source_services (source, label, watched) VALUES ('loki', 'api-1', true)")
        portal.execute("UPDATE herjev.source_services SET display_name = 'api', updated_at = 1 WHERE label = 'api-1'")
        for statement in ("DELETE FROM herjev.incidents", "UPDATE herjev.incidents SET title = 'x'",
                          "INSERT INTO herjev.mutes (fingerprint, until) VALUES ('fp1', 0)",
                          "DELETE FROM herjev.tickets", "CREATE TABLE herjev.extra (id int)",
                          "INSERT INTO herjev.channel_sends (fingerprint, incident_id, channel, status) "
                          "VALUES ('fp1', 'inc1', 'teams', 'sent')",
                          "INSERT INTO herjev.channel_sends (fingerprint, incident_id, channel, requested_at) "
                          "VALUES ('fp1', 'inc1', 'teams', 1e12)",
                          "UPDATE herjev.channel_sends SET status = 'sent'", "DELETE FROM herjev.channel_sends",
                          "DELETE FROM herjev.source_services",
                          "UPDATE herjev.source_services SET label = 'other'"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                portal.execute(statement)
        assert portal.execute("SHOW statement_timeout").fetchone()[0] == "3s"


def test_store_refuses_a_database_without_the_schema(pg_db):
    from conftest import _dsn
    with psycopg.connect(_dsn(pg_db), autocommit=True) as admin:
        admin.execute("DROP SCHEMA herjev CASCADE")
    with pytest.raises(RuntimeError, match="schema missing"):
        Store(_dsn(pg_db))


def test_store_needs_a_dsn(monkeypatch):
    monkeypatch.delenv("HERJEV_DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="HERJEV_DATABASE_URL"):
        Store()


def test_store_recovers_after_the_server_drops_its_connection(pg_dsn, pg_db):
    store = Store(pg_dsn)
    _record(store)
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        killed = admin.execute("SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
                               "WHERE datname = %s AND usename = 'herjev_watcher'", (pg_db,)).fetchone()[0]
    assert killed >= 1
    time.sleep(0.2)
    inc = make_incident()
    inc.id = "inc2"
    store.record(Alert(inc, Decision(notify=True)), ["a"], [])
    assert store.get("inc2")["id"] == "inc2"


def test_nul_characters_are_dropped_not_rejected(pg_dsn, pg_admin):
    store = Store(pg_dsn)
    inc = make_incident(msg="ERROR bad\x00byte")
    inc.steps.append({"seq": 1, "at": time.time(), "stage": "aggregate", "message": "m\x00", "body": "b\x00"})
    store.record(Alert(inc, Decision(notify=True, reason="r\x00")), ["a"], [])
    row = store.get(inc.id)
    assert row["title"] == "ERROR badbyte" and row["decision_reason"] == "r"
    assert "\x00" not in str(row["state"])
    step = pg_admin.execute("SELECT message, body FROM herjev.incident_steps WHERE incident_id = %s",
                            (inc.id,)).fetchone()
    assert step == {"message": "m", "body": "b"}


def test_write_status_keeps_one_row(pg_dsn, pg_admin):
    store = Store(pg_dsn)
    store.write_status({"alerts_sent": 1})
    store.write_status({"alerts_sent": 2})
    rows = pg_admin.execute("SELECT id, body FROM herjev.watcher_status").fetchall()
    assert rows == [{"id": 1, "body": {"alerts_sent": 2}}]


def test_mutes_and_recent_filters(pg_dsn):
    store = Store(pg_dsn)
    _record(store)
    store.mute("fp1", 60, "deploy")
    store.mute("fp1", 120, "longer")
    store.mute("gone", -1)
    mutes = store.active_mutes()
    assert set(mutes) == {"fp1"} and mutes["fp1"] > time.time() + 100
    assert [r["id"] for r in store.recent(notified_only=True, since_hours=1, min_level="error")] == ["inc1"]
    assert store.recent(min_level="fatal") == []


def test_source_services_reads_the_portal_choices(pg_dsn, pg_admin):
    pg_admin.execute("INSERT INTO herjev.source_services (source, label, watched, display_name) VALUES "
                     "('loki', 'b-1', false, 'b'), ('loki', 'a-1', true, NULL)")
    assert Store(pg_dsn).source_services() == [
        {"source": "loki", "label": "a-1", "watched": True, "display_name": None},
        {"source": "loki", "label": "b-1", "watched": False, "display_name": "b"}]
