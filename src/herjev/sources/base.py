from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from ..models import Level, LogEvent
from ..parsing import parse_line, parse_record

log = logging.getLogger(__name__)


class Source(ABC):
    """A stream of LogEvents from somewhere (poll or listen).

    Subclasses implement :meth:`stream`; they should reconnect on failure
    themselves (``self.backoff()``) so one flaky server never stops the agent.
    """

    type_name = "base"

    def __init__(self, name: str, host: str = "", service: str = "", default_level: str = "info",
                 project: str = ""):
        self.name = name
        self.host = host
        self.service = service
        self.project = project
        self.default_level = Level.parse(default_level)
        self._attempt = 0

    @abstractmethod
    def stream(self) -> AsyncIterator[LogEvent]:
        ...

    def line(self, text: str) -> LogEvent | None:
        return self._tag(parse_line(text, self.name, self.host, self.service, self.default_level))

    def record(self, obj: dict[str, Any]) -> LogEvent:
        return self._tag(parse_record(obj, self.name, self.host, self.service, self.default_level))

    def _tag(self, ev: LogEvent | None) -> LogEvent | None:
        # The aggregator reads the project from the event so it never needs the source.
        if ev is not None and self.project:
            ev.project = self.project
        return ev

    async def backoff(self, err: BaseException | str) -> None:
        self._attempt += 1
        delay = min(60.0, 2.0 ** min(self._attempt, 6))
        log.warning("source %s failed (%s); retrying in %.0fs", self.name, err, delay)
        await asyncio.sleep(delay)

    def healthy(self) -> None:
        self._attempt = 0

    def catalog(self) -> list[dict[str, Any]] | None:
        """The services this source can watch, for the portal, or None when it cannot
        choose between services. Each entry: ``label`` (as the source reports it),
        ``name`` and ``watched`` (both from the config, before portal choices)."""
        return None

    def apply_choices(self, rows: list[dict[str, Any]]) -> None:
        """Apply the portal's rows for this source (``label``, ``watched``,
        ``display_name``); a source without a catalog ignores them."""

    def describe(self) -> str:
        return f"{self.type_name}:{self.name}"
