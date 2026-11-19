from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from ..registry import register
from ..trace import once, step
from .base import Analyzer, extract_json


@register("analyzer", "openai")
class OpenAICompatAnalyzer(Analyzer):
    """Any OpenAI-compatible Chat Completions endpoint: OpenRouter (default),
    OpenAI, Groq, Together, vLLM, Ollama (``base_url: http://localhost:11434/v1``)…"""

    def __init__(self, model: str = "google/gemini-3.8-flash",
                 base_url: str = "https://openrouter.ai/api/v1",
                 api_key: str | None = None, api_key_env: str = "OPENROUTER_API_KEY",
                 json_mode: str = "json_schema", max_tokens: int | None = None,
                 runbook: str = "", timeout: float = 60.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        if json_mode not in ("json_schema", "json_object", "none"):
            raise ValueError("json_mode must be json_schema, json_object or none")
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key or os.environ.get(api_key_env, "")
        self.json_mode = json_mode
        self.max_tokens = max_tokens
        self.runbook = runbook
        self.provider_label = f"openai:{model}"
        headers = {"Content-Type": "application/json", "X-Title": "herjev"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport, headers=headers)

    def build_request(self, system: str, payload: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system + "\n\nJSON schema:\n" + json.dumps(schema)},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
            ],
        }
        # No cap by default: a reasoning model spends its thinking from the same budget,
        # and a cap cut most answers before any JSON was written.
        if self.max_tokens:
            body["max_tokens"] = self.max_tokens
        if self.json_mode == "json_schema":
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "result", "schema": schema}}
        elif self.json_mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        return body

    async def complete_json(self, system, payload, schema):
        body = self.build_request(system, payload, schema)
        system_text = body["messages"][0]["content"]
        once(f"system:{hash(system_text)}", "llm:openai", "system prompt (static, shown once per run)", system_text)
        step("llm:openai", "→ POST %s model=%s json_mode=%s, user message:", self.url, self.model,
             self.json_mode, body=payload)
        started = time.monotonic()
        resp = await self._client.post(self.url, json=body)
        elapsed = time.monotonic() - started
        if resp.status_code >= 400:
            step("llm:openai", "← HTTP %d in %.2fs", resp.status_code, elapsed, elapsed_ms=elapsed * 1000, body=resp.text)
            raise RuntimeError(f"openai-compatible HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            step("llm:openai", "← HTTP %d in %.2fs, unexpected payload", resp.status_code, elapsed, elapsed_ms=elapsed * 1000, body=data)
            raise ValueError(f"unexpected completion payload: {json.dumps(data)[:300]}") from exc
        step("llm:openai", "← HTTP %d in %.2fs model=%s finish_reason=%s usage=%s", resp.status_code, elapsed,
             data.get("model", self.model), choice.get("finish_reason"),
             json.dumps(data.get("usage")) if data.get("usage") else "n/a", elapsed_ms=elapsed * 1000, body=content or "")
        return extract_json(content or "")

    async def aclose(self) -> None:
        await self._client.aclose()
