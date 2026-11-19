"""Provider registry — the extension point of herjev.

Every pluggable stage (source, decider, analyzer, notifier) is a *kind*.
Implementations register a *type name* under a kind; the YAML config then
selects implementations by ``type:``. Third-party packages can add new
providers without touching herjev by exposing an entry point in the
``herjev.providers`` group that imports a module which calls ``register``.

    @register("notifier", "pagerduty")
    class PagerDutyNotifier(Notifier): ...
"""

from __future__ import annotations

import importlib
import importlib.metadata
import logging
from typing import Any, Callable, TypeVar

log = logging.getLogger(__name__)

KINDS = ("source", "decider", "analyzer", "notifier")
_REGISTRY: dict[str, dict[str, type]] = {k: {} for k in KINDS}
_loaded_builtins = False

T = TypeVar("T", bound=type)


def register(kind: str, name: str) -> Callable[[T], T]:
    if kind not in _REGISTRY:
        raise ValueError(f"unknown provider kind {kind!r}; expected one of {KINDS}")

    def deco(cls: T) -> T:
        _REGISTRY[kind][name] = cls
        cls.type_name = name  # type: ignore[attr-defined]
        return cls

    return deco


def _load_builtins() -> None:
    global _loaded_builtins
    if _loaded_builtins:
        return
    _loaded_builtins = True
    for mod in (
        "herjev.sources", "herjev.deciders", "herjev.analyzers", "herjev.notifiers",
    ):
        importlib.import_module(mod)
    try:
        eps = importlib.metadata.entry_points()
        group = eps.select(group="herjev.providers") if hasattr(eps, "select") else eps.get("herjev.providers", [])
        for ep in group:
            try:
                ep.load()
            except Exception as exc:  # pragma: no cover - third-party failure
                log.warning("failed to load herjev provider plugin %s: %s", ep.name, exc)
    except Exception:  # pragma: no cover
        pass


def available(kind: str) -> list[str]:
    _load_builtins()
    return sorted(_REGISTRY[kind])


def registered(kind: str) -> dict[str, type]:
    """Type name → class for every provider of ``kind``, plugins included."""
    _load_builtins()
    return dict(_REGISTRY[kind])


def create(kind: str, spec: dict[str, Any], **extra: Any) -> Any:
    """Instantiate a provider from a config mapping containing ``type``."""
    _load_builtins()
    spec = dict(spec)
    type_name = spec.pop("type", None)
    if not type_name:
        raise ValueError(f"{kind} config is missing 'type': {spec}")
    cls = _REGISTRY[kind].get(type_name)
    if cls is None:
        raise ValueError(
            f"unknown {kind} type {type_name!r}; available: {', '.join(available(kind))}"
        )
    return cls(**spec, **extra)
