import pytest

from herjev.deciders.jev import JevDecider
from herjev.deciders.rules import RulesDecider
from herjev.models import Level
from herjev.registry import create

from conftest import Recorder, make_incident

JEV_RESPONSE = {
    "id": "gen-dec-1", "model": "typesafe/jev-1.13-20260917", "provider": "TypeSafe",
    "answers": {
        "should_notify": {"type": "noul", "noul": 0.93},
        "urgency": {"type": "score", "score": 2.1, "confidence": 0.8,
                    "probabilities": {"0": 0, "1": 0.1, "2": 0.7, "3": 0.2}},
        "category": {"type": "choice", "choice": "database", "confidence": 0.9,
                     "probabilities": {"database": 0.9}},
    },
    "usage": {"input_tokens": 400, "output_tokens": 60, "cost": 0.00002},
}


async def test_jev_request_shape_and_parsing(incident):
    rec = Recorder(JEV_RESPONSE)
    d = JevDecider(api_key="k", transport=rec.transport, guidance="db is critical")
    decision = await d.decide(incident)
    req = rec.requests[0]
    assert str(req.url) == "https://openrouter.ai/api/alpha/decisions"
    assert req.headers["authorization"] == "Bearer k"
    body = rec.last_json()
    assert body["model"] == "typesafe/jev-1.13"
    assert set(body["questions"]) == {"should_notify", "urgency", "category"}
    assert body["questions"]["should_notify"]["type"] == "noul"
    assert body["questions"]["urgency"]["type"] == "score"
    assert len(body["questions"]["urgency"]["criteria"]) == 4
    assert body["questions"]["category"]["type"] == "choice"
    assert body["state"]["incident"]["occurrences"] == 3
    assert body["state"]["operator_guidance"] == "db is critical"
    assert decision.notify and decision.urgency == 2 and decision.category == "database"
    assert decision.provider.startswith("jev:")
    await d.aclose()


async def test_jev_policy_forces_panic_and_filters_noise():
    low = {**JEV_RESPONSE, "answers": {
        "should_notify": {"type": "noul", "noul": 0.1},
        "urgency": {"type": "score", "score": 0.2, "confidence": 0.9},
        "category": {"type": "choice", "choice": "noise"},
    }}
    d = JevDecider(api_key="k", transport=Recorder(low).transport)
    assert not (await d.decide(make_incident(Level.WARN))).notify
    forced = await d.decide(make_incident(Level.PANIC, "panic: nil map"))
    assert forced.notify and forced.urgency >= 2 and "forced" in forced.reason


async def test_jev_http_error_raises(incident):
    d = JevDecider(api_key="k", transport=Recorder({"error": "nope"}, status=429).transport)
    with pytest.raises(RuntimeError, match="429"):
        await d.decide(incident)


def test_jev_requires_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError):
        JevDecider()


async def test_rules_decider():
    r = RulesDecider()
    assert (await r.decide(make_incident(Level.PANIC, "panic: x", 1))).urgency == 3
    warn = await r.decide(make_incident(Level.WARN, "slow query", 1))
    assert not warn.notify
    err = await r.decide(make_incident(Level.ERROR, "ERROR out of memory", 1))
    assert err.notify and err.category == "resource_exhaustion"


async def test_chain_falls_back(monkeypatch, incident):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    chain = create("decider", {"type": "chain", "deciders": [
        {"type": "jev", "base_url": "http://127.0.0.1:9", "timeout": 0.5}, {"type": "rules"}]})
    decision = await chain.decide(incident)
    assert decision.provider == "rules"
    await chain.aclose()


async def test_chain_skips_unconfigured_provider(monkeypatch, incident):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    chain = create("decider", {"type": "chain", "deciders": [{"type": "jev"}, {"type": "rules"}]})
    assert [d.type_name for d in chain.deciders] == ["rules"]
    assert (await chain.decide(incident)).provider == "rules"
