"""Hand a portal ticket to the developer's own coding agent.

The portal's Handoff button opens ``herjev://handoff?ticket=<fp>&occ=<id>``. The OS hands
that URL to ``herjev handoff open`` (registered once by ``herjev handoff setup``), which
fetches the ticket's Markdown bundle from the configured portal, writes it to a private
file, asks which folder to work in, and opens a terminal running the agent with a short
instruction that points at the file. herjev's part ends once the terminal has started.

Log text in the bundle is untrusted. It only ever lands in a file: the URL carries two hex
ids, the portal and token come from local config, the generated ``launch.command`` quotes
every word, and custom templates run without a shell.
"""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

import httpx

from .paths import herjev_home

ID = re.compile(r"[0-9a-f]{1,64}")
KEEP_SECONDS = 7 * 86400
FETCH_TIMEOUT = 15.0
LAUNCH_GRACE = 2.0
PLACEHOLDER = re.compile(r"\{(launch_script|prompt_file|instruction|workdir)\}")

# Agent CLIs that start an interactive session seeded with a prompt; checked against each
# tool's --help or docs.
AGENTS: dict[str, list[str]] = {
    "claude": ["claude", "{instruction}"],
    "codex": ["codex", "{instruction}"],
    "agy": ["agy", "-i", "{instruction}"],
    "gemini": ["gemini", "-i", "{instruction}"],
}

# iTerm2 and Ghostty parse `command` as a command line, so the path goes in shell-quoted.
_ITERM2 = ["osascript", "-e", "on run argv",
           "-e", 'tell application "iTerm2" to create window with default profile command (quoted form of item 1 of argv)',
           "-e", 'tell application "iTerm2" to activate', "-e", "end run", "{launch_script}"]
_GHOSTTY = ["osascript", "-e", "on run argv", "-e", 'tell application "Ghostty"',
            "-e", "set cfg to new surface configuration",
            "-e", "set command of cfg to quoted form of item 1 of argv",
            "-e", "set initial working directory of cfg to item 2 of argv",
            "-e", "new window with configuration cfg", "-e", "activate",
            "-e", "end tell", "-e", "end run", "{launch_script}", "{workdir}"]

# How each terminal opens the generated launch script in a new window, without a shell.
TERMINALS: dict[str, list[str]] = {
    "terminal": ["open", "-a", "Terminal", "{launch_script}"],
    "iterm2": _ITERM2,
    "ghostty": _GHOSTTY,
    "wezterm": ["wezterm", "start", "--cwd", "{workdir}", "--", "{launch_script}"],
    "kitty": ["kitty", "--single-instance", "--directory", "{workdir}", "{launch_script}"],
    "linux": ["{terminal}", "-e", "{launch_script}"],
}
# CLIs that ship inside a macOS app bundle and are often not on PATH.
APP_BINARIES = {
    "wezterm": "/Applications/WezTerm.app/Contents/MacOS/wezterm",
    "kitty": "/Applications/kitty.app/Contents/MacOS/kitty",
}
CUSTOM = "custom"

# Where the agent's session lives. "none" runs it straight in the terminal window; the
# others start it inside a session manager, and the terminal window shows that session.
NONE = "none"
RUNTIMES = (NONE, "tmux", "herdr", "paseo", "orca")
WINDOWLESS_RUNTIMES = ("orca",)        # has its own window: no terminal app is opened
PASEO_PROVIDERS = ("claude", "codex")  # the agents paseo can run itself
RUNTIME_TIMEOUT = 30.0
RUNTIME_START_WAIT = 10.0


class HandoffError(Exception):
    """A handoff that cannot go ahead; the message is meant for the developer."""


@dataclass
class HandoffConfig:
    portal_url: str
    token: str
    agent: str = "claude"
    terminal: str = "terminal"
    runtime: str = NONE
    template: list[str] = field(default_factory=list)
    last_workdir: str = "~"
    path: str = ""   # PATH of the shell that ran setup; the URL handler starts with a minimal one

    def validate(self) -> None:
        check_portal_url(self.portal_url)
        if not self.token:
            raise HandoffError("handoff config has no token; run `herjev handoff setup`")
        if self.agent not in (*AGENTS, CUSTOM):
            raise HandoffError(f"unknown agent {self.agent!r}; use one of {', '.join([*AGENTS, CUSTOM])}")
        if self.terminal not in (*TERMINALS, CUSTOM):
            raise HandoffError(f"unknown terminal {self.terminal!r}; use one of {', '.join([*TERMINALS, CUSTOM])}")
        if self.runtime not in RUNTIMES:
            raise HandoffError(f"unknown runtime {self.runtime!r}; use one of {', '.join(RUNTIMES)}")
        if self.runtime != NONE and self.agent == CUSTOM:
            raise HandoffError("a custom agent starts itself; use runtime none")
        if self.runtime == "paseo" and self.agent not in PASEO_PROVIDERS:
            raise HandoffError(f"paseo runs only {' or '.join(PASEO_PROVIDERS)}, not {self.agent!r}")
        if CUSTOM in (self.agent, self.terminal) and not self.template:
            raise HandoffError("a custom agent or terminal needs a template")
        if self.agent == CUSTOM and any("{launch_script}" in part for part in self.template):
            raise HandoffError("with agent: custom the template starts the agent itself; it has no {launch_script}")
        if self.terminal == CUSTOM and self.agent != CUSTOM and not any("{launch_script}" in p for p in self.template):
            raise HandoffError("a custom terminal template must contain {launch_script}")


def check_portal_url(url: str) -> str:
    """``url`` without a trailing slash: https, or http only to a loopback host."""
    parts = urlsplit(url.strip())
    host = parts.hostname or ""
    if parts.scheme == "https" and host:
        return url.strip().rstrip("/")
    if parts.scheme == "http" and _loopback(host):
        return url.strip().rstrip("/")
    raise HandoffError(f"portal URL must be https:// (http:// only for localhost), got {url!r}")


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def config_path() -> Path:
    return herjev_home() / "handoff.json"


def handoff_root() -> Path:
    return herjev_home() / "handoff"


def load_config(path: Path | None = None) -> HandoffConfig:
    path = path or config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HandoffError(f"handoff is not set up ({path} missing); run `herjev handoff setup`") from None
    except (OSError, ValueError) as exc:
        raise HandoffError(f"cannot read {path}: {exc}") from None
    if not isinstance(data, dict):
        raise HandoffError(f"{path} must hold a JSON object")
    template = data.get("template") or []
    if isinstance(template, str):
        template = shlex.split(template)
    if not isinstance(template, list) or not all(isinstance(t, str) for t in template):
        raise HandoffError(f"{path}: template must be a list of strings or one string")
    known = {k: data[k] for k in ("portal_url", "token", "agent", "terminal", "runtime", "last_workdir", "path")
             if k in data}
    try:
        cfg = HandoffConfig(**{"portal_url": "", "token": "", **known, "template": template})
    except TypeError as exc:
        raise HandoffError(f"{path}: {exc}") from None
    cfg.validate()
    cfg.portal_url = check_portal_url(cfg.portal_url)
    return cfg


def write_private(path: Path, text: str, mode: int = 0o600) -> None:
    """Write ``text`` to ``path`` atomically, readable by its owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique temp name (created 0600): two handoffs saving at once cannot interleave.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_config(cfg: HandoffConfig, path: Path | None = None) -> Path:
    path = path or config_path()
    write_private(path, json.dumps(asdict(cfg), indent=2) + "\n")
    return path


# ---------------------------------------------------------------- URL and fetch

@dataclass(frozen=True)
class Target:
    ticket: str
    occ: str | None = None


def parse_url(url: str) -> Target:
    """The ticket and occurrence of a ``herjev://handoff?ticket=<hex>[&occ=<hex>]`` link.

    Any page can open a herjev:// link, so nothing else in it is used: the portal and the
    token always come from the local config."""
    parts = urlsplit(url)
    action = parts.netloc or parts.path.strip("/")
    if parts.scheme != "herjev" or action != "handoff":
        raise HandoffError(f"not a herjev handoff link: {url[:200]!r}")
    query = parse_qs(parts.query, keep_blank_values=True)

    def one(name: str, required: bool) -> str | None:
        values = query.get(name) or []
        if not values:
            if required:
                raise HandoffError(f"handoff link has no {name}")
            return None
        if len(values) > 1 or not ID.fullmatch(values[0]):
            raise HandoffError(f"handoff link has an invalid {name}")
        return values[0]

    return Target(one("ticket", True), one("occ", False))


def fetch_bundle(cfg: HandoffConfig, target: Target, transport: httpx.BaseTransport | None = None) -> str:
    """The ticket's handoff Markdown from the configured portal."""
    url = f"{cfg.portal_url}/api/tickets/{target.ticket}/handoff"
    params = {"occ": target.occ} if target.occ else None
    try:
        with httpx.Client(timeout=FETCH_TIMEOUT, transport=transport) as client:
            resp = client.get(url, params=params, headers={"Authorization": f"Bearer {cfg.token}"})
    except httpx.HTTPError as exc:
        raise HandoffError(f"cannot reach the portal at {cfg.portal_url}: {exc}") from None
    if resp.status_code == 401:
        raise HandoffError("the portal refused the saved token; run `herjev handoff setup` again")
    if resp.status_code == 404:
        raise HandoffError(f"the portal has no ticket {target.ticket}"
                           + (f" with occurrence {target.occ}" if target.occ else ""))
    if resp.status_code != 200:
        raise HandoffError(f"the portal answered {resp.status_code} for ticket {target.ticket}")
    return resp.text


# ---------------------------------------------------------------- files

@dataclass
class Handoff:
    dir: Path
    prompt_file: Path
    workdir: Path
    instruction: str
    launch_script: Path | None = None
    session_script: Path | None = None   # what the terminal opens when a runtime holds the agent

    @property
    def terminal_script(self) -> Path | None:
        return self.session_script or self.launch_script


def instruction_for(prompt_file: Path) -> str:
    return f"Read and follow the herjev incident handoff in {prompt_file}."


def _title(bundle: str) -> str:
    first = bundle.split("\n", 1)[0].removeprefix("# ").strip()
    # Printed in the terminal: drop control characters so log text cannot send escape codes.
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", first)[:200] or "herjev incident"


def agent_argv(cfg: HandoffConfig, instruction: str) -> list[str]:
    return [instruction if part == "{instruction}" else part for part in AGENTS[cfg.agent]]


def launch_script_text(cfg: HandoffConfig, workdir: Path, prompt_file: Path, title: str, argv: list[str]) -> str:
    """A POSIX shell script that shows the ticket, waits for Enter, then ``exec``s the agent.
    Every value is a single ``shlex.quote``d word, so nothing in it is ever evaluated."""
    q = shlex.quote
    lines = _script_head(cfg)
    lines += [
        f"cd {q(str(workdir))} || exit 1",
        f"printf '%s\\n' {q('herjev handoff: ' + title)} {q(f'context: {prompt_file}')} "
        f"{q('agent: ' + shlex.join(argv))}",
        f"printf '%s' {q('Nhấn Enter để bắt đầu, Ctrl-C để huỷ… ')}",
        "read -r _ || exit 1",
        "exec " + shlex.join(argv),
        "",
    ]
    return "\n".join(lines)


def _script_head(cfg: HandoffConfig) -> list[str]:
    lines = ["#!/bin/sh"]
    if cfg.path:
        lines.append(f"PATH={shlex.quote(cfg.path)}; export PATH")
    return lines


def write_session_script(cfg: HandoffConfig, h: Handoff, argv: list[str]) -> Path:
    """``session.command``: the script the terminal opens to show the runtime's session."""
    h.session_script = h.dir / "session.command"
    lines = [*_script_head(cfg), f"cd {shlex.quote(str(h.workdir))} || exit 1", "exec " + shlex.join(argv), ""]
    write_private(h.session_script, "\n".join(lines), mode=0o700)
    return h.session_script


def write_handoff(cfg: HandoffConfig, target: Target, bundle: str, workdir: Path,
                  root: Path | None = None, now: float | None = None) -> Handoff:
    """Write the bundle, and the launch script unless a custom agent starts itself."""
    root = root or handoff_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    base = root / f"{target.ticket}-{target.occ or 'latest'}-{stamp}"
    d, n = base, 1
    while d.exists():
        n += 1
        d = base.with_name(f"{base.name}-{n}")
    while True:
        try:
            d.mkdir(mode=0o700)
            break
        except FileExistsError:  # another handoff of this ticket in the same second
            n += 1
            d = base.with_name(f"{base.name}-{n}")
    os.chmod(d, 0o700)
    prompt_file = d / "handoff.md"
    write_private(prompt_file, bundle)
    h = Handoff(dir=d, prompt_file=prompt_file, workdir=workdir, instruction=instruction_for(prompt_file))
    if cfg.agent != CUSTOM:
        h.launch_script = d / "launch.command"
        write_private(h.launch_script,
                      launch_script_text(cfg, workdir, prompt_file, _title(bundle), agent_argv(cfg, h.instruction)),
                      mode=0o700)
    return h


def prune(root: Path | None = None, now: float | None = None, keep: float = KEEP_SECONDS) -> list[Path]:
    """Delete handoff directories older than ``keep``: they hold production logs."""
    root = root or handoff_root()
    now = time.time() if now is None else now
    removed = []
    if not root.is_dir():
        return removed
    for d in root.iterdir():
        try:
            old = d.is_dir() and not d.is_symlink() and now - d.stat().st_mtime > keep
        except OSError:  # removed by a handoff pruning at the same time
            continue
        if old:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d)
    return removed


# ---------------------------------------------------------------- launch

def _which(name: str, cfg: HandoffConfig) -> str | None:
    return shutil.which(name, path=cfg.path or None) or shutil.which(name)


def _linux_terminal(cfg: HandoffConfig, env: dict[str, str]) -> str:
    for name in (env.get("TERMINAL", ""), "x-terminal-emulator"):
        if name and _which(name, cfg):
            return name
    raise HandoffError("no terminal found: set $TERMINAL or install x-terminal-emulator")


def build_argv(cfg: HandoffConfig, h: Handoff, env: dict[str, str] | None = None) -> list[str]:
    """The command that starts the handoff: a terminal opening the launch script, or the
    custom template. Placeholders are replaced inside each argv element, never re-split."""
    env = os.environ if env is None else env
    values = {"prompt_file": str(h.prompt_file), "instruction": h.instruction, "workdir": str(h.workdir),
              "launch_script": str(h.terminal_script or "")}
    if CUSTOM in (cfg.agent, cfg.terminal):
        template = cfg.template
    else:
        template = TERMINALS[cfg.terminal]
    argv = [PLACEHOLDER.sub(lambda m: values[m[1]], part) for part in template]
    if cfg.terminal == "linux" and template is TERMINALS["linux"]:
        argv[0] = _linux_terminal(cfg, env)
    if cfg.terminal in APP_BINARIES and template is TERMINALS[cfg.terminal] and not _which(argv[0], cfg):
        if Path(APP_BINARIES[cfg.terminal]).exists():
            argv[0] = APP_BINARIES[cfg.terminal]
    return argv


Runner = Callable[..., "subprocess.Popen"]


def launch(cfg: HandoffConfig, argv: list[str], workdir: Path, popen: Runner = subprocess.Popen,
           grace: float = LAUNCH_GRACE) -> None:
    """Start ``argv`` detached; one that exits non-zero within ``grace`` seconds failed."""
    env = dict(os.environ)
    if cfg.path:
        env["PATH"] = cfg.path
    exe = _which(argv[0], cfg) or argv[0]
    try:
        proc = popen([exe, *argv[1:]], cwd=str(workdir), env=env, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise HandoffError(f"cannot start {argv[0]}: {exc}") from None
    try:
        code = proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        return
    if code != 0:
        raise HandoffError(f"{argv[0]} exited with {code}")


# ---------------------------------------------------------------- runtimes

Run = Callable[..., "subprocess.CompletedProcess"]


def _cli(cfg: HandoffConfig, argv: list[str], cwd: Path, run: Run) -> str:
    """Run a runtime's CLI to completion (no shell); its stdout, or a HandoffError."""
    env = dict(os.environ)
    if cfg.path:
        env["PATH"] = cfg.path
    exe = _which(argv[0], cfg) or argv[0]
    try:
        result = run([exe, *argv[1:]], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                     capture_output=True, text=True, timeout=RUNTIME_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HandoffError(f"cannot run {argv[0]}: {exc}") from None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise HandoffError(f"`{' '.join(argv[:3])}` failed with {result.returncode}"
                           + (f": {detail[-1][:300]}" if detail else ""))
    return result.stdout


def _after_start(cfg: HandoffConfig, h: Handoff, argv: list[str], run: Run, start: Callable[[], object]) -> str:
    """Run ``argv``; if it fails (the runtime's server is usually just not running), start
    the server and retry until it answers or ``RUNTIME_START_WAIT`` passes."""
    try:
        return _cli(cfg, argv, h.workdir, run)
    except HandoffError:
        start()
    deadline = time.monotonic() + RUNTIME_START_WAIT
    while True:
        try:
            return _cli(cfg, argv, h.workdir, run)
        except HandoffError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def _label(h: Handoff) -> str:
    return f"herjev {h.dir.name.split('-', 1)[0][:8]}"


# The app to bring forward when the agent opens in a window that is already on screen.
TERMINAL_APPS = {"terminal": "Terminal", "iterm2": "iTerm2", "ghostty": "Ghostty", "wezterm": "WezTerm",
                 "kitty": "kitty"}


# How a terminal app shows up among a process's ancestors: a macOS app bundle in the path,
# or the executable's name on Linux. Terminals not listed (linux, custom) cannot be told
# apart, so any herdr window counts for them.
TERMINAL_HOSTS = {"terminal": ("/Terminal.app/",), "iterm2": ("/iTerm.app/",),
                  "ghostty": ("/Ghostty.app/", "ghostty"), "wezterm": ("/WezTerm.app/", "wezterm-gui", "wezterm"),
                  "kitty": ("/kitty.app/", "kitty")}


def _herdr_client(command: str) -> bool:
    """A ``herdr`` client of the default session: no subcommand (the server is one), and
    not attached to another session or a remote machine."""
    words = command.split()
    if not words or Path(words[0]).name != "herdr":
        return False
    args = words[1:]
    if args and not args[0].startswith("-"):
        return False
    if "--remote" in args or "--no-session" in args:
        return False
    return "--session" not in args or args[args.index("--session") + 1:][:1] == ["default"]


def _hosted_by(terminal: str, commands: list[str]) -> bool:
    markers = TERMINAL_HOSTS.get(terminal)
    if markers is None:
        return True
    for command in commands:
        exe = command.split()[0] if command.split() else ""
        name = Path(exe.lstrip("-")).name
        if any(m in command if m.startswith("/") else m == name for m in markers):
            return True
    return False


def herdr_window_open(cfg: HandoffConfig, cwd: Path, run: Run) -> bool:
    """Whether a window of the configured terminal app shows the default herdr session: a
    ``herdr`` client on a tty whose ancestors include that app. A herdr window in another
    terminal app does not count, so the handoff opens a window of the configured one."""
    try:
        listing = _cli(cfg, ["ps", "-axo", "pid=,ppid=,tty=,command="], cwd, run)
    except HandoffError:
        return False
    procs: dict[str, tuple[str, str, str]] = {}
    for line in listing.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4:
            procs[parts[0]] = (parts[1], parts[2], parts[3])
    for ppid, tty, command in procs.values():
        if tty in ("?", "??") or not _herdr_client(command):
            continue
        ancestors, seen = [], set()
        while ppid in procs and ppid not in seen:
            seen.add(ppid)
            ancestors.append(procs[ppid][2])
            ppid = procs[ppid][0]
        if _hosted_by(cfg.terminal, ancestors):
            return True
    return False


def _bring_forward(cfg: HandoffConfig, cwd: Path, run: Run) -> None:
    """Best effort: raise the configured terminal app, where the herdr window usually is."""
    app = TERMINAL_APPS.get(cfg.terminal)
    if sys.platform != "darwin" or not app:
        return
    try:
        _cli(cfg, ["osascript", "-e", "on run argv", "-e", "tell application (item 1 of argv) to activate",
                   "-e", "end run", app], cwd, run)
    except HandoffError:
        pass


def start_runtime(cfg: HandoffConfig, h: Handoff, run: Run = subprocess.run,
                  popen: Runner = subprocess.Popen) -> list[str] | None:
    """Start the agent's session inside ``cfg.runtime`` (not ``none``). Returns the command
    a terminal runs to show that session, or None when it is already on screen: in a new
    tab of the open herdr window, or in Orca's own window."""
    script = shlex.quote(str(h.launch_script))  # each runtime below hands it to a shell
    if cfg.runtime == "tmux":
        return ["tmux", "new-session", "-s", f"herjev-{h.dir.name}", "-c", str(h.workdir), script]
    if cfg.runtime == "herdr" and herdr_window_open(cfg, h.workdir, run):
        out = _cli(cfg, ["herdr", "tab", "create", "--cwd", str(h.workdir), "--label", _label(h), "--focus"],
                   h.workdir, run)
        _cli(cfg, ["herdr", "pane", "run", _pane_id(out), script], h.workdir, run)
        _bring_forward(cfg, h.workdir, run)
        return None
    if cfg.runtime == "herdr":
        def serve() -> None:
            env = {**os.environ, **({"PATH": cfg.path} if cfg.path else {})}
            popen([_which("herdr", cfg) or "herdr", "server"], cwd=str(h.workdir), env=env, start_new_session=True,
                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        out = _after_start(cfg, h, ["herdr", "workspace", "create", "--cwd", str(h.workdir),
                                    "--label", _label(h), "--focus"], run, serve)
        _cli(cfg, ["herdr", "pane", "run", _pane_id(out), script], h.workdir, run)
        return ["herdr"]
    if cfg.runtime == "paseo":
        out = _after_start(cfg, h, ["paseo", "run", "--provider", cfg.agent, "--no-wait", "--quiet",
                                    "--title", _label(h), h.instruction], run,
                           lambda: _cli(cfg, ["paseo", "daemon", "start"], h.workdir, run))
        agent_id = (out.strip().splitlines() or [""])[-1].strip()
        if not re.fullmatch(r"[\w.:-]{1,128}", agent_id):
            raise HandoffError(f"paseo started no agent id: {out.strip()[:200]!r}")
        return ["paseo", "attach", agent_id]
    # orca: a terminal in the Orca app, which starts the runtime when it is not open
    _after_start(cfg, h, ["orca", "terminal", "create", "--worktree", f"path:{h.workdir}", "--title", _label(h),
                          "--command", script, "--json"], run,
                 lambda: _cli(cfg, ["orca", "open", "--json"], h.workdir, run))
    notify(f"{_label(h)} is waiting in orca")
    return None


def _pane_id(out: str) -> str:
    """The new pane of a herdr ``workspace create`` or ``tab create`` answer."""
    try:
        return str(json.loads(out)["result"]["root_pane"]["pane_id"])
    except (ValueError, KeyError, TypeError):
        raise HandoffError("herdr returned no pane id for the new tab") from None


# ---------------------------------------------------------------- folder chooser

Chooser = Callable[[Path], "Path | None"]

_CHOOSE_FOLDER = ["osascript", "-e", "on run argv", "-e", "activate",
                  "-e", 'POSIX path of (choose folder with prompt "herjev: mở agent ở thư mục nào?" '
                        "default location (POSIX file (item 1 of argv)))",
                  "-e", "end run"]


def choose_workdir(default: Path, platform: str = sys.platform, run: Callable = subprocess.run) -> Path | None:
    """Ask which folder the agent works in, starting at ``default``; None when cancelled.
    Without a dialog tool (Linux with no zenity) the default is used without asking."""
    if platform == "darwin":
        cmd = [*_CHOOSE_FOLDER, str(default)]
    elif shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--directory", f"--filename={default}/",
               "--title=herjev: mở agent ở thư mục nào?"]
    else:
        return default
    result = run(cmd, capture_output=True, text=True)
    chosen = result.stdout.strip()
    if result.returncode != 0 or not chosen:
        return None
    return Path(chosen.rstrip("/") or "/")


# ---------------------------------------------------------------- reporting

def _log(message: str) -> None:
    root = handoff_root()
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(root / "handoff.log", "a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}\n")
    except OSError:
        pass


def notify(message: str, platform: str = sys.platform, run: Callable = subprocess.run) -> None:
    """Show ``message`` as a desktop notification; the URL handler has no window of its own."""
    if platform == "darwin":
        cmd = ["osascript", "-e", "on run argv",
               "-e", 'display notification (item 1 of argv) with title "herjev handoff"',
               "-e", "end run", message]
    elif shutil.which("notify-send"):
        cmd = ["notify-send", "herjev handoff", message]
    else:
        return
    try:
        run(cmd, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def open_handoff(url: str, *, choose: Chooser = choose_workdir, popen: Runner = subprocess.Popen,
                 run: Run = subprocess.run, transport: httpx.BaseTransport | None = None,
                 config: Path | None = None, root: Path | None = None) -> Handoff | None:
    """Run one handoff for ``url``; None when the developer cancelled the folder dialog."""
    target = parse_url(url)
    cfg = load_config(config)
    bundle = fetch_bundle(cfg, target, transport)
    prune(root)
    default = Path(cfg.last_workdir or "~").expanduser()
    if not default.is_dir():
        default = Path.home()
    workdir = choose(default)
    if workdir is None:
        _log(f"cancelled {target.ticket}: no folder chosen")
        return None
    if not workdir.is_dir():
        raise HandoffError(f"{workdir} is not a folder")
    cfg.last_workdir = str(workdir)
    save_config(cfg, config)
    h = write_handoff(cfg, target, bundle, workdir, root)
    attach = start_runtime(cfg, h, run, popen) if cfg.runtime != NONE else None
    if cfg.runtime == NONE or attach:
        if attach:
            write_session_script(cfg, h, attach)
        launch(cfg, build_argv(cfg, h), workdir, popen)
    _log(f"started {target.ticket} occ={target.occ or 'latest'} in {workdir} via {cfg.runtime} ({h.dir})")
    return h


def cmd_open(args: argparse.Namespace) -> int:
    if args.home:
        # The URL handler starts without the user's profile, so setup bakes HERJEV_HOME in.
        os.environ["HERJEV_HOME"] = args.home
    try:
        open_handoff(args.url)
    except Exception as exc:
        # The handler has no window and discards output: the log and notification are all
        # the developer sees. Messages name the problem, never the config or token.
        message = str(exc) if isinstance(exc, HandoffError) else f"unexpected {type(exc).__name__}: {exc}"
        _log(f"failed: {message}")
        notify(message)
        print(f"herjev handoff: {message}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------- setup

MAC_TERMINAL_APPS = {"iterm2": "iTerm.app", "ghostty": "Ghostty.app", "wezterm": "WezTerm.app", "kitty": "kitty.app"}


def detect_agents(path: str | None = None) -> list[str]:
    return [name for name in AGENTS if shutil.which(name, path=path)]


def detect_runtimes(agent: str, path: str | None = None) -> list[str]:
    """``none`` first, then the session managers on PATH that can hold ``agent``."""
    if agent == CUSTOM:
        return [NONE]
    return [NONE, *(r for r in RUNTIMES[1:] if shutil.which(r, path=path)
                    and (r != "paseo" or agent in PASEO_PROVIDERS))]


def detect_terminals(platform: str = sys.platform, env: dict[str, str] | None = None,
                     apps: Path = Path("/Applications")) -> list[str]:
    """Terminals installed here, the default first."""
    env = os.environ if env is None else env
    if platform == "darwin":
        found = ["terminal"] + [t for t, app in MAC_TERMINAL_APPS.items()
                                if (apps / app).exists() or (Path.home() / "Applications" / app).exists()]
    else:
        found = ["linux"] if env.get("TERMINAL") or shutil.which("x-terminal-emulator") else []
        found += [t for t in ("wezterm", "kitty") if shutil.which(t)]
    return found


def herjev_executable(argv0: str | None = None) -> str:
    """The absolute path of this ``herjev``, kept unresolved so a stable symlink stays usable."""
    argv0 = argv0 if argv0 is not None else sys.argv[0]
    if Path(argv0).name == "herjev" and Path(argv0).exists():
        return os.path.abspath(argv0)
    found = shutil.which("herjev")
    if not found:
        raise HandoffError("cannot find the herjev executable; install it on PATH (e.g. `uv tool install .`)")
    return os.path.abspath(found)


def check_token(portal_url: str, token: str, transport: httpx.BaseTransport | None = None) -> None:
    """Refuse a token the portal does not accept, before anything is saved."""
    try:
        with httpx.Client(timeout=FETCH_TIMEOUT, transport=transport) as client:
            resp = client.get(f"{portal_url}/api/status", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise HandoffError(f"cannot reach the portal at {portal_url}: {exc}") from None
    if resp.status_code == 401:
        raise HandoffError("the portal refused this token; nothing was saved")
    if resp.status_code != 200:
        raise HandoffError(f"the portal answered {resp.status_code} on /api/status; nothing was saved")


TOKEN_HINT = ("switch the keyboard to a plain English layout (a Vietnamese input method sends broken bytes "
              "to a hidden prompt) and paste the token again, or set $HERJEV_DASHBOARD_TOKEN")


def read_token(secret: Callable[[str], str]) -> str:
    """The token typed at a hidden prompt. A bearer token is printable ASCII; anything else
    is a broken paste or an input method's output and would only fail later and less clearly."""
    try:
        token = secret("Portal token (hidden): ").strip()
    except UnicodeDecodeError:
        raise HandoffError(f"the token arrived as invalid text; {TOKEN_HINT}") from None
    if not all("!" <= c <= "~" for c in token):
        raise HandoffError(f"the token holds spaces or non-ASCII characters; {TOKEN_HINT}")
    return token


def _choose(label: str, options: list[str], default: str, ask: Callable[[str], str]) -> str:
    print(f"{label}:")
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o}{'  (default)' if o == default else ''}")
    while True:
        answer = ask(f"{label} [{default}]: ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]
        if answer in options:
            return answer
        print(f"  choose 1-{len(options)} or a name")


def setup(args: argparse.Namespace, *, ask: Callable[[str], str] = input, secret: Callable[[str], str] = getpass.getpass,
          env: dict[str, str] | None = None, platform: str = sys.platform, transport: httpx.BaseTransport | None = None,
          register: Callable[[str, str], Path] | None = None, herjev: str | None = None) -> HandoffConfig:
    """Ask for (or take from flags) the portal, token, agent, runtime and terminal; check the token;
    save ``handoff.json`` (0600); register the herjev:// handler for this OS."""
    from . import handoff_install

    env = os.environ if env is None else env
    try:
        previous = load_config()
    except HandoffError:
        previous = None

    portal = args.portal_url or ask(f"Portal URL{f' [{previous.portal_url}]' if previous else ''}: ").strip()
    portal = check_portal_url(portal or (previous.portal_url if previous else ""))

    agents = [*detect_agents(env.get("PATH")), CUSTOM]
    default_agent = previous.agent if previous and previous.agent in agents else agents[0]
    agent = args.agent or _choose("Agent", agents, default_agent, ask)

    runtimes = detect_runtimes(agent, env.get("PATH"))
    default_runtime = previous.runtime if previous and previous.runtime in runtimes else NONE
    runtime = args.runtime or (default_runtime if len(runtimes) == 1
                               else _choose("Runtime", runtimes, default_runtime, ask))

    terminals = [*detect_terminals(platform, env), CUSTOM]
    default_terminal = previous.terminal if previous and previous.terminal in terminals else terminals[0]
    no_window = agent == CUSTOM or runtime in WINDOWLESS_RUNTIMES
    terminal = args.terminal if args.terminal or no_window else _choose("Terminal", terminals, default_terminal, ask)
    terminal = terminal or default_terminal

    if args.template and CUSTOM not in (agent, terminal):
        raise HandoffError("--template is used only with --agent custom or --terminal custom")
    template: list[str] = []
    if CUSTOM in (agent, terminal):
        if args.template:
            raw = args.template
        else:
            hint = "{instruction}, {prompt_file}, {workdir}" if agent == CUSTOM else "{launch_script}, {workdir}"
            raw = ask(f"Template (placeholders: {hint}): ")
        template = shlex.split(raw)

    # Never a flag: it would land in shell history.
    token = env.get("HERJEV_DASHBOARD_TOKEN") or read_token(secret)
    if not token and previous and previous.portal_url == portal:
        token = previous.token
    cfg = HandoffConfig(portal_url=portal, token=token, agent=agent, terminal=terminal, runtime=runtime,
                        template=template,
                        last_workdir=previous.last_workdir if previous else "~", path=env.get("PATH", ""))
    cfg.validate()
    check_token(cfg.portal_url, cfg.token, transport)
    path = save_config(cfg)
    print(f"saved {path}")
    location = (register or (lambda p, exe: handoff_install.install(p, exe, str(herjev_home()))))(
        platform, herjev or herjev_executable())
    print(f"herjev:// links now open with {location}")
    return cfg


def cmd_setup(args: argparse.Namespace) -> int:
    from . import handoff_install

    if args.purge and not args.uninstall:
        print("herjev handoff setup: --purge works only with --uninstall", file=sys.stderr)
        return 2
    try:
        if args.uninstall:
            removed = handoff_install.uninstall(sys.platform)
            print(f"removed {removed}" if removed else "no herjev:// handler was registered")
            if args.purge and config_path().exists():
                config_path().unlink()
                print(f"removed {config_path()}")
            return 0
        setup(args)
    except HandoffError as exc:
        print(f"herjev handoff setup: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\ncancelled; nothing changed", file=sys.stderr)
        return 1
    return 0


def add_cli(sub: argparse._SubParsersAction) -> None:
    """``herjev handoff …``"""
    p = sub.add_parser("handoff", help="hand a portal ticket to your coding agent")
    hs = p.add_subparsers(dest="handoff_cmd", required=True)

    s = hs.add_parser("setup", help="configure the handoff and register herjev:// links (run once per machine)",
                      description="The token is read from $HERJEV_DASHBOARD_TOKEN or asked for; never pass it as a flag.")
    s.add_argument("--portal-url", help="the portal, e.g. https://herjev.example.workers.dev")
    s.add_argument("--agent", choices=[*AGENTS, CUSTOM])
    s.add_argument("--terminal", choices=[*TERMINALS, CUSTOM])
    s.add_argument("--runtime", choices=RUNTIMES,
                   help="session manager that holds the agent; the terminal shows its session (default: none)")
    s.add_argument("--template", help="argv template for agent or terminal 'custom' (split like a shell, run without one)")
    s.add_argument("--uninstall", action="store_true", help="unregister herjev:// and delete the handler")
    s.add_argument("--purge", action="store_true", help="with --uninstall, also delete handoff.json")
    s.set_defaults(func=cmd_setup)

    o = hs.add_parser("open", help="open a herjev://handoff link (the URL handler runs this)")
    o.add_argument("url")
    o.add_argument("--home", help=argparse.SUPPRESS)  # set by the registered handler
    o.set_defaults(func=cmd_open)
