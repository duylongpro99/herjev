"""YAML configuration loading with ``${ENV_VAR}`` / ``${VAR:-default}`` substitution."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from .paths import herjev_home

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

def default_paths() -> tuple[Path, ...]:
    return (
        Path("herjev.yaml"),
        herjev_home() / "herjev.yaml",
        Path.home() / ".config" / "herjev" / "herjev.yaml",
    )


def _substitute(value: Any) -> Any:
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            return os.environ.get(name, default if default is not None else "")
        return _ENV_RE.sub(repl, value)
    if isinstance(value, list):
        return [_substitute(v) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v) for k, v in value.items()}
    return value


def load_dotenv(path: Path) -> None:
    """Minimal .env loader (does not override already-set variables)."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def resolve_path(path: str | os.PathLike[str] | None) -> Path:
    if path:
        return Path(path).expanduser()
    env = os.environ.get("HERJEV_CONFIG")
    if env:
        return Path(env).expanduser()
    for p in default_paths():
        if p.expanduser().is_file():
            return p.expanduser()
    raise FileNotFoundError(
        "no herjev config found; pass --config, set HERJEV_CONFIG, or create "
        + ", ".join(str(p) for p in default_paths())
    )


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    cfg_path = resolve_path(path)
    # Secrets can live in a .env next to the config.
    load_dotenv(cfg_path.parent / ".env")
    data = yaml.safe_load(cfg_path.read_text()) or {}
    return parse_config(data)


def parse_config(data: dict[str, Any]) -> dict[str, Any]:
    data = _substitute(data)
    data.setdefault("sources", [])
    data.setdefault("channels", {})
    data.setdefault("routes", [])
    data.setdefault("pipeline", {})
    if not data["sources"]:
        raise ValueError("config must define at least one source")
    if not data["channels"]:
        raise ValueError("config must define at least one channel")
    for name, spec in data["channels"].items():
        channel_enabled(name, spec)
        channel_auto(name, spec)
    public_url(data)
    return data


_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0", ""}


def _channel_flag(name: str, spec: dict[str, Any], key: str) -> bool:
    """A channel flag: true when absent; a string (from ``${VAR}``) is read as a flag,
    and an empty one (``VAR=`` in .env) turns it off."""
    value = spec.get(key, True)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"channels.{name}.{key} must be true or false, got {value!r}")


def channel_enabled(name: str, spec: dict[str, Any]) -> bool:
    """``channels.<name>.enabled``: off means the channel is not built at all."""
    return _channel_flag(name, spec, "enabled")


def channel_auto(name: str, spec: dict[str, Any]) -> bool:
    """``channels.<name>.auto``: off keeps the channel out of every route, so it only
    receives what a person sends to it from the portal."""
    return _channel_flag(name, spec, "auto")


def public_url(cfg: dict[str, Any]) -> str | None:
    """``dashboard.public_url``: the base of the ``open:`` link in every alert."""
    url = ((cfg.get("dashboard") or {}).get("public_url") or "").strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"dashboard.public_url must start with http:// or https://, got {url!r}")
    return url
