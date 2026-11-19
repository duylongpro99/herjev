"""The agent loop: sources → aggregate → decide (Jev) → analyze (LLM) → notify."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .aggregator import Aggregator
from .config import channel_auto, channel_enabled, public_url
from .analyzers.base import Analyzer
from .deciders.base import Decider
from .formatting import attach_link, render_text
from .models import (URGENCY_LABELS, Alert, Analysis, ContractError, Decision, Incident, Level, LogEvent,
                     conform)
from .notifiers.base import Notifier
from .paths import herjev_home
from .registry import create
from .sources.base import Source
from .store import Store, alert_from_row
from .trace import current_incident, current_steps, make_step, step

log = logging.getLogger(__name__)

# A send asked for in the portal that the watcher has not finished by then expires.
SEND_TTL_SECONDS = 600


def _urgency_label(urgency: Any) -> str:
    try:
        return URGENCY_LABELS[urgency]
    except (IndexError, TypeError):
        return "unknown"


@dataclass
class Route:
    channels: list[str]
    levels: set[Level] | None = None
    min_urgency: int = 0
    sources: set[str] | None = None
    categories: set[str] | None = None

    @classmethod
    def from_config(cls, d: dict[str, Any]) -> "Route":
        m = d.get("match", {}) or {}
        return cls(
            channels=list(d["channels"]),
            levels={Level.parse(x) for x in m["levels"]} if m.get("levels") else None,
            min_urgency=int(m.get("min_urgency", 0)),
            sources=set(m["sources"]) if m.get("sources") else None,
            categories=set(m["categories"]) if m.get("categories") else None,
        )

    def matches(self, alert: Alert) -> bool:
        inc, dec = alert.incident, alert.decision
        return ((self.levels is None or inc.level in self.levels)
                and dec.urgency >= self.min_urgency
                and (self.sources is None or inc.source in self.sources)
                and (self.categories is None or dec.category in self.categories))


@dataclass
class Stats:
    started_at: float = field(default_factory=time.time)
    events: Counter = field(default_factory=Counter)          # by level
    events_by_source: Counter = field(default_factory=Counter)
    rejected_by_source: Counter = field(default_factory=Counter)  # events that broke the LogEvent contract
    incidents: int = 0
    alerts_sent: int = 0
    suppressed: int = 0
    errors: deque = field(default_factory=lambda: deque(maxlen=20))

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "uptime_seconds": round(time.time() - self.started_at),
            "events_by_level": dict(self.events),
            "events_by_source": dict(self.events_by_source),
            "rejected_by_source": dict(self.rejected_by_source),
            "incidents": self.incidents,
            "alerts_sent": self.alerts_sent,
            "suppressed": self.suppressed,
            "recent_errors": list(self.errors),
        }


def build_notifier(name: str, spec: dict[str, Any]) -> Notifier:
    """Build a channel. The ``open:`` link reaches it on the alert (``Alert.link``, from
    ``dashboard.public_url``), so a stale per-channel ``link_base`` key is ignored."""
    spec = {k: v for k, v in spec.items() if k not in ("link_base", "enabled", "auto")}
    return create("notifier", spec, name=name)


class Pipeline:
    def __init__(self, sources: list[Source], decider: Decider, analyzer: Analyzer | None,
                 channels: dict[str, Notifier], routes: list[Route], aggregator: Aggregator,
                 store: Store | None = None, max_alerts_per_hour: int = 60,
                 analyze_min_urgency: int = 1, concurrency: int = 4,
                 status_file: str | Path | None = None, dry_run: bool = False,
                 store_retention_days: float = 30, link_base: str | None = None,
                 manual_channels: set[str] | None = None):
        self.sources = sources
        self.decider = decider
        self.analyzer = analyzer
        self.channels = channels
        self.routes = routes
        self.aggregator = aggregator
        self.store = store
        self.max_alerts_per_hour = max_alerts_per_hour
        self.analyze_min_urgency = analyze_min_urgency
        self.dry_run = dry_run
        self.store_retention_days = store_retention_days
        self.link_base = link_base
        # Built channels that no route reaches: only a send from the portal goes to them.
        self.manual_channels = manual_channels or set()
        self.stats = Stats()
        self.status_file = Path(status_file).expanduser() if status_file else None
        self._sem = asyncio.Semaphore(concurrency)
        self._sent_times: deque[float] = deque()
        self._inflight: set[asyncio.Task] = set()
        self._status_task: asyncio.Task | None = None
        self._store_sync: asyncio.Task | None = None
        self._send_sync: asyncio.Task | None = None
        self._mutes_synced = 0.0
        self._pruned = 0.0
        self._sends_synced = 0.0
        self._choices_synced = 0.0
        for r in routes:
            for ch in r.channels:
                if ch not in channels:
                    raise ValueError(f"route references unknown channel {ch!r}")

    # ------------------------------------------------------------------ build
    @classmethod
    def from_config(cls, cfg: dict[str, Any], dry_run: bool = False) -> "Pipeline":
        p = cfg.get("pipeline", {})
        sources = [create("source", s) for s in cfg["sources"]]
        names = [s.name for s in sources]
        if len(set(names)) != len(names):
            raise ValueError("source names must be unique")
        decider = create("decider", cfg.get("decider") or {"type": "rules"})
        an_cfg = cfg.get("analyzer") or {"type": "none"}
        analyzer = None if an_cfg.get("type") == "none" else create("analyzer", an_cfg)
        link_base = public_url(cfg)
        channels = {name: build_notifier(name, spec) for name, spec in cfg["channels"].items()
                    if channel_enabled(name, spec)}
        for name in cfg["channels"].keys() - channels.keys():
            log.info("channel %s is disabled; no alerts go to it", name)
        manual = {name for name in channels if not channel_auto(name, cfg["channels"][name])}
        for name in sorted(manual):
            log.info("channel %s has auto: false; it only receives sends from the portal", name)
        if cfg.get("routes"):
            routes = []
            for r in map(Route.from_config, cfg["routes"]):
                for ch in r.channels:
                    if ch not in cfg["channels"]:
                        raise ValueError(f"route references unknown channel {ch!r}")
                # A route left with only disabled or manual channels is dropped; it must
                # not fall back to the default route, which would send everywhere.
                r.channels = [ch for ch in r.channels if ch in channels and ch not in manual]
                if r.channels:
                    routes.append(r)
        else:
            auto = [ch for ch in channels if ch not in manual]
            routes = [Route(channels=auto)] if auto else []
        agg = Aggregator(
            window_seconds=float(p.get("window_seconds", 20)),
            cooldown_seconds=float(p.get("cooldown_seconds", 600)),
            context_lines=int(p.get("context_lines", 30)),
            min_level=Level.parse(p.get("min_level", "warn")),
        )
        store = None
        if p.get("store") is not False:
            if not (p.get("store_dsn") or os.environ.get("HERJEV_DATABASE_URL")):
                raise ValueError("incident store: no database; set pipeline.store_dsn or HERJEV_DATABASE_URL, "
                                 "or pipeline.store: false to run without history")
            try:
                store = Store(p.get("store_dsn"))
            except Exception as exc:  # never let an unreachable database stop alerting
                log.error("incident store unavailable (%s); continuing without history", exc)
        return cls(sources, decider, analyzer, channels, routes, agg, store=store,
                   max_alerts_per_hour=int(p.get("max_alerts_per_hour", 60)),
                   analyze_min_urgency=int(p.get("analyze_min_urgency", 1)),
                   concurrency=int(p.get("concurrency", 4)),
                   status_file=p.get("status_file") or herjev_home() / "status.json",
                   dry_run=dry_run,
                   store_retention_days=float(p.get("store_retention_days", 30)),
                   link_base=link_base, manual_channels=manual)

    # ------------------------------------------------------------------ run
    async def run(self, duration: float | None = None) -> None:
        tasks = [asyncio.create_task(self._consume(s), name=f"source:{s.name}") for s in self.sources]
        tasks.append(asyncio.create_task(self._flush_loop(), name="flusher"))
        log.info("herjev watching %d source(s): %s", len(self.sources),
                 ", ".join(s.describe() for s in self.sources))
        try:
            if duration:
                await asyncio.sleep(duration)
            else:
                await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.drain()
            await self.aclose()

    async def _sync_store(self) -> None:
        # Mutes are set by people through the CLI, so a few seconds of lag is fine, and
        # polling less keeps the VM's egress inside the free tier.
        if time.time() - self._mutes_synced >= 5:
            self._mutes_synced = time.time()
            await self._apply_store_mutes()
        if time.time() - self._choices_synced >= 15:
            self._choices_synced = time.time()
            await self._apply_service_choices()
        await self._record_suppressed()
        if time.time() - self._pruned > 3600:
            self._pruned = time.time()
            await self._prune_store()

    async def drain(self) -> None:
        for task in (self._store_sync, self._send_sync):
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
        for inc in self.aggregator.flush(force=True):
            self._spawn(inc)
        await self._record_suppressed()
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)
        if self._status_task is not None:
            await self._status_task
        self._write_status()
        if self._status_task is not None:
            await self._status_task

    async def _consume(self, source: Source) -> None:
        while True:
            try:
                async for ev in source.stream():
                    try:
                        self.ingest(ev)
                    except ContractError as exc:  # one malformed event is dropped; the stream goes on
                        self.stats.rejected_by_source[source.name] += 1
                        self._error(f"source {source.name}: event rejected: {exc}")
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._error(f"source {source.name}: {exc}")
                await source.backoff(exc)

    def ingest(self, ev: LogEvent) -> None:
        """The core's entrance: only a valid LogEvent gets past here (else ContractError)."""
        conform(ev, LogEvent, "source")
        self.stats.events[ev.level.label] += 1
        self.stats.events_by_source[ev.source] += 1
        self.aggregator.add(ev)

    async def _flush_loop(self) -> None:
        last_status = 0.0
        while True:
            await asyncio.sleep(1.0)
            for inc in self.aggregator.flush():
                self._spawn(inc)
            # The database is remote and may be slow or down: sync mutes, record suppressed
            # incidents and prune beside the flush, never in front of it, one sync at a time.
            if self._store_sync is None or self._store_sync.done():
                self._store_sync = asyncio.create_task(self._sync_store())
            # Portal sends wait on channels (a Teams call can take 30 s), so they get their own
            # task: a slow one must not hold back mutes and suppressed-incident records.
            if (time.time() - self._sends_synced >= 5
                    and (self._send_sync is None or self._send_sync.done())):
                self._sends_synced = time.time()
                self._send_sync = asyncio.create_task(self._send_requested())
            if time.time() - last_status > 10:
                self._write_status()
                last_status = time.time()

    def _spawn(self, inc: Incident) -> None:
        t = asyncio.create_task(self.process(inc))
        self._inflight.add(t)
        t.add_done_callback(self._inflight.discard)
        t.add_done_callback(self._report_failure)

    def _report_failure(self, task: asyncio.Task) -> None:
        # An incident that failed outside the guarded stages must not vanish silently.
        if not task.cancelled() and task.exception() is not None:
            self._error(f"incident processing failed: {task.exception()!r}")

    # ------------------------------------------------------------------ core
    async def process(self, incident: Incident) -> Alert | None:
        """Decide → analyze → route → deliver → record. Returns the alert if sent."""
        async with self._sem:
            current_incident.set(incident.id)
            incident.steps = []
            current_steps.set(incident.steps)
            started = time.monotonic()
            self.stats.incidents += 1
            step("aggregate", "%s x%d from %s over %.1fs, fingerprint %s, %d sample(s), %d context line(s)",
                 incident.level.label, incident.count, incident.source,
                 incident.last_seen - incident.first_seen, incident.fingerprint,
                 len(incident.samples), len(incident.context), body=incident.title)
            try:
                decision = conform(await self.decider.decide(incident), Decision, "decider")
            except Exception as exc:
                self._error(f"decider: {exc}")
                decision = self._fallback_decision(incident, exc)
            alert = attach_link(Alert(incident, decision), self.link_base)
            log.info("incident %s [%s x%d] %s → %s", incident.id, incident.level.label,
                     incident.count, incident.title[:80], decision.reason)
            step("decision", "notify=%s urgency=%d (%s) category=%s confidence=%.2f provider=%s",
                 decision.notify, decision.urgency, _urgency_label(decision.urgency), decision.category,
                 decision.confidence, decision.provider)
            if not decision.notify:
                step("outcome", "not notified: decider verdict is notify=False (recorded in history only)")
                await self._record(alert, [], [], "not_notified", started)
                return None
            if self._rate_limited():
                step("outcome", "not notified: rate limit of %d alerts/hour reached", self.max_alerts_per_hour)
                self.stats.suppressed += 1
                await self._record(alert, [], ["rate limited"], "rate_limited", started)
                return None
            # Reserve the rate-limit slot now: concurrent incidents must see it.
            slot = time.time()
            self._sent_times.append(slot)
            if not self.analyzer:
                step("analyze", "skipped: no analyzer configured")
            elif decision.urgency < self.analyze_min_urgency:
                step("analyze", "skipped: urgency %d below analyze_min_urgency %d",
                     decision.urgency, self.analyze_min_urgency)
            else:
                try:
                    alert.analysis = conform(await self.analyzer.analyze(incident, decision), Analysis, "analyzer")
                except Exception as exc:
                    self._error(f"analyzer: {exc}")
                    step("analyze", "failed, alert goes out without analysis: %s", exc)
            targets = self.route(alert)
            step("route", "level=%s urgency=%d source=%s category=%s → %s", incident.level.label,
                 decision.urgency, incident.source, decision.category,
                 ", ".join(targets) if targets else "no matching route")
            errors = await self._deliver(alert, targets)
            step("deliver", "%d/%d channel(s) ok%s", len(targets) - len(errors), len(targets),
                 "" if not errors else "; " + "; ".join(errors))
            if len(errors) < len(targets) or not targets:
                self.aggregator.mark_alerted(incident)
            if targets and len(errors) < len(targets):
                self.stats.alerts_sent += 1
            else:
                try:
                    self._sent_times.remove(slot)  # nothing delivered: release the slot
                except ValueError:
                    pass
            outcome = ("no_route" if not targets else
                       "delivery_failed" if len(errors) >= len(targets) else "notified")
            await self._record(alert, targets, errors, outcome, started)
            return alert

    def route(self, alert: Alert) -> list[str]:
        out: list[str] = []
        for r in self.routes:
            if r.matches(alert):
                out += [c for c in r.channels if c not in out]
        return out

    async def _deliver(self, alert: Alert, targets: list[str]) -> list[str]:
        if self.dry_run:
            print(f"[dry-run → {', '.join(targets) or 'no channel'}]\n{render_text(alert)}\n")
            return []
        results = await asyncio.gather(*(self.channels[t].send(alert) for t in targets),
                                       return_exceptions=True)
        errors = []
        for t, res in zip(targets, results):
            if isinstance(res, BaseException):
                msg = f"channel {t}: {res}"
                self._error(msg)
                errors.append(msg)
        return errors

    @staticmethod
    def _fallback_decision(incident: Incident, exc: Exception) -> Decision:
        # Decider completely unavailable: never silently drop serious events.
        notify = incident.level >= Level.ERROR
        urgency = {Level.PANIC: 3, Level.FATAL: 2, Level.ERROR: 1}.get(incident.level, 0)
        return Decision(notify=notify, urgency=urgency, reason=f"decider unavailable ({exc}); level-based fallback",
                        provider="fallback")

    def _rate_limited(self) -> bool:
        now = time.time()
        while self._sent_times and now - self._sent_times[0] > 3600:
            self._sent_times.popleft()
        return len(self._sent_times) >= self.max_alerts_per_hour

    # Store calls run in a worker thread: a slow or busy database (e.g. a long
    # dashboard read) must never stall the event loop that feeds every source.
    # That includes reads, which wait on the same lock a blocked write holds.
    async def _apply_store_mutes(self) -> None:
        if not self.store:
            return
        try:
            mutes = await asyncio.to_thread(self.store.active_mutes)
        except Exception as exc:
            log.debug("mute sync failed: %s", exc)
            return
        for fp, until in mutes.items():
            self.aggregator.mute(fp, until - time.time())

    async def _apply_service_choices(self) -> None:
        """Apply the services people chose or renamed in the portal to their sources."""
        choosers = [s for s in self.sources if s.catalog() is not None]
        if not self.store or not choosers:
            return
        try:
            rows = await asyncio.to_thread(self.store.source_services)
        except Exception as exc:
            log.warning("service choices sync failed: %s", exc)
            return
        for source in choosers:
            try:
                source.apply_choices([r for r in rows if r["source"] == source.name])
            except Exception as exc:
                self._error(f"source {source.name}: portal service choices: {exc}")

    async def _record(self, alert: Alert, channels: list[str], errors: list[str], outcome: str,
                      started: float | None = None) -> None:
        if self.store:
            duration_ms = None if started is None else (time.monotonic() - started) * 1000
            try:
                await asyncio.to_thread(self.store.record, alert, channels, errors,
                                        outcome=outcome, duration_ms=duration_ms)
            except Exception as exc:
                log.warning("failed to record incident: %s", exc)

    async def _record_suppressed(self) -> None:
        """Keep cooldown-suppressed incidents in history; the decider is never called for them."""
        for inc, reason, cycle_start in self.aggregator.pop_suppressed():
            if not self.store:
                continue
            inc.steps = [make_step("aggregate", reason) | {"seq": 1}]
            decision = Decision(notify=False, reason=reason, provider="cooldown")
            try:
                await asyncio.to_thread(self.store.record_suppressed, Alert(inc, decision),
                                        min(cycle_start, time.time()))
            except Exception as exc:
                log.warning("failed to record suppressed incident: %s", exc)

    async def _send_requested(self) -> None:
        """Deliver the sends people asked for in the portal. They bypass the decider,
        routes, rate limit and cooldown; a request older than ``SEND_TTL_SECONDS`` expires
        instead of reaching the channel long after it was asked for.

        A dry run leaves the queue alone: it would mark real requests sent without
        sending them. Only requests for this watcher's own channels are claimed, so a
        watcher with other channels on the same database never takes them."""
        if not self.store or self.dry_run:
            return
        try:
            claimed = await asyncio.to_thread(self.store.take_sends, list(self.channels), SEND_TTL_SECONDS)
        except Exception as exc:
            log.warning("send queue sync failed: %s", exc)
            return
        await asyncio.gather(*(self._send_one(req) for req in claimed))

    async def _send_one(self, req: dict[str, Any]) -> None:
        error = None
        try:
            ch = self.channels[req["channel"]]  # only this watcher's channels are claimed
            row = await asyncio.to_thread(self.store.get_incident, req["incident_id"])
            if row is None:
                raise LookupError(f"incident {req['incident_id']} is no longer stored")
            if row["fingerprint"] != req["fingerprint"]:
                raise ValueError(f"incident {req['incident_id']} is not part of ticket {req['fingerprint']}")
            alert = attach_link(alert_from_row(row, self.aggregator.window_seconds), self.link_base)
            await ch.send(alert)
            log.info("ticket %s sent to channel %s from the portal", req["fingerprint"], ch.name)
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            self._error(f"portal send to channel {req['channel']}: {error}")
        try:
            await asyncio.to_thread(self.store.finish_send, req["id"], error)
        except Exception as exc:
            log.warning("failed to record the result of send %s: %s", req["id"], exc)

    async def _prune_store(self) -> None:
        if not self.store or self.store_retention_days <= 0:
            return
        try:
            n = await asyncio.to_thread(self.store.prune, self.store_retention_days)
            if n:
                log.info("incident store: removed %d incident(s) older than %g day(s)", n,
                         self.store_retention_days)
        except Exception as exc:
            log.warning("incident store prune failed: %s", exc)

    def _error(self, msg: str) -> None:
        log.warning(msg)
        self.stats.errors.append({"at": time.time(), "error": msg})

    def _write_status(self) -> None:
        # The portal lists these channels for manual sends, so it needs every built one.
        channels = [{"name": n, "type": ch.type_name, "auto": n not in self.manual_channels}
                    for n, ch in self.channels.items()]
        # HERJEV_VERSION is the commit the image was built from; the deploy workflow waits
        # until the portal reports it, which is how it knows the VM picked up a release.
        data = self.stats.to_dict() | {"version": os.environ.get("HERJEV_VERSION") or None,
                                       "sources": [s.describe() for s in self.sources],
                                       "channels": channels, "updated_at": time.time()}
        # The portal lists these for choosing and renaming services; it cannot ask Loki itself.
        catalogs = {s.name: c for s in self.sources if (c := s.catalog()) is not None}
        if catalogs:
            data["services"] = catalogs
        if self.status_file:
            try:
                self.status_file.parent.mkdir(parents=True, exist_ok=True)
                self.status_file.write_text(json.dumps(data, indent=2))
            except OSError as exc:  # pragma: no cover
                log.debug("status write failed: %s", exc)
        if self.store is not None:
            self._publish_status(data)

    def _publish_status(self, data: dict) -> None:
        """Copy the status to the store for the portal, in the background: a slow or
        down database must not stall the loop. A beat is skipped while the last one
        is still in flight; the next one replaces it."""
        if self._status_task is not None and not self._status_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # not inside the event loop
            return

        async def publish() -> None:
            try:
                await asyncio.to_thread(self.store.write_status, data)
            except Exception as exc:
                log.debug("status publish failed: %s", exc)
        self._status_task = loop.create_task(publish(), name="status")

    async def aclose(self) -> None:
        await self.decider.aclose()
        if self.analyzer:
            await self.analyzer.aclose()
        for ch in self.channels.values():
            await ch.aclose()
