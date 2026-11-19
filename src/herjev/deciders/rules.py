from __future__ import annotations

import re

from ..models import Decision, Incident, Level
from ..registry import register
from ..trace import step
from .base import Decider

_CATEGORY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("resource_exhaustion", re.compile(r"out of memory|oom|no space left|too many open files|disk full|memory limit", re.I)),
    ("database", re.compile(r"postgres|mysql|sql|deadlock|database|mongo|redis|connection pool", re.I)),
    ("network", re.compile(r"timeout|timed out|connection (refused|reset)|dns|tls|handshake|unreachable|ECONN", re.I)),
    ("security", re.compile(r"unauthori[sz]ed|forbidden|certificate|x509|auth(entication)? failed|invalid token", re.I)),
    ("configuration", re.compile(r"config|missing env|not set|permission denied|no such file", re.I)),
    ("application_bug", re.compile(r"panic|nil pointer|null ?pointer|traceback|exception|segfault|index out of range|undefined is not", re.I)),
]


@register("decider", "rules")
class RulesDecider(Decider):
    """Deterministic, offline decider. Used as a fallback when Jev is
    unreachable, or on its own if you don't want a model in the loop."""

    def __init__(self, warn_min_count: int = 5, error_min_count: int = 1,
                 burst_count: int = 20):
        self.warn_min_count = warn_min_count
        self.error_min_count = error_min_count
        self.burst_count = burst_count

    async def decide(self, incident: Incident) -> Decision:
        lvl, n = incident.level, incident.count
        if lvl >= Level.PANIC:
            urgency, notify = 3, True
        elif lvl >= Level.FATAL:
            urgency, notify = 3 if n >= self.burst_count else 2, True
        elif lvl >= Level.ERROR:
            notify = n >= self.error_min_count
            urgency = 2 if n >= self.burst_count else 1
        else:
            notify = n >= self.warn_min_count
            urgency = 1 if notify else 0
        category, matched = "unknown", ""
        text = incident.title + " " + " ".join(e.message for e in incident.samples[:3])
        for name, pat in _CATEGORY_PATTERNS:
            m = pat.search(text)
            if m:
                category, matched = name, m.group(0)
                break
        step("decide:rules", "%s x%d (thresholds: warn>=%d, error>=%d, burst>=%d) → notify=%s urgency=%d; "
             "category=%s%s", lvl.label, n, self.warn_min_count, self.error_min_count, self.burst_count,
             notify, urgency, category, f" (matched {matched!r})" if matched else " (no pattern matched)")
        return Decision(
            notify=notify, urgency=urgency, category=category, confidence=0.5,
            reason=f"rules: {lvl.label} x{n}", provider="rules",
        )
