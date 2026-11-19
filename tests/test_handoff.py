"""``herjev handoff open``: URL parsing, fetch, files, launch argv and log-text safety."""

from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import time
from pathlib import Path

import httpx
import pytest
import respx

from herjev import handoff as h
from herjev.cli import build_parser

PORTAL = "https://portal.example"
FP, OCC = "0a1b2c3d4e5f6789", "abcdef012345"
HOSTILE = "it's \"quoted\" $(touch pwned) `touch pwned2` ; touch pwned3\nnext line \x1b[31mred"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERJEV_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def cfg(**over) -> h.HandoffConfig:
    return h.HandoffConfig(**{"portal_url": PORTAL, "token": "s3cret", **over})


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ---------------------------------------------------------------- URL

def test_parse_url_accepts_ticket_and_optional_occurrence():
    assert h.parse_url(f"herjev://handoff?ticket={FP}&occ={OCC}") == h.Target(FP, OCC)
    assert h.parse_url(f"herjev://handoff?ticket={FP}") == h.Target(FP, None)
    assert h.parse_url(f"herjev:handoff?ticket={FP}") == h.Target(FP, None)


@pytest.mark.parametrize("url", [
    f"https://handoff?ticket={FP}",
    f"herjev://open?ticket={FP}",
    "herjev://handoff?occ=ab",
    "herjev://handoff?ticket=XYZ",
    "herjev://handoff?ticket=ab;rm",
    f"herjev://handoff?ticket={'a' * 65}",
    f"herjev://handoff?ticket={FP}&occ=../x",
    f"herjev://handoff?ticket={FP}&ticket=ab",
])
def test_parse_url_rejects_anything_else(url):
    with pytest.raises(h.HandoffError):
        h.parse_url(url)


def test_a_portal_parameter_is_ignored():
    target = h.parse_url(f"herjev://handoff?ticket={FP}&portal=https://evil.example&token=x")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{PORTAL}/api/tickets/{FP}/handoff").respond(200, text="# b")
        h.fetch_bundle(cfg(), target)
    assert route.calls.last.request.url.host == "portal.example"


# ---------------------------------------------------------------- config

def test_config_round_trip_is_private_and_splits_a_template_string(home):
    path = h.save_config(cfg(agent="codex", terminal="kitty"))
    assert mode(path) == 0o600
    loaded = h.load_config()
    assert (loaded.agent, loaded.terminal, loaded.token) == ("codex", "kitty", "s3cret")
    data = json.loads(path.read_text())
    path.write_text(json.dumps({**data, "terminal": "custom", "template": "herdr pane run 'my pane' {launch_script}"}))
    assert h.load_config().template == ["herdr", "pane", "run", "my pane", "{launch_script}"]


def test_config_errors_are_readable(home):
    with pytest.raises(h.HandoffError, match="herjev handoff setup"):
        h.load_config()
    for bad in ({"portal_url": "http://portal.example", "token": "t"},
                {"portal_url": PORTAL, "token": ""},
                {"portal_url": PORTAL, "token": "t", "agent": "vim"},
                {"portal_url": PORTAL, "token": "t", "terminal": "custom"},
                {"portal_url": PORTAL, "token": "t", "terminal": "custom", "template": ["herdr"]},
                {"portal_url": PORTAL, "token": "t", "agent": "custom", "template": ["x", "{launch_script}"]}):
        h.write_private(h.config_path(), json.dumps(bad))
        with pytest.raises(h.HandoffError):
            h.load_config()


@pytest.mark.parametrize("url", ["http://localhost:8787", "http://127.0.0.1:8790/", "http://[::1]:8787"])
def test_http_is_allowed_only_for_loopback(url):
    assert h.check_portal_url(url) == url.rstrip("/")


# ---------------------------------------------------------------- fetch

def test_fetch_sends_the_token_to_the_configured_portal_only():
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{PORTAL}/api/tickets/{FP}/handoff", params={"occ": OCC}).respond(200, text="# bundle")
        assert h.fetch_bundle(cfg(), h.Target(FP, OCC)) == "# bundle"
    assert route.calls.last.request.headers["authorization"] == "Bearer s3cret"


@pytest.mark.parametrize("status,match", [(401, "refused the saved token"), (404, "has no ticket"), (503, "answered 503")])
def test_fetch_errors_are_readable(status, match):
    with respx.mock() as mock:
        mock.get(f"{PORTAL}/api/tickets/{FP}/handoff").respond(status, json={"error": "x"})
        with pytest.raises(h.HandoffError, match=match):
            h.fetch_bundle(cfg(), h.Target(FP))


def test_fetch_network_error_is_readable():
    with respx.mock() as mock:
        mock.get(f"{PORTAL}/api/tickets/{FP}/handoff").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(h.HandoffError, match="cannot reach the portal"):
            h.fetch_bundle(cfg(), h.Target(FP))


# ---------------------------------------------------------------- files

def test_files_are_private(tmp_path):
    hd = h.write_handoff(cfg(), h.Target(FP, OCC), "# herjev incident x\n", tmp_path)
    assert mode(hd.dir) == 0o700 and mode(hd.dir.parent) == 0o700
    assert mode(hd.prompt_file) == 0o600
    assert mode(hd.launch_script) == 0o700
    assert hd.prompt_file.read_text() == "# herjev incident x\n"
    assert hd.dir.name.startswith(f"{FP}-{OCC}-")
    assert hd.instruction == f"Read and follow the herjev incident handoff in {hd.prompt_file}."
    again = h.write_handoff(cfg(), h.Target(FP, OCC), "# y\n", tmp_path, now=time.time())
    assert again.dir != hd.dir


def test_a_custom_agent_gets_no_launch_script(tmp_path):
    hd = h.write_handoff(cfg(agent="custom", template=["paseo", "run", "{instruction}"]), h.Target(FP), "# b\n", tmp_path)
    assert hd.launch_script is None
    assert not (hd.dir / "launch.command").exists()


def test_prune_removes_only_old_handoffs(tmp_path):
    root = tmp_path / "handoff"
    old, new = root / "old", root / "new"
    for d in (old, new):
        d.mkdir(parents=True)
        (d / "handoff.md").write_text("logs")
    week_ago = time.time() - 8 * 86400
    os.utime(old, (week_ago, week_ago))
    assert h.prune(root) == [old]
    assert not old.exists() and new.exists()


# ---------------------------------------------------------------- log text never executes

def recorder(bin_dir: Path, name: str, record: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    script.write_text(f"#!/bin/sh\nprintf '%s\\0' \"$@\" > '{record}'\n")
    script.chmod(0o755)


@pytest.fixture
def hostile(tmp_path):
    """A launch script for a bundle and a workdir full of shell syntax, with ``claude``
    replaced by a recorder of its argv."""
    workdir = tmp_path / "w'q\"$(touch pwned)`touch pwned2`\nnl"
    workdir.mkdir()
    record = tmp_path / "argv"
    recorder(tmp_path / "bin", "claude", record)
    config = cfg(path=f"{tmp_path / 'bin'}:/usr/bin:/bin")
    hd = h.write_handoff(config, h.Target(FP, OCC), f"# herjev incident {HOSTILE}\n\n{HOSTILE}\n", workdir,
                         root=tmp_path / "out")
    return tmp_path, workdir, record, hd


def run_script(script: Path, stdin: bytes | None, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/sh", str(script)], input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                          capture_output=True, cwd=cwd, timeout=10)


def no_pwned(root: Path) -> bool:
    return not any(p.name.startswith("pwned") for p in root.rglob("*"))


def test_launch_script_is_valid_sh_and_runs_the_agent_with_one_argument(hostile):
    tmp, workdir, record, hd = hostile
    assert subprocess.run(["/bin/sh", "-n", str(hd.launch_script)]).returncode == 0
    result = run_script(hd.launch_script, b"\n", tmp)
    assert result.returncode == 0, result.stderr
    argv = record.read_bytes().split(b"\0")[:-1]
    assert argv == [hd.instruction.encode()]
    out = result.stdout.decode()
    assert "herjev handoff: herjev incident it's" in out
    assert "\x1b" not in out  # control characters from the log never reach the terminal
    assert no_pwned(tmp) and no_pwned(Path.cwd())


def test_launch_script_starts_nothing_without_enter(hostile):
    tmp, _, record, hd = hostile
    result = run_script(hd.launch_script, None, tmp)
    assert result.returncode != 0
    assert not record.exists()
    assert no_pwned(tmp)


def test_custom_template_placeholders_stay_one_element(tmp_path):
    workdir = tmp_path / "a b $(x)"
    workdir.mkdir()
    config = cfg(terminal="custom", template=["herdr", "pane", "run", "--cwd={workdir}", "{launch_script}", "{instruction}"])
    hd = h.write_handoff(config, h.Target(FP), "# b\n", workdir, root=tmp_path / "out")
    assert h.build_argv(config, hd) == ["herdr", "pane", "run", f"--cwd={workdir}", str(hd.launch_script), hd.instruction]
    # A value that looks like a placeholder is not substituted again.
    agent = cfg(agent="custom", template=["paseo", "run", "{instruction}", "{prompt_file}"])
    hd2 = h.write_handoff(agent, h.Target(FP), "# b\n", tmp_path / "{instruction}", root=tmp_path / "out2")
    assert h.build_argv(agent, hd2)[2:] == [hd2.instruction, str(hd2.prompt_file)]


# ---------------------------------------------------------------- presets

def test_agent_presets():
    assert h.agent_argv(cfg(agent="claude"), "do it") == ["claude", "do it"]
    assert h.agent_argv(cfg(agent="codex"), "do it") == ["codex", "do it"]
    assert h.agent_argv(cfg(agent="agy"), "do it") == ["agy", "-i", "do it"]
    assert h.agent_argv(cfg(agent="gemini"), "do it") == ["gemini", "-i", "do it"]


def test_terminal_presets(tmp_path, monkeypatch):
    hd = h.Handoff(dir=tmp_path, prompt_file=tmp_path / "handoff.md", workdir=Path("/code/x"), instruction="i",
                   launch_script=tmp_path / "launch.command")
    s = str(hd.launch_script)
    assert h.build_argv(cfg(terminal="terminal"), hd) == ["open", "-a", "Terminal", s]
    iterm = h.build_argv(cfg(terminal="iterm2"), hd)
    assert iterm[0] == "osascript" and iterm[-1] == s and "iTerm2" in " ".join(iterm)
    ghostty = h.build_argv(cfg(terminal="ghostty"), hd)
    assert ghostty[0] == "osascript" and ghostty[-2:] == [s, "/code/x"]
    monkeypatch.setattr(h, "_which", lambda name, cfg: f"/usr/bin/{name}" if name in ("wezterm", "kitty", "xterm") else None)
    assert h.build_argv(cfg(terminal="wezterm"), hd) == ["wezterm", "start", "--cwd", "/code/x", "--", s]
    assert h.build_argv(cfg(terminal="kitty"), hd) == ["kitty", "--single-instance", "--directory", "/code/x", s]
    assert h.build_argv(cfg(terminal="linux"), hd, env={"TERMINAL": "xterm"}) == ["xterm", "-e", s]
    with pytest.raises(h.HandoffError, match="no terminal"):
        h.build_argv(cfg(terminal="linux"), hd, env={})


@pytest.mark.parametrize("terminal", ["iterm2", "ghostty"])
def test_command_string_terminals_get_a_shell_quoted_path(terminal):
    """iTerm2 and Ghostty parse their `command` as a command line, so a HERJEV_HOME with
    spaces or shell characters must reach them quoted."""
    script = " ".join(h.TERMINALS[terminal])
    assert "quoted form of item 1 of argv" in script


@pytest.mark.skipif(not Path("/usr/bin/osascript").exists(), reason="needs macOS osascript")
def test_applescript_quoted_form_round_trips_through_shell_parsing():
    path = "/Users/a b/it's $(x) `y`/launch.command"
    out = subprocess.run(["osascript", "-e", "on run argv", "-e", "return quoted form of item 1 of argv",
                          "-e", "end run", path], capture_output=True, text=True, check=True).stdout.strip()
    assert shlex.split(out) == [path]


# ---------------------------------------------------------------- open flow

class FakeProc:
    def __init__(self, code=None):
        self.code = code

    def wait(self, timeout=None):
        if self.code is None:
            raise subprocess.TimeoutExpired("x", timeout)
        return self.code


class Popen:
    def __init__(self, code=None):
        self.calls: list[tuple[list[str], dict]] = []
        self.code = code

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        return FakeProc(self.code)


@pytest.fixture
def configured(home, tmp_path):
    h.save_config(cfg(last_workdir=str(tmp_path)))
    return tmp_path


def mocked_portal():
    mock = respx.mock()
    mock.get(f"{PORTAL}/api/tickets/{FP}/handoff").respond(200, text="# herjev incident boom\n")
    return mock


def test_open_asks_for_a_folder_from_the_last_choice_and_remembers_it(configured, tmp_path):
    chosen = tmp_path / "repo"
    chosen.mkdir()
    seen, popen = [], Popen()
    with mocked_portal():
        hd = h.open_handoff(f"herjev://handoff?ticket={FP}", choose=lambda d: seen.append(d) or chosen, popen=popen)
    assert seen == [tmp_path]
    assert h.load_config().last_workdir == str(chosen)
    argv, kw = popen.calls[0]
    assert argv[-1] == str(hd.launch_script) and kw["cwd"] == str(chosen) and kw["start_new_session"]
    assert f"cd {chosen}" in hd.launch_script.read_text()
    with mocked_portal():
        h.open_handoff(f"herjev://handoff?ticket={FP}", choose=lambda d: seen.append(d) or chosen, popen=popen)
    assert seen[-1] == chosen


def test_cancelling_the_folder_dialog_launches_nothing(configured, home):
    popen = Popen()
    with mocked_portal():
        assert h.open_handoff(f"herjev://handoff?ticket={FP}", choose=lambda d: None, popen=popen) is None
    assert popen.calls == []
    assert "cancelled" in (home / "handoff" / "handoff.log").read_text()


def test_a_launcher_that_fails_at_once_is_reported(configured, tmp_path):
    with mocked_portal(), pytest.raises(h.HandoffError, match="exited with 1"):
        h.open_handoff(f"herjev://handoff?ticket={FP}", choose=lambda d: tmp_path, popen=Popen(code=1))


def test_cli_open_logs_and_notifies_failures(home, monkeypatch, capsys):
    notes = []
    monkeypatch.setattr(h, "notify", notes.append)
    args = build_parser().parse_args(["handoff", "open", f"herjev://handoff?ticket={FP}"])
    assert args.func(args) == 1
    assert "herjev handoff setup" in notes[0]
    assert "herjev handoff setup" in capsys.readouterr().err
    assert "failed" in (home / "handoff" / "handoff.log").read_text()


def test_cli_open_reports_unexpected_errors_and_uses_the_baked_home(tmp_path, monkeypatch, home):
    other = tmp_path / "other-home"
    notes = []
    monkeypatch.setattr(h, "notify", notes.append)

    def boom(url):
        assert h.config_path() == other / "handoff.json"
        raise OSError("disk full")

    monkeypatch.setattr(h, "open_handoff", boom)
    args = build_parser().parse_args(["handoff", "open", "--home", str(other), f"herjev://handoff?ticket={FP}"])
    assert args.func(args) == 1
    assert notes == ["unexpected OSError: disk full"]
    assert "disk full" in (other / "handoff" / "handoff.log").read_text()


def test_handoffs_of_one_ticket_in_the_same_second_get_their_own_folders(tmp_path):
    now = time.time()
    a = h.write_handoff(cfg(), h.Target(FP), "# a\n", tmp_path, root=tmp_path / "out", now=now)
    b = h.write_handoff(cfg(), h.Target(FP), "# b\n", tmp_path, root=tmp_path / "out", now=now)
    assert a.dir != b.dir and b.dir.name.endswith("-2")


def test_choose_workdir_dialogs():
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="/Users/me/code/\n", stderr="")

    assert h.choose_workdir(Path("/Users/me"), platform="darwin", run=run) == Path("/Users/me/code")
    assert calls[0][0] == "osascript" and calls[0][-1] == "/Users/me"
    cancelled = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="User canceled. (-128)")
    assert h.choose_workdir(Path("/Users/me"), platform="darwin", run=cancelled) is None


# ---------------------------------------------------------------- runtimes

def test_runtime_config_is_checked_and_defaults_to_none(home):
    assert h.HandoffConfig(portal_url=PORTAL, token="t").runtime == "none"
    h.config_path().parent.mkdir(parents=True, exist_ok=True)
    h.config_path().write_text(json.dumps({"portal_url": PORTAL, "token": "t"}))  # saved before runtimes existed
    assert h.load_config().runtime == "none"
    for over, match in [({"runtime": "screen"}, "unknown runtime"),
                        ({"runtime": "tmux", "agent": "custom", "template": ["x"]}, "custom agent starts itself"),
                        ({"runtime": "paseo", "agent": "gemini"}, "paseo runs only claude or codex")]:
        with pytest.raises(h.HandoffError, match=match):
            cfg(**over).validate()


class Run:
    """A fake ``subprocess.run`` answering runtime CLIs by their first two words."""

    def __init__(self, answers: dict[str, list[tuple[int, str]]] | None = None):
        self.answers = answers or {}
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        key = " ".join([Path(argv[0]).name, *argv[1:3]])
        queue = next((v for k, v in self.answers.items() if key.startswith(k)), [(0, "")])
        code, out = queue.pop(0) if len(queue) > 1 else queue[0]
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr="boom" if code else "")

    def argvs(self) -> list[list[str]]:
        return [[Path(a[0]).name, *a[1:]] for a, _ in self.calls]


HERDR_CREATED = json.dumps({"result": {"root_pane": {"pane_id": "wD:p1"}, "workspace": {"workspace_id": "wD"}}})


def open_with(runtime: str, tmp_path: Path, run: Run, popen: Popen | None = None, **over) -> h.Handoff:
    h.save_config(cfg(runtime=runtime, terminal="ghostty", last_workdir=str(tmp_path), **over))
    with mocked_portal():
        return h.open_handoff(f"herjev://handoff?ticket={FP}", choose=lambda d: tmp_path,
                              popen=popen or Popen(), run=run)


def test_runtime_none_opens_the_launch_script_directly(home, tmp_path):
    run, popen = Run(), Popen()
    hd = open_with("none", tmp_path, run, popen)
    assert run.calls == [] and hd.session_script is None
    assert popen.calls[0][0][-2] == str(hd.launch_script)


def test_tmux_session_runs_the_gated_launch_script_and_nothing_from_the_log(hostile):
    tmp, workdir, record, hd = hostile
    config = cfg(runtime="tmux", path=f"{tmp / 'bin'}:/usr/bin:/bin")
    attach = h.start_runtime(config, hd, run=Run())
    h.write_session_script(config, hd, attach)
    assert mode(hd.session_script) == 0o700
    assert h.build_argv(cfg(terminal="ghostty"), hd)[-2] == str(hd.session_script)
    tmux_record = tmp / "tmux-argv"
    recorder(tmp / "bin", "tmux", tmux_record)
    assert run_script(hd.session_script, None, tmp).returncode == 0
    argv = [a.decode() for a in tmux_record.read_bytes().split(b"\0")[:-1]]
    assert argv[:2] == ["new-session", "-s"] and argv[2].startswith(f"herjev-{FP}-")
    assert argv[3:5] == ["-c", str(workdir)]
    # tmux hands its one command argument to a shell: it must start the launch script and nothing else
    result = subprocess.run(["/bin/sh", "-c", argv[5]], input=b"\n", capture_output=True, cwd=tmp, timeout=10)
    assert result.returncode == 0, result.stderr
    assert record.read_bytes().split(b"\0")[:-1] == [hd.instruction.encode()]
    assert no_pwned(tmp)


def test_herdr_creates_a_workspace_runs_the_launch_script_and_the_terminal_attaches(home, tmp_path):
    run = Run({"herdr workspace create": [(0, HERDR_CREATED)]})
    popen = Popen()
    hd = open_with("herdr", tmp_path, run, popen)
    ps, create, pane_run = run.argvs()
    assert ps[0] == "ps"
    assert create == ["herdr", "workspace", "create", "--cwd", str(tmp_path), "--label", f"herjev {FP[:8]}", "--focus"]
    assert pane_run[:4] == ["herdr", "pane", "run", "wD:p1"]
    assert shlex.split(pane_run[4]) == [str(hd.launch_script)]
    assert run.calls[0][1]["cwd"] == str(tmp_path)
    assert popen.calls[-1][0][-2] == str(hd.session_script)
    assert hd.session_script.read_text().splitlines()[-1] == "exec herdr"


def test_herdr_server_is_started_when_it_is_not_running(home, tmp_path, monkeypatch):
    monkeypatch.setattr(h.time, "sleep", lambda s: None)
    run = Run({"herdr workspace create": [(1, ""), (1, ""), (0, HERDR_CREATED)]})
    popen = Popen()
    open_with("herdr", tmp_path, run, popen)
    server, terminal = popen.calls
    assert [Path(server[0][0]).name, *server[0][1:]] == ["herdr", "server"] and server[1]["start_new_session"]
    assert [a[1] for a in run.argvs()].count("workspace") == 3


def test_a_runtime_that_never_answers_is_reported(home, tmp_path, monkeypatch):
    monkeypatch.setattr(h, "RUNTIME_START_WAIT", 0)
    run, popen = Run({"herdr workspace create": [(1, "")]}), Popen()
    with pytest.raises(h.HandoffError, match="herdr workspace create` failed with 1: boom"):
        open_with("herdr", tmp_path, run, popen)
    assert len(popen.calls) == 1  # the server start, no terminal


def test_paseo_runs_the_agent_in_its_daemon_and_the_terminal_attaches(home, tmp_path):
    run = Run({"paseo run": [(0, "ag-123\n")]})
    hd = open_with("paseo", tmp_path, run, agent="codex")
    (paseo_run,) = run.argvs()
    assert paseo_run == ["paseo", "run", "--provider", "codex", "--no-wait", "--quiet", "--title",
                         f"herjev {FP[:8]}", hd.instruction]
    assert run.calls[0][1]["cwd"] == str(tmp_path)
    assert hd.session_script.read_text().splitlines()[-1] == "exec paseo attach ag-123"


def test_paseo_daemon_is_started_once_when_run_fails(home, tmp_path, monkeypatch):
    monkeypatch.setattr(h.time, "sleep", lambda s: None)
    run = Run({"paseo run": [(1, ""), (0, "ag-1\n")]})
    open_with("paseo", tmp_path, run)
    assert [a[:3] for a in run.argvs()] == [["paseo", "run", "--provider"], ["paseo", "daemon", "start"],
                                            ["paseo", "run", "--provider"]]


def test_paseo_without_an_agent_id_is_reported(home, tmp_path):
    with pytest.raises(h.HandoffError, match="no agent id"):
        open_with("paseo", tmp_path, Run({"paseo run": [(0, "started; see the app\n")]}))


def test_orca_opens_a_terminal_in_its_own_window_and_no_terminal_app(home, tmp_path, monkeypatch):
    notes = []
    monkeypatch.setattr(h, "notify", notes.append)
    run, popen = Run(), Popen()
    hd = open_with("orca", tmp_path, run, popen)
    (create,) = run.argvs()
    assert create[:7] == ["orca", "terminal", "create", "--worktree", f"path:{tmp_path}", "--title", f"herjev {FP[:8]}"]
    assert create[7] == "--command" and shlex.split(create[8]) == [str(hd.launch_script)] and create[9] == "--json"
    assert popen.calls == [] and hd.session_script is None
    assert notes == [f"herjev {FP[:8]} is waiting in orca"]


GHOSTTY = "    10     1 ??       /Applications/Ghostty.app/Contents/MacOS/ghostty\n" \
          "    11    10 ttys000  /usr/bin/login -flp me /bin/zsh\n    12    11 ttys000  -/bin/zsh\n"
ITERM = "    20     1 ??       /Applications/iTerm.app/Contents/MacOS/iTerm2\n" \
        "    21    20 ttys001  -/bin/zsh\n"
SERVER = "     5     1 ??       /Users/me/.local/bin/herdr server\n"
HERDR_CLIENT = GHOSTTY + SERVER + "    13    12 ttys000  herdr\n"


@pytest.mark.parametrize("terminal,listing,open_", [
    ("ghostty", HERDR_CLIENT, True),
    ("ghostty", GHOSTTY + SERVER + "    13    12 ttys000  herdr workspace list\n    14    12 ttys000  vim herdr\n", False),
    ("ghostty", GHOSTTY + "    13    12 ttys000  herdr --session other\n", False),
    ("ghostty", GHOSTTY + "    13    12 ttys000  /x/herdr --session default\n", True),
    ("ghostty", GHOSTTY + "    13    12 ttys000  herdr --remote box\n", False),
    ("ghostty", ITERM + "    22    21 ttys001  herdr\n", False),   # a herdr window, but in another app
    ("iterm2", ITERM + "    22    21 ttys001  herdr\n", True),
    ("linux", ITERM + "    22    21 ttys001  herdr\n", True),     # an unknown app: any window counts
    ("kitty", "    30     1 ?        /usr/bin/kitty\n    31    30 pts/0    bash\n    32    31 pts/0    herdr\n", True),
    ("ghostty", "", False),
])
def test_an_open_herdr_window_is_a_client_in_the_configured_terminal(tmp_path, terminal, listing, open_):
    assert h.herdr_window_open(cfg(terminal=terminal), tmp_path, Run({"ps -axo": [(0, listing)]})) is open_


def test_a_herdr_window_in_another_terminal_app_gets_a_new_window_of_the_configured_one(home, tmp_path):
    run = Run({"ps -axo": [(0, ITERM + "    22    21 ttys001  herdr\n")],
               "herdr workspace create": [(0, HERDR_CREATED)]})
    popen = Popen()
    hd = open_with("herdr", tmp_path, run, popen)
    assert [a[1:3] for a in run.argvs()][1] == ["workspace", "create"]
    assert popen.calls[-1][0][-2] == str(hd.session_script)  # the Ghostty AppleScript opens session.command


def test_an_open_herdr_window_gets_a_new_tab_and_no_new_terminal(home, tmp_path, monkeypatch):
    monkeypatch.setattr(h.sys, "platform", "darwin")
    tab = json.dumps({"result": {"root_pane": {"pane_id": "wC:p9"}, "tab": {"tab_id": "wC:t9"}}})
    run, popen = Run({"ps -axo": [(0, HERDR_CLIENT)], "herdr tab create": [(0, tab)]}), Popen()
    hd = open_with("herdr", tmp_path, run, popen)
    ps, create, pane_run, activate = run.argvs()
    assert create == ["herdr", "tab", "create", "--cwd", str(tmp_path), "--label", f"herjev {FP[:8]}", "--focus"]
    assert pane_run[:4] == ["herdr", "pane", "run", "wC:p9"] and shlex.split(pane_run[4]) == [str(hd.launch_script)]
    assert activate[0] == "osascript" and activate[-1] == "Ghostty"
    assert popen.calls == [] and hd.session_script is None
