"""One-shot helpers for the CLI."""

from __future__ import annotations

import time
from typing import Any

from .aggregator import Aggregator
from .models import Alert, Incident, Level
from .parsing import parse_line
from .registry import create


def incidents_from_text(text: str, source: str = "manual", host: str = "", service: str = "") -> list[Incident]:
    agg = Aggregator(window_seconds=0, cooldown_seconds=0, context_lines=200, min_level=Level.WARN)
    now = time.time()
    for line in text.splitlines():
        ev = parse_line(line, source, host, service)
        if ev is not None:
            ev.timestamp = now
            agg.add(ev)
    incidents = agg.flush(force=True)
    return sorted(incidents, key=lambda i: (i.level, i.count), reverse=True)


async def triage_text(cfg: dict[str, Any], text: str, host: str = "", service: str = "",
                      analyze: bool = True, max_incidents: int = 3) -> list[Alert]:
    """Run decide (+ analyze) over pasted logs without delivering anything."""
    decider = create("decider", cfg.get("decider") or {"type": "rules"})
    an_cfg = cfg.get("analyzer") or {"type": "none"}
    analyzer = None if (not analyze or an_cfg.get("type") == "none") else create("analyzer", an_cfg)
    alerts: list[Alert] = []
    try:
        for inc in incidents_from_text(text, host=host, service=service)[:max_incidents]:
            dec = await decider.decide(inc)
            alert = Alert(inc, dec)
            if analyzer:
                try:
                    alert.analysis = await analyzer.analyze(inc, dec)
                except Exception as exc:
                    dec.reason += f"; analysis failed: {exc}"
            alerts.append(alert)
    finally:
        await decider.aclose()
        if analyzer:
            await analyzer.aclose()
    return alerts
