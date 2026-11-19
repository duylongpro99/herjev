from __future__ import annotations

import os
import sys
from typing import Any

import httpx

from ..models import Alert
from ..registry import register
from .base import Notifier


@register("notifier", "console")
class ConsoleNotifier(Notifier):
    """Print alerts to stdout (handy for dry runs)."""

    async def send(self, alert: Alert) -> None:
        print("\n" + "─" * 60 + "\n" + self.render(alert) + "\n" + "─" * 60, file=sys.stdout, flush=True)


class _HTTPNotifier(Notifier):
    def __init__(self, name: str, timeout: float = 15.0,
                 transport: httpx.AsyncBaseTransport | None = None, **kw):
        super().__init__(name, **kw)
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def _post(self, url: str, **kw: Any) -> httpx.Response:
        resp = await self._client.post(url, **kw)
        if resp.status_code >= 400:
            raise RuntimeError(f"{self.type_name} HTTP {resp.status_code}: {resp.text[:200]}")
        return resp

    async def aclose(self) -> None:
        await self._client.aclose()


@register("notifier", "telegram")
class TelegramNotifier(_HTTPNotifier):
    """Direct Telegram Bot API."""

    def __init__(self, name: str, chat_id: str, bot_token: str | None = None,
                 thread_id: int | None = None, **kw):
        super().__init__(name, **kw)
        self.token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not self.token:
            raise ValueError("telegram notifier needs bot_token or TELEGRAM_BOT_TOKEN")
        self.chat_id, self.thread_id = chat_id, thread_id

    async def send(self, alert: Alert) -> None:
        body: dict[str, Any] = {"chat_id": self.chat_id, "text": self.render(alert),
                                "disable_web_page_preview": True}
        if self.thread_id:
            body["message_thread_id"] = self.thread_id
        await self._post(f"https://api.telegram.org/bot{self.token}/sendMessage", json=body)


@register("notifier", "slack")
class SlackWebhookNotifier(_HTTPNotifier):
    """Slack incoming webhook."""

    def __init__(self, name: str, webhook_url: str, **kw):
        super().__init__(name, **kw)
        self.url = webhook_url

    async def send(self, alert: Alert) -> None:
        await self._post(self.url, json={"text": self.render(alert)})


@register("notifier", "discord")
class DiscordWebhookNotifier(_HTTPNotifier):
    """Discord channel webhook."""

    def __init__(self, name: str, webhook_url: str, **kw):
        kw.setdefault("max_len", 1990)
        super().__init__(name, **kw)
        self.url = webhook_url

    async def send(self, alert: Alert) -> None:
        await self._post(self.url, json={"content": self.render(alert)})


@register("notifier", "webhook")
class WebhookNotifier(_HTTPNotifier):
    """POST the full structured alert as JSON (for PagerDuty/Opsgenie bridges,
    n8n, Zapier, your own services…)."""

    def __init__(self, name: str, url: str, headers: dict[str, str] | None = None, **kw):
        super().__init__(name, **kw)
        self.url, self.headers = url, headers or {}

    async def send(self, alert: Alert) -> None:
        payload = {"text": self.render(alert), **alert.to_dict()}
        await self._post(self.url, json=payload, headers=self.headers)
