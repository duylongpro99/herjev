"""State and config locations, and the status command used as the Docker healthcheck."""

import json

from herjev.config import resolve_path
from herjev.paths import herjev_home
from herjev.registry import create


def test_paths_follow_herjev_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERJEV_HOME", str(tmp_path))
    monkeypatch.delenv("HERJEV_CONFIG", raising=False)
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")
    assert herjev_home() == tmp_path
    (tmp_path / "herjev.yaml").write_text("x: 1")
    assert resolve_path(None) == tmp_path / "herjev.yaml"


def test_default_home_is_dot_herjev(monkeypatch):
    from pathlib import Path
    monkeypatch.delenv("HERJEV_HOME", raising=False)
    assert herjev_home() == Path("~/.herjev").expanduser()


def test_ssh_uses_writable_known_hosts(tmp_path, monkeypatch):
    monkeypatch.setenv("HERJEV_HOME", str(tmp_path / "hj"))
    src = create("source", {"type": "ssh", "name": "s", "target": "ops@h", "journal_units": ["api"],
                            "identity_file": "/opt/herjev-ssh/id_ed25519"})
    argv = src.build_argv()
    assert f"UserKnownHostsFile={tmp_path / 'hj' / 'known_hosts'}" in argv
    assert "StrictHostKeyChecking=accept-new" in argv
    assert argv[argv.index("-i") + 1] == "/opt/herjev-ssh/id_ed25519"
    assert argv[-1] == "journalctl -f -n0 -o cat -u api"
    assert (tmp_path / "hj").is_dir()


def test_status_command(tmp_path, monkeypatch, capsys):
    import time
    from herjev.cli import main
    monkeypatch.setenv("HERJEV_HOME", str(tmp_path))
    monkeypatch.setenv("HERJEV_CONFIG", str(tmp_path / "missing.yaml"))
    assert main(["status"]) == 1
    (tmp_path / "status.json").write_text(json.dumps({"updated_at": time.time() - 500}))
    assert main(["status"]) == 0
    assert main(["status", "--max-age", "90"]) == 1
