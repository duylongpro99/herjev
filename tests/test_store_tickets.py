import time

import pytest

from herjev.models import Alert, Decision
from herjev.store import Store

from conftest import make_incident


def _record(store, fp="fp1", inc_id="inc1", outcome=None):
    inc = make_incident()
    inc.fingerprint, inc.id = fp, inc_id
    store.record(Alert(inc, Decision(notify=True)), ["a"], [], outcome=outcome)


def _set_status(db, fp, status):
    db.execute("INSERT INTO herjev.tickets (fingerprint, status, updated_at) VALUES (%s, %s, %s) "
               "ON CONFLICT (fingerprint) DO UPDATE SET status = excluded.status", (fp, status, time.time()))


def _ticket(db, fp):
    return db.execute("SELECT * FROM herjev.tickets WHERE fingerprint = %s", (fp,)).fetchone()


@pytest.mark.parametrize("status,expected", [("done", "new"), ("cancel", "cancel"),
                                             ("inprogress", "inprogress"), ("review", "review")])
def test_new_occurrence_reopens_only_done(pg_dsn, pg_admin, status, expected):
    store = Store(pg_dsn)
    _record(store)
    _set_status(pg_admin, "fp1", status)
    _record(store, inc_id="inc2")
    row = _ticket(pg_admin, "fp1")
    assert row["status"] == expected
    assert (row["reopened_at"] is not None) == (status == "done")


def test_suppressed_repeat_reopens_done(pg_dsn, pg_admin):
    store = Store(pg_dsn)
    _record(store)
    _set_status(pg_admin, "fp1", "done")
    inc = make_incident()
    store.record_suppressed(Alert(inc, Decision(notify=False, provider="cooldown")), since=time.time() - 60)
    assert _ticket(pg_admin, "fp1")["status"] == "new"


def test_prune_removes_orphaned_tickets(pg_dsn, pg_admin):
    store = Store(pg_dsn)
    _record(store, fp="old", inc_id="o1")
    _record(store, fp="kept", inc_id="k1")
    _set_status(pg_admin, "old", "review")
    _set_status(pg_admin, "kept", "review")
    pg_admin.execute("UPDATE herjev.incidents SET created_at = %s WHERE id = 'o1'", (time.time() - 40 * 86400,))
    assert store.prune(30) == 1
    assert _ticket(pg_admin, "old") is None and _ticket(pg_admin, "kept") is not None


def test_ticket_status_is_checked_by_the_database(pg_admin):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        _set_status(pg_admin, "fp1", "bogus")

