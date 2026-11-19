from __future__ import annotations

from typing import Any

from ..models import Decision, Incident, Level
from ..registry import create, register
from ..trace import step
from .base import DEFAULT_CATEGORIES, Decider, apply_policy

_SCHEMA = {
    "type": "object",
    "properties": {
        "notify": {"type": "boolean"},
        "urgency": {"type": "integer", "minimum": 0, "maximum": 3},
        "category": {"type": "string"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["notify", "urgency", "category", "reason"],
}


@register("decider", "llm")
class LLMDecider(Decider):
    """Use any configured analyzer's LLM as the decider (e.g. to run without Jev).

    ``llm:`` takes an analyzer spec such as ``{type: gemini}`` or
    ``{type: openai, base_url: ..., model: ...}``.
    """

    def __init__(self, llm: dict[str, Any], min_urgency: int = 1,
                 always_notify: list[str] | None = None):
        self.llm = create("analyzer", llm)
        self.min_urgency = min_urgency
        self.always_notify = {Level.parse(x) for x in (always_notify or ["fatal", "panic"])}

    async def decide(self, incident: Incident) -> Decision:
        cats = "\n".join(f"- {k}: {v}" for k, v in DEFAULT_CATEGORIES.items())
        system = (
            "You triage production log incidents for an on-call engineer. Decide whether "
            "to notify a human. Urgency: 0 ignore, 1 investigate soon, 2 act within the hour, "
            f"3 page now. Category must be one of:\n{cats}"
        )
        step("decide:llm", "asking %s to triage", self.llm.type_name)
        out = await self.llm.complete_json(system, {"incident": incident.to_state()}, _SCHEMA)
        d = Decision(
            notify=bool(out.get("notify")),
            urgency=max(0, min(3, int(out.get("urgency", 0)))),
            category=str(out.get("category", "unknown")),
            confidence=float(out.get("confidence", 0.5) or 0.5),
            reason="llm: " + str(out.get("reason", "")),
            provider=f"llm:{self.llm.type_name}",
            raw=out,
        )
        return apply_policy(d, incident, self.always_notify, self.min_urgency)

    async def aclose(self) -> None:
        await self.llm.aclose()
