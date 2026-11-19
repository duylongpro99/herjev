"""``herjev handoff setup``: the OS URL handler builders, installers and the setup flow."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from herjev import handoff as h
from herjev import handoff_install as hi

PORTAL = "https://portal.example"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERJEV_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HERJEV_DASHBOARD_TOKEN", raising=False)
    return tmp_path / "home"


class Recorder:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        if cmd[0] == "osacompile":  # what osacompile leaves behind
            contents = Path(cmd[2]) / "Contents"
            contents.mkdir(parents=True)
            with open(contents / "Info.plist", "wb") as f:
                plistlib.dump({"CFBundleName": "Herjev Handoff", "CFBundleExecutable": "applet"}, f)


# ---------------------------------------------------------------- builders

def test_applet_quotes_the_herjev_path_and_home():
    src = hi.applet_source('/Users/a b/it\'s "x"\\bin/herjev', '/Users/a "b"/state')
    assert 'quoted form of "/Users/a b/it\'s \\"x\\"\\\\bin/herjev"' in src
    assert '" handoff open --home " & quoted form of "/Users/a \\"b\\"/state" & " " & quoted form of theURL' in src
    assert "on open location theURL" in src
    assert 'quoted form of theURL & " >/dev/null 2>&1 &"' in src


def test_plist_patch_keeps_existing_keys():
    patched = hi.patch_plist({"CFBundleName": "Herjev Handoff", "CFBundleIdentifier": "com.apple.ScriptEditor.id.x"})
    assert patched["CFBundleName"] == "Herjev Handoff"
    assert patched["CFBundleIdentifier"] == "dev.herjev.handoff"
    assert patched["CFBundleURLTypes"] == [{"CFBundleURLName": "herjev handoff", "CFBundleURLSchemes": ["herjev"]}]
    assert patched["LSUIElement"] is True


def test_desktop_entry_quotes_the_path():
    assert "Exec=/usr/bin/herjev handoff open --home /home/me/.herjev %u\n" in hi.desktop_entry("/usr/bin/herjev", "/home/me/.herjev")
    entry = hi.desktop_entry('/home/a b/$x "q"/100%/herjev', "/h")
    # String unescaping runs first, so each Exec escape backslash is written twice.
    assert 'Exec="/home/a b/\\\\$x \\\\"q\\\\"/100%%/herjev" handoff open --home /h %u\n' in entry
    assert hi.desktop_arg("/a b/c\\d") == '"/a b/c\\\\\\\\d"'
    with pytest.raises(h.HandoffError):
        hi.desktop_arg("/a\nb")
    assert "MimeType=x-scheme-handler/herjev;\n" in entry and "NoDisplay=true\n" in entry


# ---------------------------------------------------------------- installers

def test_macos_install_and_uninstall_command_sequence(tmp_path):
    run = Recorder()
    app = hi.install_macos("/opt/herjev", "/home/state", apps_dir=tmp_path, run=run)
    assert app == tmp_path / "Herjev Handoff.app"
    assert [c[0] for c in run.calls] == ["osacompile", "codesign", hi.LSREGISTER]
    assert run.calls[1] == ["codesign", "--force", "--sign", "-", str(app)]
    assert run.calls[2] == [hi.LSREGISTER, "-f", str(app)]
    with open(app / "Contents" / "Info.plist", "rb") as f:
        info = plistlib.load(f)
    assert info["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["herjev"] and info["CFBundleName"] == "Herjev Handoff"

    run.calls.clear()  # re-running replaces the bundle
    hi.install_macos("/opt/herjev", "/home/state", apps_dir=tmp_path, run=run)
    assert [c[:2] for c in run.calls[:1]] == [[hi.LSREGISTER, "-u"]]
    assert [c[0] for c in run.calls[1:]] == ["osacompile", "codesign", hi.LSREGISTER]

    run.calls.clear()
    assert hi.uninstall_macos(apps_dir=tmp_path, run=run) == app
    assert run.calls == [[hi.LSREGISTER, "-u", str(app)]]
    assert not app.exists()
    assert hi.uninstall_macos(apps_dir=tmp_path, run=run) is None


def test_linux_install_and_uninstall_command_sequence(tmp_path, monkeypatch):
    monkeypatch.setattr(hi.shutil, "which", lambda name: f"/usr/bin/{name}")
    run = Recorder()
    entry = hi.install_linux("/opt/herjev", "/home/state", data_home=tmp_path, run=run)
    assert entry == tmp_path / "applications" / "herjev-handoff.desktop"
    assert "Exec=/opt/herjev handoff open --home /home/state %u" in entry.read_text()
    assert run.calls == [["update-desktop-database", str(tmp_path / "applications")],
                         ["xdg-mime", "default", "herjev-handoff.desktop", "x-scheme-handler/herjev"]]
    run.calls.clear()
    assert hi.uninstall_linux(data_home=tmp_path, run=run) == entry
    assert not entry.exists()
    assert run.calls == [["update-desktop-database", str(tmp_path / "applications")]]


def test_a_failing_tool_is_reported(tmp_path):
    with pytest.raises(h.HandoffError, match="false failed|not found"):
        hi.run_command(["false"])
    with pytest.raises(h.HandoffError, match="not found"):
        hi.run_command([str(tmp_path / "missing-tool")])


@pytest.mark.skipif(sys.platform != "darwin" or not os.environ.get("HERJEV_TEST_MACOS_HANDLER"),
                    reason="builds a real applet; opt in on macOS with HERJEV_TEST_MACOS_HANDLER=1")
def test_macos_applet_builds_and_is_signed(tmp_path):
    def run(cmd):  # everything except registering, so this machine's herjev:// is untouched
        if cmd[0] != hi.LSREGISTER:
            hi.run_command(cmd)

    app = hi.install_macos("/opt/my herjev/herjev", "/home/state", apps_dir=tmp_path / "Applications", run=run)
    with open(app / "Contents" / "Info.plist", "rb") as f:
        info = plistlib.load(f)
    assert info["CFBundleIdentifier"] == "dev.herjev.handoff" and info["LSUIElement"] is True
    assert info["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["herjev"]
    subprocess.run(["codesign", "--verify", str(app)], check=True)
    hi.uninstall_macos(apps_dir=tmp_path / "Applications", run=run)
    assert not app.exists()


# ---------------------------------------------------------------- setup flow

def args(**over) -> argparse.Namespace:
    return argparse.Namespace(**{"portal_url": PORTAL, "agent": "claude", "terminal": "terminal", "runtime": "none",
                                 "template": None, "uninstall": False, "purge": False, **over})


def portal(status=200):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json={})
    return httpx.MockTransport(handler), seen


def no_questions(prompt):
    raise AssertionError(f"asked {prompt!r}")


def test_setup_with_flags_checks_the_token_saves_privately_and_registers(home, tmp_path):
    transport, seen = portal()
    registered = []
    cfg = h.setup(args(), ask=no_questions, secret=lambda p: "s3cret", env={"PATH": "/opt/bin:/usr/bin"},
                  platform="darwin", transport=transport, herjev="/opt/bin/herjev",
                  register=lambda p, exe: registered.append((p, exe)) or tmp_path / "app")
    assert seen[0].url == f"{PORTAL}/api/status" and seen[0].headers["authorization"] == "Bearer s3cret"
    assert registered == [("darwin", "/opt/bin/herjev")]
    path = h.config_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    saved = json.loads(path.read_text())
    assert saved == {"portal_url": PORTAL, "token": "s3cret", "agent": "claude", "terminal": "terminal",
                     "runtime": "none", "template": [], "last_workdir": "~", "path": "/opt/bin:/usr/bin"}
    assert cfg.agent == "claude"


def test_setup_asks_with_detected_choices(home, tmp_path, monkeypatch):
    monkeypatch.setattr(h, "detect_agents", lambda path=None: ["claude", "codex"])
    monkeypatch.setattr(h, "detect_terminals", lambda platform, env: ["terminal", "ghostty"])
    monkeypatch.setattr(h, "detect_runtimes", lambda agent, path=None: ["none", "tmux", "herdr"])
    answers = iter([PORTAL + "/", "2", "herdr", "2"])
    transport, _ = portal()
    cfg = h.setup(args(portal_url=None, agent=None, terminal=None, runtime=None), ask=lambda p: next(answers),
                  secret=lambda p: "s3cret", env={}, platform="darwin", transport=transport, herjev="/x/herjev",
                  register=lambda p, exe: tmp_path)
    assert (cfg.portal_url, cfg.agent, cfg.runtime, cfg.terminal) == (PORTAL, "codex", "herdr", "ghostty")


def test_setup_skips_the_terminal_question_for_a_runtime_with_its_own_window(home, tmp_path, monkeypatch):
    monkeypatch.setattr(h, "detect_runtimes", lambda agent, path=None: ["none", "orca"])
    transport, _ = portal()
    cfg = h.setup(args(terminal=None, runtime=None), ask=lambda p: "orca" if p.startswith("Runtime") else no_questions(p),
                  secret=lambda p: "s3cret", env={}, platform="darwin", transport=transport, herjev="/x/herjev",
                  register=lambda p, exe: tmp_path)
    assert cfg.runtime == "orca"


def test_detect_runtimes_offers_only_what_can_hold_the_agent(tmp_path):
    for name in ("tmux", "herdr", "paseo"):
        exe = tmp_path / name
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    assert h.detect_runtimes("claude", str(tmp_path)) == ["none", "tmux", "herdr", "paseo"]
    assert h.detect_runtimes("gemini", str(tmp_path)) == ["none", "tmux", "herdr"]
    assert h.detect_runtimes("custom", str(tmp_path)) == ["none"]


def test_setup_takes_a_custom_template(home, tmp_path):
    transport, _ = portal()
    cfg = h.setup(args(terminal="custom", template="herdr pane run '{launch_script}'"), ask=no_questions,
                  secret=lambda p: "t", env={}, platform="darwin", transport=transport, herjev="/x/herjev",
                  register=lambda p, exe: tmp_path)
    assert cfg.template == ["herdr", "pane", "run", "{launch_script}"]


def test_setup_reads_the_token_from_the_environment(home, tmp_path):
    transport, seen = portal()
    h.setup(args(), ask=no_questions, secret=no_questions, env={"HERJEV_DASHBOARD_TOKEN": "fromenv"},
            platform="darwin", transport=transport, herjev="/x/herjev", register=lambda p, exe: tmp_path)
    assert seen[0].headers["authorization"] == "Bearer fromenv"


@pytest.mark.parametrize("over,status,match", [
    ({"portal_url": "http://portal.example"}, 200, "https://"),
    ({}, 401, "refused this token"),
    ({}, 500, "answered 500"),
])
def test_setup_refuses_and_saves_nothing(home, over, status, match):
    transport, _ = portal(status)
    registered = []
    with pytest.raises(h.HandoffError, match=match):
        h.setup(args(**over), ask=no_questions, secret=lambda p: "s3cret", env={}, platform="darwin",
                transport=transport, herjev="/x/herjev", register=lambda p, exe: registered.append(p))
    assert not h.config_path().exists() and registered == []


def test_the_token_is_written_only_to_handoff_json(home, tmp_path):
    transport, _ = portal()
    h.setup(args(), ask=no_questions, secret=lambda p: "very-secret-token", env={}, platform="darwin",
            transport=transport, herjev="/x/herjev", register=lambda p, exe: tmp_path)
    holders = [p for p in home.rglob("*") if p.is_file() and b"very-secret-token" in p.read_bytes()]
    assert holders == [h.config_path()]


def test_the_token_is_not_a_flag():
    from herjev.cli import build_parser
    with pytest.raises(SystemExit):
        build_parser().parse_args(["handoff", "setup", "--token", "x"])


def test_setup_refuses_flags_it_would_ignore(home, capsys):
    from herjev.cli import build_parser
    transport, _ = portal()
    with pytest.raises(h.HandoffError, match="--template"):
        h.setup(args(template="x {launch_script}"), ask=no_questions, secret=lambda p: "t", env={},
                platform="darwin", transport=transport, herjev="/x/herjev", register=lambda p, exe: None)
    a = build_parser().parse_args(["handoff", "setup", "--purge"])
    assert a.func(a) == 2


def test_uninstall_keeps_the_config_unless_purged(home, monkeypatch, capsys):
    from herjev.cli import build_parser
    h.save_config(h.HandoffConfig(portal_url=PORTAL, token="t"))
    monkeypatch.setattr(hi, "uninstall", lambda platform: Path("/Applications/x.app"))
    a = build_parser().parse_args(["handoff", "setup", "--uninstall"])
    assert a.func(a) == 0 and h.config_path().exists()
    a = build_parser().parse_args(["handoff", "setup", "--uninstall", "--purge"])
    assert a.func(a) == 0 and not h.config_path().exists()


def test_herjev_executable_prefers_the_running_one(tmp_path):
    exe = tmp_path / "herjev"
    exe.write_text("")
    assert h.herjev_executable(str(exe)) == str(exe)


def _broken_paste(prompt):
    return b"abc\xe2(".decode()


@pytest.mark.parametrize("secret,match", [
    (_broken_paste, "invalid text"),
    (lambda p: "abc\u2019def", "non-ASCII"),
    (lambda p: "abc def", "spaces"),
])
def test_setup_explains_a_token_that_is_not_plain_ascii(home, secret, match):
    transport, seen = portal()
    with pytest.raises(h.HandoffError, match=match):
        h.setup(args(), ask=no_questions, secret=secret, env={}, platform="darwin",
                transport=transport, herjev="/x/herjev", register=lambda p, exe: None)
    assert seen == [] and not h.config_path().exists()
