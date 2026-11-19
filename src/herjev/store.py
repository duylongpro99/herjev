"""Postgres history of incidents, decisions and advice.

Lets the CLI and the portal answer "what happened last night?" even when the
watcher runs on another machine. The schema lives in ``supabase/migrations``
and is applied by CI (or ``scripts/apply-migrations.sh`` locally); this module
never creates or alters tables.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict
from functools import partial
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .models import Alert, Analysis, ContractError, Decision, Incident, Level, LogEvent

log = logging.getLogger(__name__)

_INCIDENT_COLUMNS = ("id", "fingerprint", "created_at", "level", "source", "host", "service", "title",
                     "count", "notify", "urgency", "category", "decision_reason", "decider", "analysis",
                     "channels", "delivery_errors", "state", "project", "outcome", "decision_raw",
                     "duration_ms")
_UPSERT_INCIDENT = (
    f"INSERT INTO herjev.incidents ({', '.join(_INCIDENT_COLUMNS)}) "
    f"VALUES ({', '.join(f'%({c})s' for c in _INCIDENT_COLUMNS)}) "
    f"ON CONFLICT (id) DO UPDATE SET "
    + ", ".join(f"{c} = excluded.{c}" for c in _INCIDENT_COLUMNS if c != "id"))

_dumps = partial(json.dumps, default=str, ensure_ascii=False)


def _clean(value: Any) -> Any:
    """Drop NUL characters: Postgres text and jsonb cannot hold them, log lines can."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {_clean(k): _clean(v) for k, v in value.items()}
    return value


def _json(value: Any) -> Jsonb | None:
    return None if value is None else Jsonb(_clean(value), dumps=_dumps)


def derive_outcome(notify: bool, channels: list[str], errors: list[str]) -> str:
    if not notify:
        return "not_notified"
    if errors == ["rate limited"]:
        return "rate_limited"
    if not channels:
        return "no_route"
    return "delivery_failed" if len(errors) >= len(channels) else "notified"


def alert_from_row(row: dict[str, Any], window_seconds: float = 0.0) -> Alert:
    """Rebuild the alert of a stored incident, for a send asked for after the fact.

    The row keeps what ``render_text`` and ``Alert.to_dict`` read. It has no first-seen
    time, so that is estimated from when the incident was recorded: its window closed
    ``window_seconds`` (the aggregator's) after the first event, then processing took
    ``duration_ms``."""
    state = row.get("state") or {}
    level = Level.parse(row["level"], Level.ERROR)
    source = row.get("source") or row.get("project") or "unknown"
    first_seen = row["created_at"] - (row.get("duration_ms") or 0) / 1000 - window_seconds
    last_seen = first_seen + float(state.get("window_seconds") or 0)

    def events(lines: Any, lvl: Level) -> list[LogEvent]:
        return [LogEvent(message=str(x), level=lvl, source=source, raw=str(x)) for x in lines or []]

    incident = Incident(
        fingerprint=row["fingerprint"], level=level, source=source, host=row.get("host") or "",
        service=row.get("service") or "", title=row.get("title") or "", first_seen=first_seen,
        last_seen=last_seen, count=row.get("count") or 1, samples=events(state.get("samples"), level),
        # Context lines keep no level of their own in the row.
        context=events(state.get("context"), Level.INFO), id=row["id"], project=row.get("project") or "")
    decision = Decision(notify=bool(row.get("notify")), urgency=row.get("urgency") or 0,
                        category=row.get("category") or "unknown", reason=row.get("decision_reason") or "",
                        provider=row.get("decider") or "")
    analysis = None
    if row.get("analysis"):
        try:
            analysis = Analysis(**row["analysis"])
        except (TypeError, ContractError) as exc:  # a row from an older shape still goes out
            log.warning("incident %s: stored analysis unreadable (%s); sending without it", row["id"], exc)
    return Alert(incident, decision, analysis)


class Store:
    """``dsn`` defaults to ``$HERJEV_DATABASE_URL``.

    Safe to share across threads: the pipeline calls it through ``asyncio.to_thread``.
    The pool checks a connection before lending it, so one the server or the Supabase
    pooler closed is replaced instead of failing the next write."""

    def __init__(self, dsn: str | None = None, timeout: float = 10.0):
        dsn = dsn or os.environ.get("HERJEV_DATABASE_URL")
        if not dsn:
            raise ValueError("incident store: no database; set pipeline.store_dsn or HERJEV_DATABASE_URL")
        # No prepared statements: a transaction-mode pooler cannot keep them between transactions.
        self._pool = ConnectionPool(
            dsn, min_size=1, max_size=2, timeout=timeout, open=False, name="herjev-store",
            check=ConnectionPool.check_connection,
            kwargs={"prepare_threshold": None, "row_factory": dict_row, "connect_timeout": int(timeout)})
        try:
            self._pool.open(wait=True, timeout=timeout)
            with self._pool.connection() as conn:
                found = conn.execute("SELECT to_regclass('herjev.tickets') AS t").fetchone()
            if found is None or found["t"] is None:
                raise RuntimeError("incident store: schema missing; apply supabase/migrations first")
        except BaseException:
            self._pool.close()
            raise

    def record(self, alert: Alert, channels: list[str], errors: list[str],
               outcome: str | None = None, duration_ms: float | None = None) -> None:
        """Write the incident and its trace steps in one transaction."""
        inc, dec, ana = alert.incident, alert.decision, alert.analysis
        now = time.time()
        row = {
            "id": inc.id, "fingerprint": inc.fingerprint, "created_at": now, "level": inc.level.label,
            "source": inc.source, "host": inc.host, "service": inc.service, "title": inc.title,
            "count": inc.count, "notify": bool(dec.notify), "urgency": dec.urgency,
            "category": dec.category, "decision_reason": dec.reason, "decider": dec.provider,
            "analysis": _json(asdict(ana) if ana else None), "channels": _json(channels),
            "delivery_errors": _json(errors), "state": _json(inc.to_state()),
            "project": inc.project or inc.source,
            "outcome": outcome or derive_outcome(dec.notify, channels, errors),
            "decision_raw": _json(dec.raw or None),
            "duration_ms": None if duration_ms is None else round(duration_ms, 1),
        }
        row = {k: _clean(v) if isinstance(v, str) else v for k, v in row.items()}
        steps = [(inc.id, st.get("seq", i), st.get("at"), _clean(st.get("stage")), _clean(st.get("message")),
                  _clean(st.get("body")), st.get("elapsed_ms"), st.get("blob"))
                 for i, st in enumerate(inc.steps, 1)]
        blobs = [(st["blob"], _clean(st.get("blob_body"))) for st in inc.steps if st.get("blob")]
        with self._pool.connection() as conn:
            conn.execute(_UPSERT_INCIDENT, row)
            conn.execute("DELETE FROM herjev.incident_steps WHERE incident_id = %s", (inc.id,))
            with conn.cursor() as cur:
                if blobs:
                    cur.executemany("INSERT INTO herjev.trace_blobs (key, body) VALUES (%s, %s) "
                                    "ON CONFLICT (key) DO NOTHING", blobs)
                if steps:
                    cur.executemany(
                        "INSERT INTO herjev.incident_steps "
                        "(incident_id, seq, at, stage, message, body, elapsed_ms, blob) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", steps)
            # The error is back: a Done ticket reopens. Cancel stays cancelled.
            conn.execute("UPDATE herjev.tickets SET status = 'new', updated_at = %s, reopened_at = %s "
                         "WHERE fingerprint = %s AND status = 'done'", (now, now, inc.fingerprint))

    def record_suppressed(self, alert: Alert, since: float) -> None:
        """Cooldown repeats of one fingerprint share a single row per cooldown period.

        A crash loop would otherwise add a full row every aggregation window. The
        existing row since ``since`` (when the fingerprint was last alerted) gets the
        new occurrences added and its latest sample, state and reason.
        """
        inc = alert.incident
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT id, count FROM herjev.incidents WHERE fingerprint = %s AND outcome = 'suppressed' "
                "AND created_at >= %s ORDER BY created_at DESC LIMIT 1", (inc.fingerprint, since)).fetchone()
        if row is not None:
            inc.id, inc.count = row["id"], (row["count"] or 0) + inc.count
        self.record(alert, [], [], outcome="suppressed")

    def prune(self, older_than_days: float) -> int:
        """Delete incidents (and their steps) older than the retention window."""
        cutoff = time.time() - older_than_days * 86400
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM herjev.incident_steps s USING herjev.incidents i "
                         "WHERE s.incident_id = i.id AND i.created_at < %s", (cutoff,))
            n = conn.execute("DELETE FROM herjev.incidents WHERE created_at < %s", (cutoff,)).rowcount
            conn.execute("DELETE FROM herjev.trace_blobs b WHERE NOT EXISTS "
                         "(SELECT 1 FROM herjev.incident_steps s WHERE s.blob = b.key)")
            conn.execute("DELETE FROM herjev.channel_sends WHERE requested_at < %s", (cutoff,))
            conn.execute("DELETE FROM herjev.tickets t WHERE NOT EXISTS "
                         "(SELECT 1 FROM herjev.incidents i WHERE i.fingerprint = t.fingerprint)")
        return n

    def recent(self, limit: int = 20, since_hours: float | None = None,
               notified_only: bool = False, min_level: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM herjev.incidents WHERE true"
        args: list[Any] = []
        if since_hours:
            q += " AND created_at >= %s"
            args.append(time.time() - since_hours * 3600)
        if notified_only:
            q += " AND notify"
        if min_level:
            from .models import Level
            q += " AND level = ANY(%s)"
            args.append([l.label for l in Level if l >= Level.parse(min_level)])
        q += " ORDER BY created_at DESC LIMIT %s"
        args.append(limit)
        with self._pool.connection() as conn:
            return conn.execute(q, args).fetchall()

    def get(self, incident_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            return conn.execute("SELECT * FROM herjev.incidents WHERE id = %s OR fingerprint = %s "
                                "ORDER BY created_at DESC LIMIT 1", (incident_id, incident_id)).fetchone()

    def mute(self, fingerprint: str, seconds: float, reason: str = "") -> None:
        with self._pool.connection() as conn:
            conn.execute("INSERT INTO herjev.mutes (fingerprint, until, reason) VALUES (%s, %s, %s) "
                         "ON CONFLICT (fingerprint) DO UPDATE SET until = excluded.until, reason = excluded.reason",
                         (fingerprint, time.time() + seconds, reason))

    def active_mutes(self) -> dict[str, float]:
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT fingerprint, until FROM herjev.mutes WHERE until > %s",
                                (time.time(),)).fetchall()
        return {r["fingerprint"]: r["until"] for r in rows}

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            return conn.execute("SELECT * FROM herjev.incidents WHERE id = %s", (incident_id,)).fetchone()

    def take_sends(self, channels: list[str], expire_after: float, limit: int = 20) -> list[dict[str, Any]]:
        """Expire portal sends still open after ``expire_after`` seconds, then mark the
        oldest pending ones for ``channels`` ``sending`` and return them, in one round trip.
        Rows another watcher is claiming are skipped, so no send is delivered twice."""
        now = time.time()
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE herjev.channel_sends SET status = 'expired', finished_at = %s, "
                "error = CASE status WHEN 'sending' THEN 'the watcher stopped before the send finished' "
                "ELSE 'the watcher did not pick it up in time' END "
                "WHERE status IN ('pending', 'sending') AND requested_at < %s", (now, now - expire_after))
            return conn.execute(
                "UPDATE herjev.channel_sends SET status = 'sending' WHERE id IN ("
                "SELECT id FROM herjev.channel_sends WHERE status = 'pending' AND channel = ANY(%s) "
                "ORDER BY requested_at, id LIMIT %s FOR UPDATE SKIP LOCKED) "
                "RETURNING id, fingerprint, incident_id, channel, requested_at", (channels, limit)).fetchall()

    def finish_send(self, send_id: int, error: str | None) -> None:
        with self._pool.connection() as conn:
            conn.execute("UPDATE herjev.channel_sends SET status = %s, error = %s, finished_at = %s WHERE id = %s",
                         ("failed" if error else "sent", _clean(error), time.time(), send_id))

    def source_services(self) -> list[dict[str, Any]]:
        """The services people chose or renamed in the portal, for every source."""
        with self._pool.connection() as conn:
            return conn.execute("SELECT source, label, watched, display_name FROM herjev.source_services "
                                "ORDER BY source, label").fetchall()

    def write_status(self, body: dict[str, Any]) -> None:
        """Publish the watcher status for the portal, which cannot read status.json."""
        with self._pool.connection() as conn:
            conn.execute("INSERT INTO herjev.watcher_status (id, body, updated_at) VALUES (1, %s, %s) "
                         "ON CONFLICT (id) DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at",
                         (_json(body), time.time()))

    def close(self) -> None:
        self._pool.close()
