"""Core domain objects shared by every stage of the pipeline.

Providers (sources, deciders, analyzers, notifiers) only exchange these
types, which is what keeps them swappable. They are closed contracts: every
source, whatever its raw data, produces a ``LogEvent`` with exactly these
fields, and every channel renders from one ``Alert``. Information the core
should carry is added as a new field here, never as an untyped extra.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import Any

# Version of the published alert shape (``Alert.to_dict``). Adding a field keeps
# it; removing, renaming or retyping one bumps it.
ALERT_SCHEMA_VERSION = 1


class ContractError(ValueError):
    """A value does not have the shape its contract declares."""


def _check(owner: str, name: str, value: Any, kind: type | tuple[type, ...]) -> None:
    # bool is an int subclass; a flag is never a valid number here.
    if not isinstance(value, kind) or (isinstance(value, bool) and bool not in (kind if isinstance(kind, tuple) else (kind,))):
        expected = kind.__name__ if isinstance(kind, type) else " | ".join(k.__name__ for k in kind)
        raise ContractError(f"{owner}.{name} must be {expected}, got {type(value).__name__}: {value!r}"[:200])


def _check_number(owner: str, name: str, value: Any) -> float:
    _check(owner, name, value, (int, float))
    if not math.isfinite(value):
        raise ContractError(f"{owner}.{name} must be a finite number, got {value!r}")
    return float(value)


def _check_strings(owner: str, name: str, value: Any) -> None:
    _check(owner, name, value, list)
    for i, item in enumerate(value):
        _check(owner, f"{name}[{i}]", item, str)


def conform(value: Any, contract: type, producer: str) -> Any:
    """Return ``value`` if it is a valid ``contract``; raise ContractError naming the producer."""
    if not isinstance(value, contract):
        raise ContractError(f"{producer} must produce a {contract.__name__}, got {type(value).__name__}")
    value.validate()
    return value


class Level(IntEnum):
    """Log severity. Only WARN and above are "events" the agent reacts to."""

    TRACE = 5
    DEBUG = 10
    INFO = 20
    WARN = 30
    ERROR = 40
    FATAL = 50
    PANIC = 60

    @classmethod
    def parse(cls, value: str | int | "Level" | None, default: "Level" | None = None) -> "Level":
        if isinstance(value, Level):
            return value
        if isinstance(value, int):
            return cls(value)
        if value is None:
            if default is None:
                raise ValueError("level is required")
            return default
        key = str(value).strip().upper()
        alias = _LEVEL_ALIASES.get(key)
        if alias is not None:
            return alias
        if default is not None:
            return default
        raise ValueError(f"unknown log level: {value!r}")

    @property
    def label(self) -> str:
        return self.name.lower()


_LEVEL_ALIASES: dict[str, Level] = {
    "TRACE": Level.TRACE,
    "DEBUG": Level.DEBUG, "DBG": Level.DEBUG,
    "INFO": Level.INFO, "INF": Level.INFO, "NOTICE": Level.INFO,
    "WARN": Level.WARN, "WARNING": Level.WARN, "WRN": Level.WARN,
    "ERROR": Level.ERROR, "ERR": Level.ERROR, "SEVERE": Level.ERROR,
    "FATAL": Level.FATAL, "CRITICAL": Level.FATAL, "CRIT": Level.FATAL,
    "ALERT": Level.FATAL, "EMERG": Level.PANIC, "EMERGENCY": Level.PANIC,
    "PANIC": Level.PANIC, "DPANIC": Level.PANIC,
}

# The predefined event types the agent alerts on.
EVENT_LEVELS: tuple[Level, ...] = (Level.WARN, Level.ERROR, Level.FATAL, Level.PANIC)


@dataclass
class LogEvent:
    """A single normalized log line: the one shape every source delivers to the core.

    Sources map their native data (Loki labels, JSON keys, syslog headers) onto
    these fields; anything else stays in ``raw``. Checked on construction and
    again where it enters the pipeline, since a source may still set fields after
    parsing.
    """

    message: str
    level: Level
    source: str                      # name of the configured source
    host: str = ""
    service: str = ""
    project: str = ""                 # empty: the core uses the source name
    timestamp: float = field(default_factory=time.time)
    raw: str = ""
    explicit_level: bool = True       # False when the level was only a default guess

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        for name in ("message", "source", "host", "service", "project", "raw"):
            _check("LogEvent", name, getattr(self, name), str)
        if not self.source:
            raise ContractError("LogEvent.source must name the configured source")
        _check("LogEvent", "level", self.level, Level)
        self.timestamp = _check_number("LogEvent", "timestamp", self.timestamp)
        _check("LogEvent", "explicit_level", self.explicit_level, bool)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["level"] = self.level.label
        return d


@dataclass
class Incident:
    """A group of similar events (same fingerprint) seen inside a window."""

    fingerprint: str
    level: Level                      # highest level seen
    source: str
    host: str
    service: str
    title: str                        # representative message
    first_seen: float
    last_seen: float
    count: int = 1
    samples: list[LogEvent] = field(default_factory=list)      # matching lines
    context: list[LogEvent] = field(default_factory=list)      # surrounding lines (any level)
    id: str = ""
    project: str = ""                 # the events' `project` (set by their source) → source name
    steps: list[dict[str, Any]] = field(default_factory=list)  # ordered trace, persisted by the store

    def to_state(self, max_samples: int = 5, max_context: int = 20) -> dict[str, Any]:
        """Compact, provider-neutral view used as Jev state / LLM input."""
        return {
            "level": self.level.label,
            "source": self.source,
            "host": self.host,
            "service": self.service,
            "message": self.title,
            "occurrences": self.count,
            "window_seconds": round(self.last_seen - self.first_seen, 1),
            "samples": [e.raw or e.message for e in self.samples[:max_samples]],
            "context": [e.raw or e.message for e in self.context[-max_context:]],
        }


@dataclass
class Decision:
    """Output of a Decider: should we bother a human, and how urgently."""

    notify: bool
    urgency: int = 0                  # 0 = ignore … 3 = page now
    category: str = "unknown"
    confidence: float = 0.0
    reason: str = ""
    provider: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _check("Decision", "notify", self.notify, bool)
        _check("Decision", "urgency", self.urgency, int)
        if not 0 <= self.urgency < len(URGENCY_LABELS):
            raise ContractError(f"Decision.urgency must be 0..{len(URGENCY_LABELS) - 1}, got {self.urgency}")
        for name in ("category", "reason", "provider"):
            _check("Decision", name, getattr(self, name), str)
        self.confidence = _check_number("Decision", "confidence", self.confidence)
        _check("Decision", "raw", self.raw, dict)


@dataclass
class Analysis:
    """Output of an Analyzer: the "thinking" part — cause and advice."""

    summary: str
    probable_causes: list[str] = field(default_factory=list)
    impact: str = ""
    recommended_actions: list[str] = field(default_factory=list)
    diagnostic_commands: list[str] = field(default_factory=list)
    confidence: float = 0.0
    provider: str = ""

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        for name in ("summary", "impact", "provider"):
            _check("Analysis", name, getattr(self, name), str)
        for name in ("probable_causes", "recommended_actions", "diagnostic_commands"):
            _check_strings("Analysis", name, getattr(self, name))
        self.confidence = _check_number("Analysis", "confidence", self.confidence)


@dataclass
class Alert:
    """What notifiers receive: the incident plus everything we concluded.

    The one shape every channel renders from. A channel that hands the alert to
    another system sends ``to_dict()``, never its own selection of fields.
    """

    incident: Incident
    decision: Decision
    analysis: Analysis | None = None
    link: str = ""                    # where a person opens this incident (portal ticket); "" when none

    def to_dict(self) -> dict[str, Any]:
        """The published alert, versioned by ``ALERT_SCHEMA_VERSION``; internal
        state (the decision trace, the provider's raw response) stays out."""
        inc = self.incident
        return {
            "schema_version": ALERT_SCHEMA_VERSION,
            "link": self.link,
            "incident": {
                "id": inc.id, "fingerprint": inc.fingerprint, "level": inc.level.label,
                "source": inc.source, "project": inc.project or inc.source, "host": inc.host,
                "service": inc.service, "title": inc.title, "count": inc.count,
                "first_seen": inc.first_seen, "last_seen": inc.last_seen, "state": inc.to_state(),
            },
            "decision": {k: v for k, v in asdict(self.decision).items() if k != "raw"},
            "analysis": asdict(self.analysis) if self.analysis else None,
        }


URGENCY_LABELS = ("ignore", "investigate soon", "act within the hour", "page now")

# Final result of one incident, as stored and shown by the dashboard.
OUTCOMES = ("notified", "not_notified", "rate_limited", "suppressed", "delivery_failed", "no_route")

# Ticket status of a fingerprint group, in board column order. A fingerprint with
# no stored status is "new". Which moves are allowed is the portal's rule
# (dashboard/worker/queries.ts).
TICKET_STATUSES = ("new", "inprogress", "cancel", "review", "done")
