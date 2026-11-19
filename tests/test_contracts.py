"""The closed shapes between the core, its sources and its channels."""

import asyncio
import dataclasses
import json

import pytest

from herjev.aggregator import Aggregator
from herjev.analyzers.base import Analyzer
from herjev.deciders.base import ChainDecider, Decider
from herjev.deciders.rules import RulesDecider
from herjev.models import (ALERT_SCHEMA_VERSION, Alert, Analysis, ContractError, Decision, Level,
                           LogEvent)
from herjev.notifiers.base import Notifier
from herjev.notifiers.simple import WebhookNotifier
from herjev.parsing import parse_line, parse_record
from herjev.pipeline import Pipeline, Route
from herjev import registry
from herjev.registry import register
from herjev.sources.base import Source

from conftest import make_incident

LOG_EVENT_FIELDS = {"message", "level", "source", "host", "service", "project", "timestamp", "raw",
                    "explicit_level"}


def ev(**kw):
    return LogEvent(**({"message": "ERROR db down", "level": Level.ERROR, "source": "s"} | kw))


@pytest.mark.parametrize("bad", [
    {"level": "error"}, {"message": None}, {"timestamp": "now"}, {"timestamp": float("nan")},
    {"source": ""}, {"host": 1}, {"explicit_level": "yes"},
])
def test_log_event_rejects_a_wrong_shape(bad):
    with pytest.raises(ContractError):
        ev(**bad)


def test_log_event_has_no_untyped_extras():
    assert {f.name for f in dataclasses.fields(LogEvent)} == LOG_EVENT_FIELDS
    with pytest.raises(TypeError):
        LogEvent(message="x", level=Level.ERROR, source="s", attributes={})


def test_every_input_shape_becomes_the_same_log_event():
    record = {"msg": "db down", "level": "error", "host": "h1", "project": "shop", "trace_id": "t-1",
              "nested": {"a": 1}}
    events = [parse_line("ERROR db down", "s"), parse_line(json.dumps(record), "s"), parse_record(record, "s")]
    for e in events:
        assert type(e) is LogEvent and set(vars(e)) == LOG_EVENT_FIELDS
        e.validate()
    # Known keys map onto fields; the rest stays only in `raw`.
    assert events[2].project == "shop" and events[2].host == "h1" and "trace_id" in events[2].raw


def test_decision_and_analysis_reject_a_wrong_shape():
    with pytest.raises(ContractError):
        Decision(notify="yes")
    with pytest.raises(ContractError):
        Decision(notify=True, urgency=7)
    with pytest.raises(ContractError):
        Analysis(summary="x", recommended_actions="restart it")


def test_alert_publishes_one_versioned_shape_without_internals():
    inc = make_incident()
    inc.steps = [{"stage": "decide:jev", "body": {"secret": "prompt"}}]
    alert = Alert(inc, Decision(notify=True, urgency=2, raw={"model": "raw answer"}),
                  Analysis(summary="db down"), link="https://h/tickets/fp1?occ=inc1")
    out = alert.to_dict()
    assert set(out) == {"schema_version", "link", "incident", "decision", "analysis"}
    assert out["schema_version"] == ALERT_SCHEMA_VERSION and out["link"] == alert.link
    assert set(out["incident"]) == {"id", "fingerprint", "level", "source", "project", "host", "service",
                                    "title", "count", "first_seen", "last_seen", "state"}
    assert "raw" not in out["decision"] and "steps" not in json.dumps(out)
    json.dumps(out)  # serialisable as is


def test_webhook_sends_the_published_shape(monkeypatch):
    sent = {}
    ch = WebhookNotifier("hook", url="http://x")

    async def post(url, **kw):
        sent.update(kw["json"])
    monkeypatch.setattr(ch, "_post", post)
    alert = Alert(make_incident(), Decision(notify=True, urgency=1))
    asyncio.run(ch.send(alert))
    assert sent == {"text": ch.render(alert), **alert.to_dict()}


# --- the pipeline holds its boundaries -------------------------------------------------

class ListSource(Source):
    def __init__(self, items):
        super().__init__("probe")
        self.items, self.streams = items, 0

    async def stream(self):
        self.streams += 1
        for item in self.items:
            yield item


class Collect(Notifier):
    def __init__(self):
        super().__init__("c")
        self.alerts = []

    async def send(self, alert):
        self.alerts.append(alert)


class DictDecider(Decider):
    async def decide(self, incident):
        return {"notify": True}


class BadAnalyzer(Analyzer):
    async def complete_json(self, system, payload, schema):
        return {}

    async def analyze(self, incident, decision):
        return "the database is down"


def run(items, decider=None, analyzer=None):
    src, ch = ListSource(items), Collect()
    pipe = Pipeline([src], decider or RulesDecider(), analyzer, {"c": ch}, [Route(["c"])],
                    Aggregator(window_seconds=0, cooldown_seconds=0), status_file=None)
    asyncio.run(pipe.run(duration=1.5))
    return pipe, src, ch


def test_a_malformed_event_is_dropped_and_the_stream_goes_on():
    bad = ev()
    bad.timestamp = "now"  # set after parsing, as a source could
    pipe, src, ch = run([bad, {"message": "not an event"}, ev(message="ERROR next line")])
    assert pipe.stats.rejected_by_source == {"probe": 2}
    assert src.streams == 1, "the source must not be restarted for one bad event"
    assert [a.incident.title for a in ch.alerts] == ["ERROR next line"]


def test_a_decider_breaking_its_contract_falls_back_by_level():
    pipe, _, ch = run([ev()], decider=DictDecider())
    [alert] = ch.alerts
    assert alert.decision.provider == "fallback" and alert.decision.notify
    assert any("decider" in e["error"] for e in pipe.stats.errors)


def test_a_chain_moves_past_a_decider_breaking_its_contract(monkeypatch):
    monkeypatch.setitem(registry._REGISTRY, "decider", dict(registry._REGISTRY["decider"]))
    register("decider", "dict-test")(DictDecider)
    chain = ChainDecider([{"type": "dict-test"}, {"type": "rules"}])
    assert asyncio.run(chain.decide(make_incident())).provider == "rules"


def test_an_analyzer_breaking_its_contract_leaves_the_alert_without_analysis():
    pipe, _, ch = run([ev()], analyzer=BadAnalyzer())
    [alert] = ch.alerts
    assert alert.analysis is None
    assert any("analyzer" in e["error"] for e in pipe.stats.errors)
