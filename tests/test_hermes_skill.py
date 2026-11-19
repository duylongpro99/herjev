"""The CLI commands behind the on-call tools, and the Hermes skill installer."""

import json
import subprocess
from pathlib import Path

import pytest

from herjev.cli import main
from herjev.models import Alert, Decision, Level
from herjev.store import Store

from conftest import make_incident

REPO = Path(__file__).resolve().parents[1]
INSTALLER = REPO / "scripts" / "install-hermes-skill.sh"


def _seed(tmp_path, monkeypatch, pg_dsn):
    monkeypatch.setenv("HERJEV_HOME", str(tmp_path))
    store = Store()
    store.record(Alert(make_incident(), Decision(notify=True, urgency=2, reason="jev")), ["agent"], [])
    warn = make_incident(level=Level.WARN, msg="WARN slow query")
    warn.id, warn.fingerprint = "inc2", "fp2"
    store.record(Alert(warn, Decision(notify=False, reason="noise")), [], [])
    return store


def test_incident_command_prints_full_row(tmp_path, monkeypatch, capsys, pg_dsn):
    _seed(tmp_path, monkeypatch, pg_dsn)
    assert main(["incident", "inc1"]) == 0
    row = json.loads(capsys.readouterr().out)
    assert row["id"] == "inc1" and row["state"]["context"]
    assert main(["incident", "fp1"]) == 0  # fingerprints work too
    assert main(["incident", "nope"]) == 1


def test_incidents_min_level(tmp_path, monkeypatch, capsys, pg_dsn):
    _seed(tmp_path, monkeypatch, pg_dsn)
    assert main(["incidents", "--json", "--min-level", "error"]) == 0
    assert [r["id"] for r in json.loads(capsys.readouterr().out)] == ["inc1"]


def test_mute_command_resolves_incident_fingerprint(tmp_path, monkeypatch, capsys, pg_dsn):
    store = _seed(tmp_path, monkeypatch, pg_dsn)
    assert main(["mute", "inc1", "--minutes", "5", "--reason", "deploy"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"muted": "fp1", "minutes": 5.0, "known_incident": True}
    assert set(store.active_mutes()) == {"fp1"}


def _install(*args, home=None, check=True):
    return subprocess.run(["bash", str(INSTALLER), *args], capture_output=True, text=True,
                          check=check, env={"PATH": f"{home / 'bin'}:/usr/bin:/bin" if home else "/usr/bin:/bin",
                                            "HOME": str(home or "/nonexistent")})


def test_installer_copies_the_skill_and_backs_up_changes(tmp_path):
    hermes = tmp_path / "hermes home"
    skill = hermes / "skills" / "devops" / "herjev-oncall" / "SKILL.md"
    out = _install("--hermes-home", str(hermes)).stdout
    assert f"installed {skill}" in out
    assert skill.read_text() == (REPO / "integrations/hermes/herjev-oncall/SKILL.md").read_text()

    assert "unchanged" in _install("--hermes-home", str(hermes)).stdout
    assert not list(skill.parent.glob("SKILL.md.bak-*"))

    skill.write_text("old")
    _install("--hermes-home", str(hermes))
    assert len(list(skill.parent.glob("SKILL.md.bak-*"))) == 1


def test_installer_defaults_to_hermes_home_under_home(tmp_path):
    (tmp_path / "bin").mkdir()
    _install(home=tmp_path)
    assert (tmp_path / ".hermes" / "skills" / "devops" / "herjev-oncall" / "SKILL.md").is_file()


def test_installer_print_and_bad_args():
    assert _install("--print").stdout.startswith("---\nname: herjev-oncall\n")
    for args in (["--category", "../x"], ["--connect"], ["--connect", "--herjev-url", "ftp://h"],
                 ["--connect", "--herjev-url", "http://h/mcp;rm"],
                 ["--connect", "--herjev-url", "http://h/mcp", "--route", "A B"]):
        bad = subprocess.run(["bash", str(INSTALLER), *args], capture_output=True, text=True)
        assert bad.returncode == 2, args


FAKE_HERMES = """#!/bin/sh
# Records calls; `webhook subscribe` writes the route and prints its secret like Hermes.
echo "$@" >> "$HERMES_HOME/hermes-calls"
case "$1 $2" in
  "webhook subscribe")
    printf '{\\n  "%s": {"secret": "s3cret", "skills": ["herjev-oncall"]}\\n}\\n' "$3" \\
      > "$HERMES_HOME/webhook_subscriptions.json"
    echo "  Secret: s3cret" ;;
  "mcp test") [ -z "$MCP_DOWN" ] || { echo "connection refused"; exit 1; } ;;
esac
"""


def _fake_hermes(tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "hermes").write_text(FAKE_HERMES)
    (tmp_path / "bin" / "hermes").chmod(0o755)


def test_installer_connect_wires_both_directions(tmp_path):
    _fake_hermes(tmp_path)
    hermes, env_file = tmp_path / "hermes", tmp_path / "herjev.env"
    args = ["--connect", "--hermes-home", str(hermes), "--herjev-url", "http://127.0.0.1:8789/mcp",
            "--env-file", str(env_file), "--deliver", "slack", "--chat-id", "C1"]
    out = _install(*args, home=tmp_path).stdout
    assert "reached herjev's MCP endpoint" in out
    assert "HERJEV_WEBHOOK_TOKEN=s3cret" in out and "url: http://127.0.0.1:8644/webhooks/herjev" in out

    token = env_file.read_text().strip().removeprefix("HERJEV_MCP_TOKEN=")
    assert len(token) == 64 and oct(env_file.stat().st_mode & 0o777) == "0o600"
    assert (hermes / ".env").read_text() == f"HERJEV_MCP_TOKEN={token}\n"
    calls = (hermes / "hermes-calls").read_text()
    assert "config set mcp_servers.herjev.url http://127.0.0.1:8789/mcp" in calls
    assert "config set mcp_servers.herjev.headers.Authorization Bearer ${HERJEV_MCP_TOKEN}" in calls
    assert "--skills herjev-oncall" in calls and "--deliver slack --deliver-chat-id C1" in calls
    route = json.loads((hermes / "webhook_subscriptions.json").read_text())["herjev"]
    assert route["toolsets"] == ["mcp-herjev"] and route["secret"] == "s3cret"
    # The fake route has no chat, so the skill still runs on the bare alert text.
    assert route["prompt"] == "{text}" and "deliver_only" not in route

    # A re-run keeps the token and the route.
    out = _install(*args, home=tmp_path).stdout
    assert "kept the existing Hermes route" in out
    assert env_file.read_text() == f"HERJEV_MCP_TOKEN={token}\n"
    assert (hermes / "hermes-calls").read_text().count("webhook subscribe") == 1


def test_installer_connect_existing_route_posts_alerts_as_they_are(tmp_path):
    _fake_hermes(tmp_path)
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    (hermes / "webhook_subscriptions.json").write_text(json.dumps(
        {"herjev": {"secret": "old", "deliver": "slack", "prompt": "herjev: {text}", "skills": ["herjev-oncall"],
                    "toolsets": ["terminal"]},
         "other": {"secret": "x"}}))
    _install("--connect", "--hermes-home", str(hermes), "--herjev-url", "http://h:8789/mcp",
             "--env-file", str(tmp_path / "e"), home=tmp_path)
    subs = json.loads((hermes / "webhook_subscriptions.json").read_text())
    assert subs["herjev"] == {"secret": "old", "deliver": "slack", "prompt": "{text}",
                              "toolsets": ["mcp-herjev"], "deliver_only": True}
    assert subs["other"] == {"secret": "x"}


def test_installer_connect_warns_when_herjev_is_unreachable(tmp_path):
    _fake_hermes(tmp_path)
    proc = subprocess.run(
        ["bash", str(INSTALLER), "--connect", "--hermes-home", str(tmp_path / "h"),
         "--herjev-url", "http://h:8789/mcp", "--env-file", str(tmp_path / "e")],
        capture_output=True, text=True,
        env={"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "HOME": str(tmp_path), "MCP_DOWN": "1"})
    assert proc.returncode == 1 and "could not reach herjev's MCP endpoint" in proc.stderr
    assert "connection refused" in proc.stderr
