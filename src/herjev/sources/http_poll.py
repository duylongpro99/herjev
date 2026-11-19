from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from typing import Any, AsyncIterator

import httpx

from ..models import LogEvent
from ..registry import register
from ..trace import step
from .base import Source

log = logging.getLogger(__name__)


@register("source", "http_poll")
class HTTPPollSource(Source):
    """Poll an HTTP endpoint that returns recent logs.

    Response may be: a JSON array of records, an object with the records
    under ``records_path`` (dotted), NDJSON, or plain text lines. A
    ``cursor_param`` (e.g. ``since``) is sent with the newest timestamp or
    cursor seen so far, taken from ``cursor_field`` in each record.
    """

    def __init__(self, name: str, url: str, interval: float = 15.0,
                 headers: dict[str, str] | None = None, params: dict[str, Any] | None = None,
                 records_path: str | None = None, cursor_param: str | None = None,
                 cursor_field: str | None = None, timeout: float = 15.0, **kw):
        super().__init__(name, **kw)
        self.url = url
        self.interval = interval
        self.headers = headers or {}
        self.params = params or {}
        self.records_path = records_path
        self.cursor_param = cursor_param
        self.cursor_field = cursor_field
        self.timeout = timeout
        self.cursor: Any = None

    def _records(self, resp: httpx.Response) -> list[Any]:
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype and "ndjson" not in ctype:
            data: Any = resp.json()
            if self.records_path:
                for part in self.records_path.split("."):
                    data = data.get(part, []) if isinstance(data, dict) else []
            return data if isinstance(data, list) else [data]
        return resp.text.splitlines()

    def _to_events(self, records: list[Any]) -> list[LogEvent]:
        out = []
        for rec in records:
            ev = self.record(rec) if isinstance(rec, dict) else self.line(str(rec))
            if ev is None:
                continue
            if self.cursor_field and isinstance(rec, dict) and rec.get(self.cursor_field) is not None:
                self.cursor = rec[self.cursor_field]
            out.append(ev)
        return out

    async def stream(self) -> AsyncIterator[LogEvent]:
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
            while True:
                params = dict(self.params)
                if self.cursor_param and self.cursor is not None:
                    params[self.cursor_param] = self.cursor
                try:
                    resp = await client.get(self.url, params=params)
                    resp.raise_for_status()
                    events = self._to_events(self._records(resp))
                    self.healthy()
                except (httpx.HTTPError, ValueError) as exc:
                    await self.backoff(exc)
                    continue
                for ev in events:
                    yield ev
                await asyncio.sleep(self.interval)


# Coolify names containers after a 24-character resource id: compose services as
# `<service>-<id>`, applications as the bare `<id>` (plus `-<suffix>` for helpers).
_COOLIFY_ID = r"[a-z0-9]{24}"
_ID_SUFFIX = re.compile(rf"^(.+)-{_COOLIFY_ID}$")
_ID_PREFIX = re.compile(rf"^({_COOLIFY_ID})(-.+)?$")


def _logql_regex_literal(text: str) -> str:
    # Escaped for RE2, then for the LogQL double-quoted string that carries it.
    return re.sub(r"([.+*?()\[\]{}|^$\\])", r"\\\\\1", text)


@register("source", "loki")
class LokiSource(Source):
    """Poll Grafana Loki's ``query_range`` API with a LogQL query.

    ``service_names`` maps a stream's service label (or a Coolify resource id) to
    the name shown in alerts and the portal; ``strip_service_ids`` drops Coolify's
    `-<id>` suffix from compose service names. ``services`` lists the shown names
    to watch: the source adds a ``service_name`` matcher for them to the query's
    stream selector, so Loki returns only those services. Choices made in the portal
    (:meth:`apply_choices`) override both while the watcher runs.
    """

    LABELS_REFRESH_SECONDS = 60.0

    def __init__(self, name: str, url: str, query: str, interval: float = 15.0,
                 headers: dict[str, str] | None = None, limit: int = 1000,
                 timeout: float = 20.0, service_names: dict[str, str] | None = None,
                 strip_service_ids: bool = False, services: list[str] | None = None, **kw):
        super().__init__(name, **kw)
        self.config_names = {str(k): str(v) for k, v in (service_names or {}).items()}
        self.config_services = [str(s) for s in services or []]
        self.service_names = dict(self.config_names)
        self.strip_service_ids = strip_service_ids
        self.services = list(self.config_services)
        self.base_url = url.rstrip("/") + "/loki/api/v1"
        self.url = self.base_url + "/query_range"
        self.base_query = query
        self.query = self._select_services(query) if self.services else query
        self.labels: list[str] = []
        self._labels_at = 0.0
        self.interval = interval
        self.headers = headers or {}
        self.limit = limit
        self.timeout = timeout
        self.start_ns = int(time.time() * 1e9)

    def _service_name(self, label: str, names: dict[str, str] | None = None) -> str:
        names = self.service_names if names is None else names
        if label in names:
            return names[label]
        if self.strip_service_ids:
            m = _ID_SUFFIX.match(label)
            if m:
                return names.get(m.group(1), m.group(1))
        # `<id>-proxy` reads as `<name>-proxy` once the id has a name.
        m = _ID_PREFIX.match(label)
        if m and m.group(1) in names:
            return names[m.group(1)] + (m.group(2) or "")
        return label

    def catalog(self) -> list[dict[str, Any]]:
        every = not self.config_services
        return [{"label": label, "name": (name := self._service_name(label, self.config_names)),
                 "watched": every or name in self.config_services} for label in self.labels]

    def apply_choices(self, rows: list[dict[str, Any]]) -> None:
        names = dict(self.config_names)
        names.update({str(r["label"]): str(r["display_name"]) for r in rows if r.get("display_name")})
        self.service_names = names
        watched = [self._service_name(str(r["label"])) for r in rows if r.get("watched")]
        self.services = list(dict.fromkeys(watched)) if watched else list(self.config_services)
        query = self._select_services(self.base_query) if self.services else self.base_query
        if query != self.query:
            log.info("source %s now watches %s", self.name, ", ".join(self.services) or "every service")
        self.query = query

    def _label_patterns(self, shown: str) -> list[str]:
        """Every ``service_name`` label value that :meth:`_service_name` shows as ``shown``."""
        lit = _logql_regex_literal
        patterns = [] if shown in self.service_names else [lit(shown)]
        if self.strip_service_ids and shown not in self.service_names:
            patterns.append(lit(shown) + "-" + _COOLIFY_ID)
        for label, name in self.service_names.items():
            if shown == name:
                patterns.append(lit(label))
                if self.strip_service_ids:
                    patterns.append(lit(label) + "-" + _COOLIFY_ID)
            elif shown.startswith(name + "-") and _ID_PREFIX.match(label):
                patterns.append(lit(label + shown[len(name):]))
        return patterns

    def _select_services(self, query: str) -> str:
        end = query.find("}")
        if not query.lstrip().startswith("{") or end < 0:
            raise ValueError(f"source {self.name}: `services` needs a query that starts "
                             "with a stream selector, e.g. '{job=\"docker\"}'")
        regex = "|".join(dict.fromkeys(p for s in self.services for p in self._label_patterns(s)))
        head = query[:end].rstrip()
        sep = "" if head.endswith("{") else ", "
        return f'{head}{sep}service_name=~"{regex}"{query[end:]}'

    async def label_values(self, client: httpx.AsyncClient | None = None,
                           label: str = "service_name") -> list[str]:
        if client is None:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as own:
                return await self.label_values(own, label)
        # Only the services the configured query can match: its stream selector.
        end = self.base_query.find("}")
        selector = self.base_query[:end + 1] if self.base_query.lstrip().startswith("{") and end > 0 else None
        resp = await client.get(f"{self.base_url}/label/{label}/values",
                                params={"query": selector, "since": "24h"} if selector else None)
        resp.raise_for_status()
        return sorted(resp.json().get("data") or [])

    async def _refresh_labels(self, client: httpx.AsyncClient) -> None:
        """Keep :attr:`labels` current for the portal; a failure keeps the last list."""
        if time.time() - self._labels_at < self.LABELS_REFRESH_SECONDS:
            return
        self._labels_at = time.time()
        try:
            self.labels = await self.label_values(client)
        except (httpx.HTTPError, ValueError) as exc:
            log.debug("source %s: listing services failed: %s", self.name, exc)

    @classmethod
    def add_cli(cls, sub: Any) -> None:
        p = sub.add_parser("loki", help="Loki sources: list the services a source can watch")
        p.add_argument("action", choices=["services"])
        p.add_argument("source", nargs="?", help="loki source name (default: every loki source)")
        p.set_defaults(func=_loki_command)

    def _parse(self, payload: dict[str, Any]) -> list[LogEvent]:
        entries: list[tuple[int, dict[str, str], str]] = []
        for stream in payload.get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            for ts, line in stream.get("values", []):
                entries.append((int(ts), labels, line))
        entries.sort(key=lambda e: e[0])
        out = []
        for ts, labels, line in entries:
            ev = self.line(line)
            if ev is None:
                continue
            ev.timestamp = ts / 1e9
            ev.host = ev.host or str(labels.get("host") or labels.get("instance") or "")
            ev.service = ev.service or self._service_name(
                str(labels.get("service_name") or labels.get("app") or labels.get("job") or ""))
            # The stream's `project` label names the project unless the source or the
            # record set one; the core reads only LogEvent fields, never Loki labels.
            ev.project = ev.project or str(labels.get("project") or "")
            self.start_ns = max(self.start_ns, ts + 1)
            if self.services and ev.service not in self.services:
                continue
            out.append(ev)
        return out

    async def stream(self) -> AsyncIterator[LogEvent]:
        async with httpx.AsyncClient(timeout=self.timeout, headers=self.headers) as client:
            while True:
                await self._refresh_labels(client)
                try:
                    resp = await client.get(self.url, params={
                        "query": self.query, "start": str(self.start_ns),
                        "limit": self.limit, "direction": "forward",
                    })
                    resp.raise_for_status()
                    events = self._parse(resp.json())
                    self.healthy()
                    if events:
                        levels = Counter(ev.level.label for ev in events)
                        step(f"source:{self.name}", "loki returned %d line(s): %s", len(events),
                             ", ".join(f"{k}={v}" for k, v in levels.most_common()))
                except (httpx.HTTPError, ValueError) as exc:
                    await self.backoff(exc)
                    continue
                for ev in events:
                    yield ev
                await asyncio.sleep(self.interval)


def _loki_command(args: Any) -> int:
    """Print each service a loki source sees: its label, shown name and whether it is watched."""
    from ..config import load_config
    from ..registry import create
    cfg = load_config(args.config)
    specs = [s for s in cfg["sources"] if s.get("type") == "loki"
             and (args.source is None or s.get("name") == args.source)]
    if not specs:
        raise ValueError(f"no loki source{' named ' + args.source if args.source else ''} in the config")
    for spec in specs:
        src = create("source", spec)
        labels = asyncio.run(src.label_values())
        print(f"{src.name}:")
        for label in labels:
            shown = src._service_name(label)
            mark = "*" if shown in src.services else " "
            print(f"  {mark} {shown:40} {label if shown != label else ''}".rstrip())
        if src.services:
            missing = sorted(set(src.services) - {src._service_name(l) for l in labels})
            print("  (* watched)" + (f"; not in Loki: {', '.join(missing)}" if missing else ""))
    return 0
