"""Turn raw log lines into LogEvents and compute similarity fingerprints.

Handles JSON logs (zap, logrus, bunyan, pino, structlog, GCP/ECS-style) and
plain-text logs (``2024-.. ERROR ...``, ``[warn]``, ``level=error``, Go
``panic:`` and Python tracebacks).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .models import Level, LogEvent

_JSON_LEVEL_KEYS = ("level", "severity", "lvl", "log.level", "loglevel", "levelname", "@l")
_JSON_MSG_KEYS = ("message", "msg", "@m", "@mt", "text", "log", "error")
_JSON_HOST_KEYS = ("host", "hostname", "host.name", "instance")
_JSON_SERVICE_KEYS = ("service", "service.name", "app", "logger", "component", "name")

_PANIC_RE = re.compile(r"^(panic:|fatal error:|thread '.*' panicked at|Traceback \(most recent call last\))")
_LOGFMT_LEVEL_RE = re.compile(r"\b(?:level|lvl|severity)=\"?([A-Za-z]+)\"?", re.I)
_BRACKET_LEVEL_RE = re.compile(
    r"(?:^|[\s\[\(|<])(TRACE|DEBUG|DBG|INFO|INF|NOTICE|WARN|WARNING|WRN|ERROR|ERR|SEVERE|"
    r"FATAL|CRITICAL|CRIT|ALERT|EMERG|EMERGENCY|PANIC|DPANIC)(?=[\s\]\)|:>]|$)",
    re.I,
)
# Numeric syslog/pino levels
_PINO_LEVELS = {10: Level.TRACE, 20: Level.DEBUG, 30: Level.INFO, 40: Level.WARN, 50: Level.ERROR, 60: Level.FATAL}

# Normalization for fingerprints: strip values that vary between otherwise identical errors.
_NORMALIZERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\S*\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\S*\s*"), ""),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"\b[0-9a-f]{16,}\b", re.I), "<hash>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\"[^\"]{0,200}\""), "\"<str>\""),
    (re.compile(r"\b\d+(?:\.\d+)?(?:ms|s|us|ns|b|kb|mb|gb|%)?\b", re.I), "<n>"),
]


def _get(d: dict[str, Any], dotted: str) -> Any:
    if dotted in d:
        return d[dotted]
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _first(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        v = _get(d, k)
        if v not in (None, ""):
            return v
    return None


def detect_level(text: str, default: Level = Level.INFO) -> Level:
    return _detect(text, default)[0]


def _detect(text: str, default: Level) -> tuple[Level, bool]:
    """Return (level, explicit) where explicit is False if nothing matched."""
    stripped = text.lstrip()
    if _PANIC_RE.match(stripped):
        return (Level.PANIC if not stripped.startswith("Traceback") else Level.ERROR), True
    m = _LOGFMT_LEVEL_RE.search(text)
    if m:
        return Level.parse(m.group(1), default), True
    m = _BRACKET_LEVEL_RE.search(text[:200])
    if m:
        return Level.parse(m.group(1), default), True
    return default, False


def parse_line(line: str, source: str, host: str = "", service: str = "",
               default_level: Level = Level.INFO) -> LogEvent | None:
    line = line.rstrip("\r\n")
    if not line.strip():
        return None
    if line.lstrip().startswith("{"):
        try:
            obj = json.loads(line)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            return parse_record(obj, source, host, service, default_level, raw=line)
    level, explicit = _detect(line, default_level)
    return LogEvent(
        message=line.strip(), level=level, source=source, host=host,
        service=service, raw=line, explicit_level=explicit,
    )


def parse_record(obj: dict[str, Any], source: str, host: str = "", service: str = "",
                 default_level: Level = Level.INFO, raw: str | None = None) -> LogEvent:
    """Parse an already-decoded structured record (JSON line, HTTP payload item).

    Known keys map onto LogEvent fields; the record itself is kept whole in ``raw``."""
    lvl_raw = _first(obj, _JSON_LEVEL_KEYS)
    if isinstance(lvl_raw, (int, float)):
        level = _PINO_LEVELS.get(int(lvl_raw), default_level)
    elif lvl_raw is not None:
        level = Level.parse(str(lvl_raw), default_level)
    else:
        level = default_level
    msg = _first(obj, _JSON_MSG_KEYS)
    msg = str(msg) if msg is not None else json.dumps(obj)[:500]
    if level < Level.PANIC and _PANIC_RE.match(msg.lstrip()) and not msg.lstrip().startswith("Traceback"):
        level = Level.PANIC
    return LogEvent(
        message=msg,
        level=level,
        source=source,
        host=str(_first(obj, _JSON_HOST_KEYS) or host),
        service=str(_first(obj, _JSON_SERVICE_KEYS) or service),
        project=str(obj.get("project") or ""),
        raw=raw if raw is not None else json.dumps(obj, default=str),
    )


def normalize(message: str) -> str:
    text = message.strip()
    for pattern, repl in _NORMALIZERS:
        text = pattern.sub(repl, text)
    return re.sub(r"\s+", " ", text)[:300]


def fingerprint(event: LogEvent) -> str:
    key = f"{event.source}|{event.service}|{normalize(event.message)}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]
