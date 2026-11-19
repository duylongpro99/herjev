from __future__ import annotations

import asyncio
import shlex
from typing import AsyncIterator

from ..models import LogEvent
from ..registry import register
from .base import Source


@register("source", "command")
class CommandSource(Source):
    """Run a long-lived command and treat each stdout/stderr line as a log line.

    Covers ``journalctl -f``, ``docker logs -f``, ``kubectl logs -f``,
    ``gcloud logging tail``, ``aws logs tail --follow`` … anything that streams.
    """

    def __init__(self, name: str, command: str | list[str], restart: bool = True, **kw):
        super().__init__(name, **kw)
        self.argv = shlex.split(command) if isinstance(command, str) else list(command)
        self.restart = restart

    def build_argv(self) -> list[str]:
        return self.argv

    async def stream(self) -> AsyncIterator[LogEvent]:
        while True:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *self.build_argv(),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    stdin=asyncio.subprocess.DEVNULL,
                )
            except OSError as exc:
                await self.backoff(exc)
                continue
            assert proc.stdout is not None
            try:
                async for raw in proc.stdout:
                    self.healthy()
                    ev = self.line(raw.decode("utf-8", "replace"))
                    if ev is not None:
                        yield ev
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            if not self.restart:
                return
            await self.backoff(f"process exited with {proc.returncode}")
