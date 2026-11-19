"""Readable per-incident trace of every pipeline step.

Each stage (aggregate → decide → analyze → route → deliver) logs what it
received, what it sent to Jev / the LLM, what came back and why it chose the
result, all on the ``herjev.trace`` logger at INFO (``herjev -v run``).
Lines carry the incident id of the task that produced them, so concurrent
incidents stay readable.

While an incident is being processed the pipeline also installs a step
collector (``current_steps``); every step is appended to it in order, whether
or not terminal logging is on, so the store can persist the full decision
chain for the dashboard.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from contextvars import ContextVar
from typing import Any

log = logging.getLogger("herjev.trace")

current_incident: ContextVar[str] = ContextVar("herjev_incident", default="-")
current_steps: ContextVar[list[dict[str, Any]] | None] = ContextVar("herjev_steps", default=None)
_shown_once: set[str] = set()

MAX_DUMP_CHARS = 20_000


def dump(obj: Any) -> str:
    """Pretty JSON for multi-line log blocks; long payloads are capped."""
    if isinstance(obj, str):
        try:  # model replies are usually JSON text: indent them too
            obj = json.loads(obj)
        except ValueError:
            pass
    text = obj if isinstance(obj, str) else json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    if len(text) > MAX_DUMP_CHARS:
        text = text[:MAX_DUMP_CHARS] + f"\n… ({len(text) - MAX_DUMP_CHARS} more chars)"
    return text


def make_step(stage: str, message: str, body: Any = None, elapsed_ms: float | None = None,
              blob: str | None = None) -> dict[str, Any]:
    """One persisted step; ``body`` is stored as text (capped like the log block)."""
    return {"at": time.time(), "stage": stage, "message": message,
            "body": None if body is None else dump(body),
            "elapsed_ms": None if elapsed_ms is None else round(elapsed_ms, 1), "blob": blob}


def _collect(item: dict[str, Any], incident: str | None) -> None:
    steps = current_steps.get()
    if steps is None or (incident is not None and incident != current_incident.get()):
        return
    item["seq"] = len(steps) + 1
    steps.append(item)


def step(stage: str, msg: str, *args: Any, body: Any = None, incident: str | None = None,
         elapsed_ms: float | None = None) -> None:
    """Record one step; ``body`` (dict/list/str) is appended to the log line as an indented block."""
    logging_on = log.isEnabledFor(logging.INFO)
    if current_steps.get() is None and not logging_on:
        return
    try:  # tracing must never stop an alert from going out
        text = msg % args if args else msg
        _collect(make_step(stage, text, body, elapsed_ms), incident)
        if not logging_on:
            return
        line = f"[{incident or current_incident.get()}] {stage}: {text}"
        if body is not None:
            line += "\n" + "\n".join("    " + ln for ln in dump(body).splitlines())
        log.info(line)
    except Exception as exc:
        log.debug("trace step %s could not be recorded: %s", stage, exc)


def blob_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def once(key: str, stage: str, msg: str, body: Any) -> bool:
    """Log a static block (prompt, question set) only the first time; True if logged now.

    Every incident still gets a step referencing the block by content hash, so
    the store keeps one copy and the dashboard can show it for each incident.
    """
    try:  # tracing must never stop an alert from going out
        if current_steps.get() is not None:
            text = dump(body)
            item = make_step(stage, msg, elapsed_ms=None, blob=blob_key(text))
            item["blob_body"] = text
            _collect(item, None)
        if key in _shown_once or not log.isEnabledFor(logging.INFO):
            return False
        _shown_once.add(key)
        line = f"[{current_incident.get()}] {stage}: {msg}\n" + "\n".join("    " + ln for ln in dump(body).splitlines())
        log.info(line)
        return True
    except Exception as exc:
        log.debug("trace block %s could not be recorded: %s", stage, exc)
        return False
