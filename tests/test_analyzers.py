import json

from herjev.analyzers.base import extract_json
from herjev.analyzers.gemini import GeminiAnalyzer
from herjev.analyzers.openai_compat import OpenAICompatAnalyzer
from herjev.models import Decision

from conftest import Recorder

ADVICE = {
    "summary": "API cannot reach Postgres.",
    "probable_causes": ["Postgres down", "PgBouncer pool exhausted"],
    "impact": "All writes failing",
    "recommended_actions": ["Check pg status", "Fail over"],
    "diagnostic_commands": ["systemctl status postgresql"],
    "confidence": 0.7,
}
DEC = Decision(notify=True, urgency=2, category="database", reason="jev")


async def test_gemini_interactions(incident):
    rec = Recorder({"id": "x", "status": "completed", "steps": [
        {"type": "thought", "content": [{"type": "text", "text": "hmm"}]},
        {"type": "model_output", "content": [{"type": "text", "text": json.dumps(ADVICE)}]}]})
    a = GeminiAnalyzer(api_key="g", transport=rec.transport, runbook="pg16")
    out = await a.analyze(incident, DEC)
    req = rec.requests[0]
    assert str(req.url).endswith("/v1beta/interactions")
    assert req.headers["x-goog-api-key"] == "g"
    body = rec.last_json()
    assert body["model"] == "gemini-3.8-flash"
    assert body["response_format"]["mime_type"] == "application/json"
    assert "team_runbook_notes" in json.loads(body["input"])
    assert out.summary == ADVICE["summary"] and len(out.probable_causes) == 2
    assert out.provider == "gemini:gemini-3.8-flash"


async def test_gemini_generate_content(incident):
    rec = Recorder({"candidates": [{"content": {"parts": [{"text": "```json\n" + json.dumps(ADVICE) + "\n```"}]}}]})
    a = GeminiAnalyzer(api_key="g", api="generate_content", model="gemini-2.5-flash", transport=rec.transport)
    out = await a.analyze(incident, DEC)
    assert str(rec.requests[0].url).endswith("/models/gemini-2.5-flash:generateContent")
    assert rec.last_json()["generationConfig"]["responseMimeType"] == "application/json"
    assert out.recommended_actions == ADVICE["recommended_actions"]


async def test_openai_compat(incident):
    rec = Recorder({"choices": [{"message": {"content": json.dumps(ADVICE)}}]})
    a = OpenAICompatAnalyzer(api_key="o", transport=rec.transport)
    out = await a.analyze(incident, DEC)
    assert str(rec.requests[0].url) == "https://openrouter.ai/api/v1/chat/completions"
    assert rec.last_json()["response_format"]["type"] == "json_schema"
    assert out.impact == "All writes failing"


def test_extract_json_variants():
    assert extract_json('{"a":1}') == {"a": 1}
    assert extract_json('Sure! ```json\n{"a":2}\n``` hope it helps') == {"a": 2}
    assert extract_json('prefix {"a":3} suffix') == {"a": 3}


async def test_openai_sends_no_token_cap_unless_configured(incident):
    completion = {"choices": [{"message": {"content": json.dumps(ADVICE)}, "finish_reason": "stop"}]}
    rec = Recorder(completion)
    await OpenAICompatAnalyzer(api_key="k", transport=rec.transport).analyze(incident, DEC)
    assert "max_tokens" not in rec.last_json()
    rec = Recorder(completion)
    await OpenAICompatAnalyzer(api_key="k", max_tokens=8192, transport=rec.transport).analyze(incident, DEC)
    assert rec.last_json()["max_tokens"] == 8192


async def test_gemini_sends_no_token_cap_unless_configured(incident):
    rec = Recorder({"candidates": [{"content": {"parts": [{"text": json.dumps(ADVICE)}]}}]})
    await GeminiAnalyzer(api_key="g", api="generate_content", transport=rec.transport).analyze(incident, DEC)
    assert "maxOutputTokens" not in rec.last_json()["generationConfig"]
    rec = Recorder({"output_text": json.dumps(ADVICE)})
    await GeminiAnalyzer(api_key="g", max_output_tokens=4096, transport=rec.transport).analyze(incident, DEC)
    assert rec.last_json()["generation_config"]["max_output_tokens"] == 4096
