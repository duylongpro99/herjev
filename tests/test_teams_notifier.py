"""The Teams channel posts the shared alert format to a group chat through Graph."""

import json
import os
import time

import httpx
import pytest

from herjev.formatting import render_text
from herjev.models import Alert, Analysis, Decision
from herjev.notifiers.teams import TeamsAuthError, TeamsNotifier

from conftest import make_incident


def _alert():
    inc = make_incident()
    return Alert(inc, Decision(notify=True, urgency=2, category="database", reason="jev"),
                 Analysis(summary="db down", probable_causes=["Postgres <primary> is down"]))


def _graph(calls, token_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/oauth2/v2.0/token"):
            if token_status != 200:
                return httpx.Response(token_status, json={"error": "invalid_grant", "error_description": "expired"})
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "r2", "expires_in": 3600})
        return httpx.Response(201, json={"id": "m1"})
    return httpx.MockTransport(handler)


def _notifier(tmp_path, calls, token, **kw):
    path = tmp_path / "teams_token.json"
    path.write_text(json.dumps(token))
    return TeamsNotifier("teams", chat_id="19:abc@thread.v2", app_id="app", tenant_id="t",
                         token_file=str(path), transport=_graph(calls, **kw)), path


async def test_posts_the_shared_format_as_html(tmp_path):
    calls = []
    ch, _ = _notifier(tmp_path, calls, {"access_token": "a1", "refresh_token": "r1",
                                        "expires_at": time.time() + 600})
    alert = _alert()
    await ch.send(alert)
    await ch.aclose()
    (req,) = calls
    assert str(req.url) == "https://graph.microsoft.com/v1.0/chats/19:abc@thread.v2/messages"
    assert req.headers["authorization"] == "Bearer a1"
    content = json.loads(req.content)["body"]["content"]
    expected = render_text(alert).replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    assert content == expected and "&lt;primary&gt;" in content


async def test_expired_token_is_refreshed_and_rotated(tmp_path):
    calls = []
    ch, path = _notifier(tmp_path, calls, {"access_token": "old", "refresh_token": "r1", "expires_at": 0})
    await ch.send(_alert())
    await ch.aclose()
    assert calls[0].url.path == "/t/oauth2/v2.0/token" and b"refresh_token=r1" in calls[0].content
    assert calls[1].headers["authorization"] == "Bearer new"
    saved = json.loads(path.read_text())
    assert saved["refresh_token"] == "r2" and saved["expires_at"] > time.time()
    assert os.stat(path).st_mode & 0o777 == 0o600


async def test_failed_refresh_asks_for_login(tmp_path):
    ch, _ = _notifier(tmp_path, [], {"access_token": "old", "refresh_token": "r1", "expires_at": 0},
                      token_status=400)
    with pytest.raises(TeamsAuthError, match="herjev teams login"):
        await ch.send(_alert())
    await ch.aclose()


def test_needs_chat_and_app_settings(monkeypatch):
    for var in ("TEAMS_CHAT_ID", "TEAMS_APP_ID", "TEAMS_TENANT_ID"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError, match="TEAMS_CHAT_ID"):
        TeamsNotifier("teams", app_id="a", tenant_id="t")
    with pytest.raises(ValueError, match="TEAMS_APP_ID"):
        TeamsNotifier("teams", chat_id="19:x@thread.v2")


def test_account_command_is_contributed_by_the_channel():
    import inspect

    import herjev.cli as cli
    from herjev.notifiers import teams

    args = cli.build_parser().parse_args(["teams", "whoami", "--tenant-id", "t"])
    assert args.func is teams._account_command and args.tenant_id == "t"
    assert "teams" not in inspect.getsource(cli).lower()
