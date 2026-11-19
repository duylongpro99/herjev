from __future__ import annotations

from abc import ABC, abstractmethod

from ..formatting import render_text
from ..models import Alert


class Notifier(ABC):
    """Delivers an Alert to a user through some channel."""

    type_name = "base"

    def __init__(self, name: str, max_len: int = 3800):
        self.name = name
        self.max_len = max_len

    def render(self, alert: Alert) -> str:
        return render_text(alert, max_len=self.max_len)

    @abstractmethod
    async def send(self, alert: Alert) -> None:
        ...

    async def aclose(self) -> None:
        pass
