from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator

from ..models import LogEvent
from ..registry import register
from .base import Source


@register("source", "file")
class FileSource(Source):
    """Tail a local file (e.g. a file synced/mounted from a remote host).
    Survives rotation/truncation. ``from_start`` replays existing content."""

    def __init__(self, name: str, path: str, from_start: bool = False,
                 poll_interval: float = 0.5, **kw):
        super().__init__(name, **kw)
        self.path = os.path.expanduser(path)
        self.from_start = from_start
        self.poll_interval = poll_interval

    async def stream(self) -> AsyncIterator[LogEvent]:
        first = True
        while True:
            try:
                f = open(self.path, "r", encoding="utf-8", errors="replace")
            except OSError as exc:
                await self.backoff(exc)
                continue
            with f:
                if first and not self.from_start:
                    f.seek(0, os.SEEK_END)
                first = False
                ino = os.fstat(f.fileno()).st_ino
                buf = ""
                while True:
                    chunk = f.readline()
                    if chunk:
                        self.healthy()
                        buf += chunk
                        if buf.endswith("\n"):
                            ev = self.line(buf)
                            buf = ""
                            if ev is not None:
                                yield ev
                        continue
                    await asyncio.sleep(self.poll_interval)
                    try:
                        st = os.stat(self.path)
                    except OSError:
                        break
                    if st.st_ino != ino or st.st_size < f.tell():
                        break  # rotated or truncated → reopen from start
            self.from_start = True
