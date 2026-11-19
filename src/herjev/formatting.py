"""Render an Alert as the chat message every channel sends.

This is the one alert format: console, Telegram, Slack, Discord, Teams, the
webhook ``text`` field (which Hermes relays verbatim) all use ``render_text``.
Change the format here and every channel follows.
"""

from __future__ import annotations

import datetime as _dt
import re
from urllib.parse import quote

from .models import Alert


def _ts(t: float) -> str:
    return _dt.datetime.fromtimestamp(t).strftime("%H:%M:%S %d/%m")


def _one_line(s: str, limit: int = 400) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def root_cause(alert: Alert) -> str:
    """Top probable cause from the analysis, else its summary."""
    ana = alert.analysis
    if ana and ana.probable_causes:
        return _one_line(ana.probable_causes[0])
    if ana and ana.summary:
        return _one_line(ana.summary)
    return "chưa có phân tích AI"


def ticket_url(base: str, alert: Alert) -> str:
    """Dashboard deep link that opens the ticket drawer on this exact occurrence."""
    inc = alert.incident
    return f"{base.rstrip('/')}/tickets/{quote(inc.fingerprint, safe='')}?occ={quote(inc.id, safe='')}"


def attach_link(alert: Alert, base: str | None) -> Alert:
    """Set ``alert.link`` to its ticket when the portal has a public URL; channels only read it."""
    alert.link = ticket_url(base, alert) if base else ""
    return alert


def render_text(alert: Alert, max_len: int = 3800) -> str:
    inc = alert.incident
    text = "\n".join([
        f"loại: {inc.level.label}",
        f"thời gian: {_ts(inc.first_seen)}",
        f"nội dung: {_one_line(inc.title)}",
        f"service: {inc.service or inc.host or inc.source}",
        f"root cause dự đoán: {root_cause(alert)}",
    ])
    if not alert.link:
        return _truncate(text, max_len)
    # The link goes last and outside the truncation, so a cut message still opens the ticket.
    link = f"open: {alert.link}"
    return _truncate(text, max_len - len(link) - 1) + "\n" + link


def _truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[: max_len - 20] + "\n…(truncated)"
