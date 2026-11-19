"""Register ``herjev://`` with the OS so a click in the portal runs ``herjev handoff open``.

macOS delivers a URL to an app only as an Apple Event, so the handler is a tiny AppleScript
applet built with ``osacompile`` and patched to claim the scheme. Linux uses a desktop entry
for ``x-scheme-handler/herjev``. Both are generated on the developer's machine, never shipped.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .handoff import HandoffError

APP_NAME = "Herjev Handoff.app"
BUNDLE_ID = "dev.herjev.handoff"
SCHEME = "herjev"
LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/"
              "LaunchServices.framework/Support/lsregister")
DESKTOP_NAME = "herjev-handoff.desktop"

Run = Callable[[list[str]], None]


def run_command(cmd: list[str]) -> None:
    """Run ``cmd`` without a shell; a failure becomes a readable HandoffError."""
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise HandoffError(f"{cmd[0]} not found") from None
    except subprocess.CalledProcessError as exc:
        raise HandoffError(f"{Path(cmd[0]).name} failed: {(exc.stderr or exc.stdout or '').strip()[:300]}") from None


# ---------------------------------------------------------------- macOS

def applescript_string(text: str) -> str:
    """``text`` as an AppleScript string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def applet_source(herjev: str, home: str) -> str:
    """The applet: hand each herjev:// URL to ``herjev handoff open`` and return at once.
    The absolute path and HERJEV_HOME are baked in because the applet's shell does not
    load the user's profile."""
    return (
        "on open location theURL\n"
        f"  do shell script quoted form of {applescript_string(herjev)} & \" handoff open --home \" & "
        f"quoted form of {applescript_string(home)} & \" \" & "
        "quoted form of theURL & \" >/dev/null 2>&1 &\"\n"
        "end open location\n"
        "\n"
        "on run\n"
        '  display dialog "Herjev Handoff opens herjev:// links from the portal. '
        "Run 'herjev handoff setup' to reconfigure it.\" buttons {\"OK\"} default button 1\n"
        "end run\n"
    )


def patch_plist(info: dict) -> dict:
    """``info`` claiming the herjev scheme as a background app; other keys are kept."""
    return {
        **info,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleURLTypes": [{"CFBundleURLName": "herjev handoff", "CFBundleURLSchemes": [SCHEME]}],
        "LSUIElement": True,
    }


def macos_app(apps_dir: Path | None = None) -> Path:
    # Under ~/Applications: registered from elsewhere (e.g. /tmp), `open` failed with -10814.
    return (apps_dir or Path.home() / "Applications") / APP_NAME


def install_macos(herjev: str, home: str, apps_dir: Path | None = None, run: Run = run_command) -> Path:
    app = macos_app(apps_dir)
    if app.exists():
        uninstall_macos(apps_dir, run)
    app.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "handler.applescript"
        source.write_text(applet_source(herjev, home), encoding="utf-8")
        run(["osacompile", "-o", str(app), str(source)])
    info_path = app / "Contents" / "Info.plist"
    if info_path.exists():
        with open(info_path, "rb") as f:
            info = plistlib.load(f)
        with open(info_path, "wb") as f:
            plistlib.dump(patch_plist(info), f)
    # Editing Info.plist breaks the ad-hoc signature osacompile applied.
    run(["codesign", "--force", "--sign", "-", str(app)])
    run([LSREGISTER, "-f", str(app)])
    return app


def uninstall_macos(apps_dir: Path | None = None, run: Run = run_command) -> Path | None:
    app = macos_app(apps_dir)
    if not app.exists():
        return None
    try:
        run([LSREGISTER, "-u", str(app)])
    finally:
        shutil.rmtree(app, ignore_errors=True)
    return app


# ---------------------------------------------------------------- Linux

def desktop_arg(text: str) -> str:
    """One argument of a desktop entry's Exec key. The value is unescaped once as a string
    (``\\\\`` → ``\\``), then quoted per the Exec rules, so a backslash inside quotes is four."""
    if "\n" in text or "\r" in text:
        raise HandoffError(f"a path with a line break cannot go in a desktop entry: {text!r}")
    text = text.replace("%", "%%")
    if not any(c in text for c in " \t\"'\\><~|&;$*?#()`"):
        return text
    quoted = "".join("\\" + c if c in '"`$\\' else c for c in text)
    return '"' + quoted.replace("\\", "\\\\") + '"'


def desktop_entry(herjev: str, home: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Herjev Handoff\n"
        f"Exec={desktop_arg(herjev)} handoff open --home {desktop_arg(home)} %u\n"
        f"MimeType=x-scheme-handler/{SCHEME};\n"
        "NoDisplay=true\n"
        "Terminal=false\n"
    )


def linux_apps_dir(data_home: Path | None = None) -> Path:
    return (data_home or Path.home() / ".local" / "share") / "applications"


def _refresh(apps: Path, run: Run) -> None:
    # Optional: only refreshes the MIME cache, and is missing on some desktops.
    if shutil.which("update-desktop-database"):
        try:
            run(["update-desktop-database", str(apps)])
        except HandoffError:
            pass


def install_linux(herjev: str, home: str, data_home: Path | None = None, run: Run = run_command) -> Path:
    apps = linux_apps_dir(data_home)
    apps.mkdir(parents=True, exist_ok=True)
    entry = apps / DESKTOP_NAME
    entry.write_text(desktop_entry(herjev, home), encoding="utf-8")
    _refresh(apps, run)
    run(["xdg-mime", "default", DESKTOP_NAME, f"x-scheme-handler/{SCHEME}"])
    return entry


def uninstall_linux(data_home: Path | None = None, run: Run = run_command) -> Path | None:
    apps = linux_apps_dir(data_home)
    entry = apps / DESKTOP_NAME
    if not entry.exists():
        return None
    entry.unlink()
    _refresh(apps, run)
    return entry


# ---------------------------------------------------------------- per OS

def install(platform: str, herjev: str, home: str, run: Run = run_command) -> Path:
    if platform == "darwin":
        return install_macos(herjev, home, run=run)
    if platform.startswith("linux"):
        return install_linux(herjev, home, run=run)
    raise HandoffError(f"registering herjev:// is not supported on {platform}; use Copy prompt or Download .md")


def uninstall(platform: str, run: Run = run_command) -> Path | None:
    if platform == "darwin":
        return uninstall_macos(run=run)
    if platform.startswith("linux"):
        return uninstall_linux(run=run)
    return None
