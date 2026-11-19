import pytest

from herjev.config import parse_config
import asyncio

from herjev.formatting import _ts, attach_link, render_text, ticket_url
from herjev.models import Alert, Analysis, Decision, Incident, Level
from herjev.pipeline import Pipeline


def alert(title="pq: remaining connection slots are reserved", fp="9480943d491588a4"):
    inc = Incident(fingerprint=fp, level=Level.FATAL, source="payments-journal", host="api-1",
                   service="payments", title=title, first_seen=0, last_seen=19, count=14,
                   id="3f9c1a2b7d10")
    ana = Analysis(summary="Postgres is out of connections.", impact="checkout fails",
                   probable_causes=["leak"], recommended_actions=["restart payments"],
                   diagnostic_commands=["psql -c 'select 1'"])
    dec = Decision(notify=True, urgency=2, category="database", reason="jev: notify p=0.97", provider="t")
    return Alert(inc, dec, ana)


# The one alert format every channel sends; it must not drift by accident.
SNAPSHOT = (
    "loại: fatal\n"
    "thời gian: {a}\n"
    "nội dung: pq: remaining connection slots are reserved\n"
    "service: payments\n"
    "root cause dự đoán: leak"
)


def test_five_line_format():
    expected = SNAPSHOT.format(a=_ts(0))
    assert render_text(alert()) == expected
    assert render_text(attach_link(alert(), "")) == expected


def test_root_cause_falls_back_to_summary_then_to_no_analysis():
    a = alert(title="boom\n  at   main.go:1")
    assert "nội dung: boom at main.go:1" in render_text(a)
    a.analysis.probable_causes = []
    assert render_text(a).endswith("root cause dự đoán: Postgres is out of connections.")
    a.analysis = None
    assert render_text(a).endswith("root cause dự đoán: chưa có phân tích AI")


def test_link_is_the_last_line():
    text = render_text(attach_link(alert(), "https://h"))
    lines = text.split("\n")
    assert lines[-1] == "open: https://h/tickets/9480943d491588a4?occ=3f9c1a2b7d10"
    assert lines[-2] == "root cause dự đoán: leak"


def test_trailing_slash_and_quoting():
    a = alert(fp="a/b c")
    assert ticket_url("https://h/base/", a) == "https://h/base/tickets/a%2Fb%20c?occ=3f9c1a2b7d10"


def test_truncated_message_keeps_link_within_max_len():
    a = alert(title="x" * 300)
    a.analysis.probable_causes = ["y" * 300]
    text = render_text(attach_link(a, "https://herjev.example.com"), max_len=300)
    assert len(text) <= 300
    assert "…(truncated)" in text
    assert text.endswith("open: https://herjev.example.com/tickets/9480943d491588a4?occ=3f9c1a2b7d10")


def _cfg(**dash):
    return {"sources": [{"name": "x", "type": "file", "path": "/tmp/x"}],
            "channels": {"c": {"type": "console"}}, "dashboard": dash, "pipeline": {"store": False}}


def test_public_url_must_be_http():
    with pytest.raises(ValueError, match="public_url"):
        parse_config(_cfg(public_url="herjev.example.com"))


def test_pipeline_puts_the_link_on_the_alert():
    pipe = Pipeline.from_config(parse_config(_cfg(public_url="https://herjev.example.com/")), dry_run=True)
    sent = asyncio.run(pipe.process(alert().incident))
    assert sent.link == "https://herjev.example.com/tickets/9480943d491588a4?occ=3f9c1a2b7d10"
    assert pipe.channels["c"].render(sent).endswith("\nopen: " + sent.link)


def test_pipeline_without_public_url_has_no_link():
    pipe = Pipeline.from_config(parse_config(_cfg()), dry_run=True)
    sent = asyncio.run(pipe.process(alert().incident))
    assert sent.link == "" and "open:" not in pipe.channels["c"].render(sent)


def test_channels_know_nothing_about_the_portal():
    # A stale per-channel link_base is ignored; the link only travels on the alert.
    pipe = Pipeline.from_config(parse_config({**_cfg(), "channels": {"c": {"type": "console", "link_base": "x"}}}),
                                dry_run=True)
    assert not hasattr(pipe.channels["c"], "link_base")
