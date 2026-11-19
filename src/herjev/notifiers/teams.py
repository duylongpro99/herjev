"""Microsoft Teams group chat, posted as a signed-in Teams user.

Microsoft Graph with delegated permissions and the OAuth2 device code flow, so
no Teams bot and no tenant admin are needed. ``herjev teams login`` signs the
account in once; the refresh token is cached in ``$HERJEV_HOME/teams_token.json``
(mode 600) and rotated on every refresh.

Settings: ``TEAMS_APP_ID`` (Entra app registration, public client flows on),
``TEAMS_TENANT_ID`` and, for the channel, ``TEAMS_CHAT_ID``.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from ..models import Alert
from ..paths import herjev_home
from ..registry import register
from .base import Notifier

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access User.Read ChatMessage.Send Chat.ReadBasic"


class TeamsAuthError(RuntimeError):
    pass


class TeamsGraph:
    """Delegated Graph client: token cache, refresh and the few calls herjev needs."""

    def __init__(self, app_id: str | None = None, tenant_id: str | None = None,
                 token_file: str | os.PathLike[str] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, timeout: float = 30.0):
        self.app_id = app_id or os.environ.get("TEAMS_APP_ID", "")
        self.tenant_id = tenant_id or os.environ.get("TEAMS_TENANT_ID", "")
        if not self.app_id or not self.tenant_id:
            raise ValueError("teams needs TEAMS_APP_ID and TEAMS_TENANT_ID (or app_id/tenant_id)")
        self.token_file = Path(token_file).expanduser() if token_file else herjev_home() / "teams_token.json"
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._lock = asyncio.Lock()

    def _url(self, kind: str) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/{kind}"

    def _save(self, tok: dict[str, Any]) -> None:
        tok["expires_at"] = int(time.time()) + int(tok.get("expires_in", 3600)) - 120
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.token_file.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(tok, f)
        os.replace(tmp, self.token_file)

    async def _access_token(self) -> str:
        async with self._lock:
            try:
                tok = json.loads(self.token_file.read_text())
            except FileNotFoundError:
                raise TeamsAuthError(f"not signed in ({self.token_file} missing); run: herjev teams login") from None
            if tok.get("expires_at", 0) > time.time():
                return tok["access_token"]
            resp = await self._client.post(self._url("token"), data={
                "grant_type": "refresh_token", "client_id": self.app_id,
                "refresh_token": tok["refresh_token"], "scope": SCOPES})
            new = resp.json()
            if resp.status_code != 200:
                raise TeamsAuthError("token refresh failed, run `herjev teams login` again: "
                                     + str(new.get("error_description") or new)[:200])
            new.setdefault("refresh_token", tok["refresh_token"])
            self._save(new)
            return new["access_token"]

    async def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = path if path.startswith("http") else GRAPH + path
        resp = await self._client.request(method, url, json=body,
                                          headers={"Authorization": f"Bearer {await self._access_token()}"})
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            err = data.get("error", data)
            raise RuntimeError(f"Graph {method} {path} -> {resp.status_code}: "
                               f"{err.get('message') if isinstance(err, dict) else err}"[:300])
        return data

    async def send_text(self, chat_id: str, text: str) -> dict[str, Any]:
        content = html.escape(text).replace("\n", "<br>")
        return await self.call("POST", f"/chats/{chat_id}/messages",
                               {"body": {"contentType": "html", "content": content}})

    async def group_chats(self) -> list[dict[str, Any]]:
        out, url = [], "/me/chats?$filter=chatType%20eq%20'group'&$expand=members&$top=50"
        while url:
            data = await self.call("GET", url)
            out += data.get("value", [])
            url = data.get("@odata.nextLink")
        return out

    async def login(self, show: Callable[[str], None] = print) -> dict[str, Any]:
        """Device code sign-in; ``show`` gets the "open this URL, enter this code" text."""
        resp = await self._client.post(self._url("devicecode"), data={"client_id": self.app_id, "scope": SCOPES})
        dc = resp.json()
        if resp.status_code != 200:
            raise TeamsAuthError(f"device code request failed: {dc.get('error_description') or dc}")
        show(dc["message"])
        interval = int(dc.get("interval", 5))
        deadline = time.time() + int(dc.get("expires_in", 900))
        while time.time() < deadline:
            await asyncio.sleep(interval)
            resp = await self._client.post(self._url("token"), data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": self.app_id, "device_code": dc["device_code"]})
            tok = resp.json()
            if resp.status_code == 200:
                self._save(tok)
                return await self.call("GET", "/me")
            if tok.get("error") == "authorization_pending":
                continue
            if tok.get("error") == "slow_down":
                interval += 5
                continue
            raise TeamsAuthError(f"login failed: {tok.get('error_description') or tok}")
        raise TeamsAuthError("login timed out")

    async def aclose(self) -> None:
        await self._client.aclose()


@register("notifier", "teams")
class TeamsNotifier(Notifier):
    """Teams group chat, as the user signed in with ``herjev teams login``."""

    def __init__(self, name: str, chat_id: str | None = None, app_id: str | None = None,
                 tenant_id: str | None = None, token_file: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, **kw):
        super().__init__(name, **kw)
        self.chat_id = chat_id or os.environ.get("TEAMS_CHAT_ID", "")
        if not self.chat_id:
            raise ValueError("teams notifier needs chat_id or TEAMS_CHAT_ID")
        self.graph = TeamsGraph(app_id, tenant_id, token_file, transport=transport)

    async def send(self, alert: Alert) -> None:
        await self.graph.send_text(self.chat_id, self.render(alert))

    async def aclose(self) -> None:
        await self.graph.aclose()

    @classmethod
    def add_cli(cls, sub: argparse._SubParsersAction) -> None:
        p = sub.add_parser("teams", help="Teams account for the teams channel: login, whoami, chats")
        p.add_argument("action", choices=["login", "whoami", "chats"])
        p.add_argument("--app-id", help="Entra app (client) id (default: TEAMS_APP_ID)")
        p.add_argument("--tenant-id", help="Entra tenant id (default: TEAMS_TENANT_ID)")
        p.set_defaults(func=_account_command)


def _account_command(args: argparse.Namespace) -> int:
    """Sign the Teams account in, show it, or list its group chats (for TEAMS_CHAT_ID)."""
    from ..config import load_config
    try:
        load_config(args.config)  # loads the .env next to herjev.yaml (TEAMS_APP_ID, TEAMS_TENANT_ID)
    except FileNotFoundError:
        pass

    async def go() -> int:
        graph = TeamsGraph(args.app_id, args.tenant_id)
        try:
            if args.action == "login":
                me = await graph.login(show=lambda msg: print(msg, flush=True))
                print(f"signed in as {me.get('displayName')} <{me.get('userPrincipalName')}>; "
                      f"token in {graph.token_file}")
            elif args.action == "whoami":
                me = await graph.call("GET", "/me")
                print(json.dumps({k: me.get(k) for k in ("displayName", "userPrincipalName", "id")}))
            else:
                for c in await graph.group_chats():
                    names = "; ".join(m.get("displayName") or "?" for m in c.get("members", []))
                    print(f"{c['id']}\t{c.get('topic') or '(no topic)'}\t{names}")
            return 0
        finally:
            await graph.aclose()

    try:
        return asyncio.run(go())
    except RuntimeError as exc:
        print(f"herjev: {exc}", file=sys.stderr)
        return 1
