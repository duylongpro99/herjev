"""Group similar events into incidents, and suppress repeats.

* Events with the same fingerprint inside ``window_seconds`` become one
  Incident (so a crash-loop spamming 500 lines produces one alert).
* A recent ring buffer of *all* lines per source is attached as context so
  the LLM can see what happened just before the error.
* After an incident is alerted, the same fingerprint is muted for
  ``cooldown_seconds`` unless its level escalates. Muted incidents are kept
  (bounded) with the reason so the pipeline can record them via
  :meth:`Aggregator.pop_suppressed`.
* Continuation lines (stack traces, "goroutine N [running]:") are attached
  to the preceding event rather than treated as new events.
"""

from __future__ import annotations

import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from .models import Incident, Level, LogEvent
from .parsing import fingerprint
from .trace import step

_CONTINUATION_RE = re.compile(r"^(\s+|at |\t|goroutine \d+|Caused by:|\.\.\. \d+ more|File \")")
_TIMESTAMP_START_RE = re.compile(r"^\[?\d{2,4}[-/:]\d{2}")
_MAX_CONTINUATION_LINES = 60
_MAX_SUPPRESSED = 500


@dataclass
class _Pending:
    incident: Incident
    deadline: float


@dataclass
class Aggregator:
    window_seconds: float = 20.0
    cooldown_seconds: float = 600.0
    context_lines: int = 30
    max_samples: int = 10
    min_level: Level = Level.WARN
    clock: callable = field(default=time.time)  # type: ignore[valid-type]

    def __post_init__(self) -> None:
        self._pending: dict[str, _Pending] = {}
        self._last_alert: dict[str, tuple[float, Level]] = {}
        self._context: dict[str, deque[LogEvent]] = {}
        self._last_event: dict[str, LogEvent] = {}
        # When the current cooldown or mute began. A mute back-dates _last_alert into
        # the future, so that time cannot tell where the suppression cycle started.
        self._cycle_start: dict[str, float] = {}
        self._suppressed_list: deque[tuple[Incident, str, float]] = deque(maxlen=_MAX_SUPPRESSED)

    def add(self, event: LogEvent) -> None:
        ctx = self._context.setdefault(event.source, deque(maxlen=self.context_lines))
        # Multi-line: attach continuation to previous event of the same source.
        prev = self._last_event.get(event.source)
        if prev is not None and self._is_continuation(prev, event):
            prev.message += "\n" + event.message
            if prev.raw:
                prev.raw += "\n" + (event.raw or event.message)
            return
        ctx.append(event)
        self._last_event[event.source] = event
        if event.level < self.min_level:
            return
        fp = fingerprint(event)
        now = self.clock()
        pending = self._pending.get(fp)
        if pending is None:
            inc = Incident(
                fingerprint=fp, level=event.level, source=event.source, host=event.host,
                service=event.service, title=event.message.splitlines()[0][:300],
                first_seen=event.timestamp, last_seen=event.timestamp, count=1,
                samples=[event], id=uuid.uuid4().hex[:12], project=self._project(event),
            )
            self._pending[fp] = _Pending(inc, now + self.window_seconds)
        else:
            inc = pending.incident
            inc.count += 1
            inc.last_seen = max(inc.last_seen, event.timestamp)
            if event.level > inc.level:
                inc.level = event.level
            if len(inc.samples) < self.max_samples:
                inc.samples.append(event)

    @staticmethod
    def _project(event: LogEvent) -> str:
        return event.project or event.source

    @staticmethod
    def _is_continuation(prev: LogEvent, event: LogEvent) -> bool:
        if prev.message.count("\n") >= _MAX_CONTINUATION_LINES or event.level > prev.level:
            return False
        text = event.raw or event.message
        if _CONTINUATION_RE.match(text):
            return True
        # Unleveled, untimestamped lines right after an error are stack frames
        # (e.g. Go "main.handler()" / "\t/app/main.go:42").
        return (prev.level >= Level.ERROR and not event.explicit_level
                and not _TIMESTAMP_START_RE.match(text)
                and abs(event.timestamp - prev.timestamp) < 2.0)

    def flush(self, force: bool = False) -> list[Incident]:
        """Return incidents whose window has closed (all of them if ``force``)."""
        now = self.clock()
        ready: list[Incident] = []
        for fp in list(self._pending):
            p = self._pending[fp]
            if not force and p.deadline > now:
                continue
            del self._pending[fp]
            inc = p.incident
            inc.context = list(self._context.get(inc.source, ()))
            if self._suppressed(inc, now):
                at, level = self._last_alert[fp]
                head = f"suppressed {inc.level.label} x{inc.count} {inc.title[:80]!r}: "
                if at > now:
                    reason = head + (f"fingerprint {fp} is muted for another "
                                     f"{at + self.cooldown_seconds - now:.0f}s")
                else:
                    reason = head + (f"same fingerprint {fp} alerted at level {level.label} {now - at:.0f}s "
                                     f"ago (cooldown {self.cooldown_seconds:.0f}s, only a higher level "
                                     f"breaks through)")
                step("aggregate", reason, incident=inc.id)
                self._suppressed_list.append((inc, reason, self._cycle_start.get(fp, at)))
                continue
            ready.append(inc)
        return ready

    def pop_suppressed(self) -> list[tuple[Incident, str, float]]:
        """Incidents held back since the last call: (incident, reason, cycle_start), where
        cycle_start is when the cooldown or mute that holds them back began."""
        out = list(self._suppressed_list)
        self._suppressed_list.clear()
        return out

    def _suppressed(self, inc: Incident, now: float) -> bool:
        last = self._last_alert.get(inc.fingerprint)
        if last is None:
            return False
        at, level = last
        return (now - at) < self.cooldown_seconds and inc.level <= level

    def mark_alerted(self, inc: Incident) -> None:
        now = self.clock()
        self._last_alert[inc.fingerprint] = (now, inc.level)
        self._cycle_start[inc.fingerprint] = now

    def mute(self, fp: str, seconds: float) -> None:
        """Mute a fingerprint (applied from mutes recorded in the store).

        Store mutes are re-applied every second, so a mute that extends a cooldown
        or mute already in force keeps that cycle's start.
        """
        now = self.clock()
        last = self._last_alert.get(fp)
        if last is None or now - last[0] >= self.cooldown_seconds:
            self._cycle_start[fp] = now
        self._last_alert[fp] = (now + seconds - self.cooldown_seconds, Level.PANIC)
