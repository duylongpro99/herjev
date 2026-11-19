from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from ..models import Decision, Incident, Level, conform
from ..registry import create, register
from ..trace import step

log = logging.getLogger(__name__)

DEFAULT_CATEGORIES: dict[str, str] = {
    "application_bug": "Unhandled exception, nil/null dereference, logic error, failed assertion in app code",
    "database": "Database connection, query, deadlock, migration, replication or pool exhaustion problems",
    "network": "Timeouts, DNS, TLS, connection refused/reset, upstream unreachable",
    "resource_exhaustion": "Out of memory, disk full, file descriptors, CPU throttling, OOMKilled",
    "dependency": "Third-party API or internal downstream service returning errors",
    "configuration": "Missing/invalid config, secrets, env vars, permissions, feature flags",
    "security": "Auth failures, suspicious access, certificate expiry, injection attempts",
    "noise": "Benign or expected message that does not need a human",
}


class Decider(ABC):
    """Decides whether an Incident deserves a notification and how urgent it is."""

    type_name = "base"

    @abstractmethod
    async def decide(self, incident: Incident) -> Decision:
        ...

    async def aclose(self) -> None:
        pass


def apply_policy(decision: Decision, incident: Incident, always_notify: set[Level],
                 min_urgency: int) -> Decision:
    """Common guardrails applied on top of any model's verdict."""
    before = (decision.notify, decision.urgency)
    if incident.level in always_notify and not decision.notify:
        decision.notify = True
        decision.urgency = max(decision.urgency, 2)
        decision.reason = (decision.reason + "; " if decision.reason else "") + \
            f"forced: {incident.level.label} is always notified"
    if decision.notify and decision.urgency < min_urgency and incident.level not in always_notify:
        decision.notify = False
        decision.reason = (decision.reason + "; " if decision.reason else "") + \
            f"urgency {decision.urgency} below min_urgency {min_urgency}"
    after = (decision.notify, decision.urgency)
    step("policy", "always_notify=%s min_urgency=%d: %s", sorted(lv.label for lv in always_notify),
         min_urgency, "no change" if after == before else
         f"notify {before[0]}→{after[0]}, urgency {before[1]}→{after[1]}")
    return decision


@register("decider", "chain")
class ChainDecider(Decider):
    """Try deciders in order; the first that doesn't raise wins.

    ``type: chain`` + ``deciders: [{type: jev, ...}, {type: rules}]`` gives you
    Jev with an offline safety net.
    """

    def __init__(self, deciders: list[dict[str, Any]]):
        if not deciders:
            raise ValueError("chain decider needs at least one entry")
        self.deciders: list = []
        for spec in deciders:
            try:
                self.deciders.append(create("decider", spec))
            except Exception as exc:  # e.g. missing API key: degrade to the next entry
                log.warning("decider %s unavailable: %s", spec.get("type"), exc)
        if not self.deciders:
            raise ValueError("no decider in the chain could be initialised")

    async def decide(self, incident: Incident) -> Decision:
        last: Exception | None = None
        for d in self.deciders:
            step("decide", "trying %s", d.type_name)
            try:
                return conform(await d.decide(incident), Decision, f"decider {d.type_name}")
            except Exception as exc:
                log.warning("decider %s failed: %s; trying next", d.type_name, exc)
                last = exc
        raise RuntimeError(f"all deciders failed: {last}")

    async def aclose(self) -> None:
        for d in self.deciders:
            await d.aclose()
