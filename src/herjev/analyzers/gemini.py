from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from ..registry import register
from ..trace import once, step
from .base import Analyzer, extract_json


@register("analyzer", "gemini")
class GeminiAnalyzer(Analyzer):
    """Google Gemini via the Gemini API (key from https://ai.google.dev/gemini-api/docs/api-key).

    ``api: interactions`` (default) uses ``POST /v1beta/interactions``;
    ``api: generate_content`` uses the classic ``models/{model}:generateContent``.
    Both request JSON output constrained by the analysis schema.
    """

    def __init__(self, model: str = "gemini-3.8-flash", api: str = "interactions",
                 api_key: str | None = None, api_key_env: str = "GEMINI_API_KEY",
                 base_url: str = "https://generativelanguage.googleapis.com/v1beta",
                 thinking_level: str | None = "low", max_output_tokens: int | None = None,
                 runbook: str = "", timeout: float = 60.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        if api not in ("interactions", "generate_content"):
            raise ValueError("gemini api must be 'interactions' or 'generate_content'")
        self.model, self.api = model, api
        self.api_key = api_key or os.environ.get(api_key_env) or os.environ.get("GOOGLE_API_KEY", "")
        if not self.api_key:
            raise ValueError(f"gemini analyzer: no API key (set {api_key_env} or api_key)")
        self.base_url = base_url.rstrip("/")
        self.thinking_level = thinking_level
        self.max_output_tokens = max_output_tokens
        self.runbook = runbook
        self.provider_label = f"gemini:{model}"
        self._client = httpx.AsyncClient(
            timeout=timeout, transport=transport,
            headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
        )

    def build_request(self, system: str, payload: dict[str, Any], schema: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        user = json.dumps(payload, ensure_ascii=False, default=str)
        if self.api == "interactions":
            gen: dict[str, Any] = {}
            if self.max_output_tokens:  # no cap by default: thinking shares this budget
                gen["max_output_tokens"] = self.max_output_tokens
            if self.thinking_level:
                gen["thinking_level"] = self.thinking_level
            return f"{self.base_url}/interactions", {
                "model": self.model,
                "system_instruction": system,
                "input": user,
                "response_format": {"type": "text", "mime_type": "application/json", "schema": schema},
                "generation_config": gen,
            }
        config: dict[str, Any] = {"responseMimeType": "application/json", "responseJsonSchema": schema,
                                  "temperature": 0.2}
        if self.max_output_tokens:
            config["maxOutputTokens"] = self.max_output_tokens
        return f"{self.base_url}/models/{self.model}:generateContent", {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }

    @staticmethod
    def extract_text(data: dict[str, Any]) -> str:
        if data.get("output_text"):
            return str(data["output_text"])
        texts: list[str] = []
        for step in data.get("steps", []) or data.get("outputs", []) or []:
            if step.get("type") not in (None, "model_output", "text"):
                continue
            content = step.get("content", [])
            if isinstance(content, dict):
                content = [content]
            if step.get("text"):
                texts.append(step["text"])
            for c in content:
                if c.get("type", "text") == "text" and c.get("text"):
                    texts.append(c["text"])
        for cand in data.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                if part.get("text") and not part.get("thought"):
                    texts.append(part["text"])
        if not texts:
            raise ValueError(f"gemini returned no text: {json.dumps(data)[:300]}")
        return "".join(texts)

    async def complete_json(self, system, payload, schema):
        url, body = self.build_request(system, payload, schema)
        once(f"system:{hash(system)}", "llm:gemini", "system prompt (static, shown once per run)", system)
        step("llm:gemini", "→ POST %s model=%s api=%s, user message:", url, self.model, self.api, body=payload)
        started = time.monotonic()
        resp = await self._client.post(url, json=body)
        elapsed = time.monotonic() - started
        if resp.status_code >= 400:
            step("llm:gemini", "← HTTP %d in %.2fs", resp.status_code, elapsed, elapsed_ms=elapsed * 1000, body=resp.text)
            raise RuntimeError(f"gemini HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        usage = data.get("usage") or data.get("usageMetadata")
        text = self.extract_text(data)
        step("llm:gemini", "← HTTP %d in %.2fs usage=%s", resp.status_code, elapsed,
             json.dumps(usage) if usage else "n/a", elapsed_ms=elapsed * 1000, body=text)
        return extract_json(text)

    async def aclose(self) -> None:
        await self._client.aclose()
