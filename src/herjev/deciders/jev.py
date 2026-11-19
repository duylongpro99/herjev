from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from ..models import URGENCY_LABELS, Decision, Incident, Level
from ..registry import register
from ..trace import once, step
from .base import DEFAULT_CATEGORIES, Decider, apply_policy

URGENCY_CRITERIA = [
    "Ignore: benign, expected, or self-recovered; no human needed",
    "Investigate soon: real problem but limited impact; handle during working hours",
    "Act within the hour: degraded service, growing error rate, or risk of outage",
    "Page now: outage, crash loop, data loss, security incident, or panic in a critical path",
]


@register("decider", "jev")
class JevDecider(Decider):
    """Uses TypeSafe's Jev (a "System One" decision model) to triage incidents.

    One request asks three typed questions evaluated in parallel against the
    incident *state*:

    * ``should_notify`` (noul)   → probability a human should be told
    * ``urgency``       (score)  → 0..3 on URGENCY_CRITERIA
    * ``category``      (choice) → one of ``categories``

    Default transport is OpenRouter's Decisions API
    (``POST {base_url}/api/alpha/decisions``, ``OPENROUTER_API_KEY``). Point
    ``base_url``/``endpoint``/``api_key_env`` elsewhere to use another host.
    """

    def __init__(self, model: str = "typesafe/jev-1.13",
                 base_url: str = "https://openrouter.ai",
                 endpoint: str = "/api/alpha/decisions",
                 api_key: str | None = None, api_key_env: str = "OPENROUTER_API_KEY",
                 notify_threshold: float = 0.5, min_urgency: int = 1,
                 always_notify: list[str] | None = None,
                 categories: dict[str, str] | None = None,
                 extra_state: dict[str, Any] | None = None,
                 guidance: str = "",
                 timeout: float = 20.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.model = model
        self.url = base_url.rstrip("/") + endpoint
        self.api_key = api_key or os.environ.get(api_key_env, "")
        if not self.api_key:
            raise ValueError(f"jev decider: no API key (set {api_key_env} or api_key)")
        self.notify_threshold = notify_threshold
        self.min_urgency = min_urgency
        self.always_notify = {Level.parse(x) for x in (always_notify or ["fatal", "panic"])}
        self.categories = categories or DEFAULT_CATEGORIES
        self.extra_state = extra_state or {}
        self.guidance = guidance
        self._client = httpx.AsyncClient(
            timeout=timeout, transport=transport,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json",
                     "X-Title": "herjev"},
        )

    def build_request(self, incident: Incident) -> dict[str, Any]:
        state: dict[str, Any] = {"incident": incident.to_state(), **self.extra_state}
        if self.guidance:
            state["operator_guidance"] = self.guidance
        return {
            "model": self.model,
            "state": state,
            "questions": {
                "should_notify": {
                    "type": "noul",
                    "instructions": "Should an on-call engineer be notified about this log incident?",
                    "criteria": {
                        "true": "Real or imminent impact: crashes, panics, sustained or bursty errors, "
                                "data loss, security issues, resource exhaustion, broken dependencies",
                        "false": "Benign noise: expected warnings, single transient retry that recovered, "
                                 "client-side 4xx, deprecation notices, debug chatter",
                    },
                },
                "urgency": {
                    "type": "score",
                    "instructions": "How urgently does a human need to act on this incident?",
                    "criteria": URGENCY_CRITERIA,
                },
                "category": {
                    "type": "choice",
                    "instructions": "What kind of problem does this incident most likely represent?",
                    "criteria": self.categories,
                },
            },
            "session_id": f"herjev-{incident.fingerprint}",
        }

    def parse_response(self, data: dict[str, Any], incident: Incident) -> Decision:
        answers = data.get("answers") or {}
        if not answers:
            raise ValueError(f"jev returned no answers: {json.dumps(data)[:300]}")
        noul = float(answers.get("should_notify", {}).get("noul", 0.0))
        urg = answers.get("urgency", {})
        urgency = max(0, min(3, round(float(urg.get("score", 0)))))
        cat = answers.get("category", {})
        category = cat.get("choice", "unknown")
        notify = noul >= self.notify_threshold and category != "noise"
        step("decide:jev", "interpreted answers",
             body={"should_notify": f"p={noul:.2f} vs notify_threshold={self.notify_threshold} "
                                    f"→ {'yes' if noul >= self.notify_threshold else 'no'}",
                   "urgency": f"score={urg.get('score')} (confidence={urg.get('confidence')}) "
                              f"→ {urgency} {URGENCY_LABELS[urgency]}",
                   "category": f"{category} (confidence={cat.get('confidence')})"
                               + (" → 'noise' never notifies" if category == "noise" else ""),
                   "notify_before_policy": notify})
        decision = Decision(
            notify=notify,
            urgency=urgency,
            category=category,
            confidence=float(urg.get("confidence", noul)),
            reason=f"jev: notify p={noul:.2f}, urgency={URGENCY_LABELS[urgency]}, category={category}",
            provider=f"jev:{data.get('model', self.model)}",
            raw=data,
        )
        return apply_policy(decision, incident, self.always_notify, self.min_urgency)

    async def decide(self, incident: Incident) -> Decision:
        body = self.build_request(incident)
        once(f"jev-questions:{self.url}", "decide:jev", "question definitions (static, shown once per run)",
             body["questions"])
        step("decide:jev", "→ POST %s model=%s questions=%s", self.url, self.model, ", ".join(body["questions"]),
             body={k: v for k, v in body.items() if k != "questions"})
        started = time.monotonic()
        resp = await self._client.post(self.url, json=body)
        elapsed = time.monotonic() - started
        if resp.status_code >= 400:
            step("decide:jev", "← HTTP %d in %.2fs", resp.status_code, elapsed, elapsed_ms=elapsed * 1000, body=resp.text)
            raise RuntimeError(f"jev HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        step("decide:jev", "← HTTP %d in %.2fs", resp.status_code, elapsed, elapsed_ms=elapsed * 1000, body=data)
        return self.parse_response(data, incident)

    async def aclose(self) -> None:
        await self._client.aclose()
