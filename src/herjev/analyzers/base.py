from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import asdict
from typing import Any

from ..models import URGENCY_LABELS, Analysis, Decision, Incident, conform
from ..registry import create, register
from ..trace import step

log = logging.getLogger(__name__)

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "One or two sentences: what is going wrong, in plain language"},
        "probable_causes": {"type": "array", "items": {"type": "string"}, "description": "Most likely root causes, most likely first"},
        "impact": {"type": "string", "description": "Who/what is affected and how badly"},
        "recommended_actions": {"type": "array", "items": {"type": "string"}, "description": "Concrete ordered steps to mitigate then fix"},
        "diagnostic_commands": {"type": "array", "items": {"type": "string"}, "description": "Shell commands/queries to confirm the cause"},
        "confidence": {"type": "number", "description": "0..1 confidence in the diagnosis"},
    },
    "required": ["summary", "probable_causes", "recommended_actions"],
}

SYSTEM_PROMPT = """You are a senior site-reliability engineer on call.
You receive a production log incident (the matching log lines plus the lines
that preceded them) and a triage verdict. Think through what the logs actually
show, then give the on-call engineer:
- a plain-language summary of the problem,
- the most probable root causes (ranked, grounded in the log evidence — say so when evidence is thin),
- the likely impact,
- concrete, ordered actions: first mitigate, then fix, then prevent,
- diagnostic commands they can run to confirm.
Be specific to the technology in the logs. Never invent log lines. Keep it concise:
the message is read on a phone. Respond with JSON only."""


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    elif not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("model did not return a JSON object")
    return data


class Analyzer(ABC):
    """Reasons about an incident and produces advice.

    Concrete LLM providers only need to implement :meth:`complete_json`;
    prompt construction and parsing are shared so switching provider never
    changes behavior.
    """

    type_name = "base"
    runbook: str = ""

    @abstractmethod
    async def complete_json(self, system: str, payload: dict[str, Any],
                            schema: dict[str, Any]) -> dict[str, Any]:
        ...

    def build_payload(self, incident: Incident, decision: Decision) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "incident": incident.to_state(max_samples=5, max_context=30),
            "triage": {
                "urgency": URGENCY_LABELS[decision.urgency],
                "category": decision.category,
                "reason": decision.reason,
            },
        }
        if self.runbook:
            payload["team_runbook_notes"] = self.runbook
        return payload

    async def analyze(self, incident: Incident, decision: Decision) -> Analysis:
        out = await self.complete_json(SYSTEM_PROMPT, self.build_payload(incident, decision), ANALYSIS_SCHEMA)
        analysis = Analysis(
            summary=str(out.get("summary", "")).strip(),
            probable_causes=[str(x) for x in out.get("probable_causes", []) or []][:5],
            impact=str(out.get("impact", "") or ""),
            recommended_actions=[str(x) for x in out.get("recommended_actions", []) or []][:7],
            diagnostic_commands=[str(x) for x in out.get("diagnostic_commands", []) or []][:5],
            confidence=float(out.get("confidence", 0) or 0),
            provider=getattr(self, "provider_label", self.type_name),
        )
        step("analyze", "result from %s (confidence %.2f)", analysis.provider, analysis.confidence,
             body=asdict(analysis))
        return analysis

    async def aclose(self) -> None:
        pass


@register("analyzer", "none")
class NoAnalyzer(Analyzer):
    """Skip analysis — alerts are sent with the triage verdict only."""

    def __init__(self) -> None:
        pass

    async def complete_json(self, system, payload, schema):  # pragma: no cover
        raise RuntimeError("analysis disabled")

    async def analyze(self, incident: Incident, decision: Decision) -> Analysis:
        raise RuntimeError("analysis disabled")


@register("analyzer", "chain")
class ChainAnalyzer(Analyzer):
    """Try analyzers in order (e.g. Gemini, then an OpenRouter model)."""

    def __init__(self, analyzers: list[dict[str, Any]]):
        if not analyzers:
            raise ValueError("chain analyzer needs at least one entry")
        self.analyzers: list = []
        for spec in analyzers:
            try:
                self.analyzers.append(create("analyzer", spec))
            except Exception as exc:  # e.g. missing API key: degrade to the next entry
                log.warning("analyzer %s unavailable: %s", spec.get("type"), exc)
        if not self.analyzers:
            raise ValueError("no analyzer in the chain could be initialised")

    async def complete_json(self, system, payload, schema):
        last: Exception | None = None
        for a in self.analyzers:
            try:
                return await a.complete_json(system, payload, schema)
            except Exception as exc:
                log.warning("analyzer %s failed: %s; trying next", a.type_name, exc)
                last = exc
        raise RuntimeError(f"all analyzers failed: {last}")

    async def analyze(self, incident: Incident, decision: Decision) -> Analysis:
        last: Exception | None = None
        for a in self.analyzers:
            step("analyze", "trying %s", a.type_name)
            try:
                return conform(await a.analyze(incident, decision), Analysis, f"analyzer {a.type_name}")
            except Exception as exc:
                log.warning("analyzer %s failed: %s; trying next", a.type_name, exc)
                last = exc
        raise RuntimeError(f"all analyzers failed: {last}")

    async def aclose(self) -> None:
        for a in self.analyzers:
            await a.aclose()
