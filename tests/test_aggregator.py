from herjev.aggregator import Aggregator
from herjev.models import Level
from herjev.parsing import parse_line


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def test_window_groups_and_cooldown_suppresses():
    clk = Clock()
    agg = Aggregator(window_seconds=10, cooldown_seconds=100, clock=clk)
    agg.add(parse_line("INFO booting", "s"))
    for i in range(5):
        agg.add(parse_line(f"ERROR request {i} failed with code 500", "s"))
    assert agg.flush() == []          # window still open
    clk.t += 11
    [inc] = agg.flush()
    assert inc.count == 5 and inc.level == Level.ERROR
    assert any("booting" in e.message for e in inc.context)
    agg.mark_alerted(inc)

    agg.add(parse_line("ERROR request 9 failed with code 500", "s"))
    clk.t += 11
    assert agg.flush() == []          # cooled down

    agg.add(parse_line("FATAL request 9 failed with code 500", "s"))  # escalation
    clk.t += 11
    # different level word changes message → new fingerprint anyway; either way it must alert
    assert len(agg.flush()) == 1


def test_below_min_level_is_context_only():
    agg = Aggregator(window_seconds=0, min_level=Level.WARN)
    agg.add(parse_line("INFO all good", "s"))
    agg.add(parse_line("DEBUG noise", "s"))
    assert agg.flush(force=True) == []


def test_multiline_stack_trace_attached():
    agg = Aggregator(window_seconds=0)
    agg.add(parse_line("ERROR NullPointerException in handler", "s"))
    agg.add(parse_line("    at com.example.Handler.run(Handler.java:42)", "s"))
    agg.add(parse_line("    at java.lang.Thread.run(Thread.java:750)", "s"))
    [inc] = agg.flush(force=True)
    assert inc.count == 1
    assert "Handler.java:42" in inc.samples[0].message


def test_mute():
    clk = Clock()
    agg = Aggregator(window_seconds=0, cooldown_seconds=10, clock=clk)
    agg.add(parse_line("ERROR x", "s"))
    [inc] = agg.flush(force=True)
    agg.mute(inc.fingerprint, 3600)
    clk.t += 1800
    agg.add(parse_line("PANIC x", "s") if False else parse_line("ERROR x", "s"))
    assert agg.flush(force=True) == []
    clk.t += 1900
    agg.add(parse_line("ERROR x", "s"))
    assert len(agg.flush(force=True)) == 1


def test_go_panic_frames_attached_but_next_log_line_is_not():
    agg = Aggregator(window_seconds=0)
    for l in ["panic: runtime error: nil map", "", "goroutine 1 [running]:", "main.handler()",
              "\t/app/main.go:42 +0x1d", "2026-09-26T13:00:05Z INFO restarted"]:
        ev = parse_line(l, "s")
        if ev:
            agg.add(ev)
    [inc] = agg.flush(force=True)
    msg = inc.samples[0].message
    assert "main.handler()" in msg and "main.go:42" in msg and "restarted" not in msg
    assert "restarted" in inc.context[-1].message


def test_project_comes_from_the_event_else_the_source():
    agg = Aggregator(window_seconds=0, cooldown_seconds=0)
    agg.add(parse_line("ERROR boom", "loki"))
    ev = parse_line("ERROR other", "app")
    ev.project = "shop"
    agg.add(ev)
    assert sorted(i.project for i in agg.flush(force=True)) == ["loki", "shop"]
