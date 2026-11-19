from herjev.models import Level
from herjev.parsing import detect_level, fingerprint, normalize, parse_line


def test_plain_text_levels():
    assert detect_level("2026-09-26T10:00:00Z ERROR something broke") == Level.ERROR
    assert detect_level("[warn] disk at 85%") == Level.WARN
    assert detect_level("level=fatal msg=\"boom\"") == Level.FATAL
    assert detect_level("panic: runtime error: invalid memory address") == Level.PANIC
    assert detect_level("E0926 CRITICAL: kernel oops") == Level.FATAL
    assert detect_level("GET /health 200") == Level.INFO
    # "error" inside a word/URL shouldn't trigger
    assert detect_level("GET /errors-page 200 INFO") == Level.INFO


def test_json_lines():
    ev = parse_line('{"level":"error","msg":"db down","service":"pay","host":"p1"}', "src")
    assert ev.level == Level.ERROR and ev.message == "db down"
    assert ev.service == "pay" and ev.host == "p1"
    pino = parse_line('{"level":60,"msg":"fatal thing"}', "src")
    assert pino.level == Level.FATAL
    nested = parse_line('{"log":{"level":"WARNING"},"message":"slow"}', "src")
    assert nested.level == Level.WARN
    gopanic = parse_line('{"level":"error","msg":"panic: nil map"}', "src")
    assert gopanic.level == Level.PANIC


def test_normalize_and_fingerprint_group_similar_errors():
    a = parse_line("2026-09-26T10:00:01Z ERROR timeout after 3000ms calling 10.0.0.1:443 req=1f2e3d4c-aaaa-bbbb-cccc-123456789abc", "s")
    b = parse_line("2026-09-26T10:05:09Z ERROR timeout after 5012ms calling 10.0.0.9:443 req=9f2e3d4c-aaaa-bbbb-cccc-123456789abc", "s")
    c = parse_line("2026-09-26T10:05:09Z ERROR disk full on /var", "s")
    assert normalize(a.message) == normalize(b.message)
    assert fingerprint(a) == fingerprint(b) != fingerprint(c)


def test_blank_lines_ignored():
    assert parse_line("   \n", "s") is None
