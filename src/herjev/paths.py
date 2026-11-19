"""Filesystem locations."""

from __future__ import annotations

import os
from pathlib import Path


def herjev_home() -> Path:
    """herjev's own state dir (status, Teams token, ssh keys for ssh sources)."""
    return Path(os.environ.get("HERJEV_HOME") or "~/.herjev").expanduser()


def status_path(cfg: dict | None = None) -> Path:
    """Where the watcher writes its status: ``pipeline.status_file``, else ``$HERJEV_HOME/status.json``."""
    path = ((cfg or {}).get("pipeline") or {}).get("status_file")
    return Path(path).expanduser() if path else herjev_home() / "status.json"
