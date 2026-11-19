"""``herjev`` command line."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time

from . import __version__, handoff
from .config import channel_enabled, load_config, public_url
from .formatting import attach_link, render_text
from .models import Alert, Decision, Incident, Level, LogEvent
from .registry import KINDS, available, registered


def _setup_logging(verbose: int) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose > 1 else logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def cmd_run(args: argparse.Namespace) -> int:
    from .pipeline import Pipeline
    cfg = load_config(args.config)
    mcp_cfg = cfg.get("mcp_server") or {}
    if mcp_cfg.get("enabled"):
        try:
            from . import mcp_server  # noqa: F401
        except ImportError:
            print("herjev mcp_server needs the optional extra: pip install 'herjev[mcp]'", file=sys.stderr)
            return 2
    pipe = Pipeline.from_config(cfg, dry_run=args.dry_run)
    try:
        asyncio.run(_watch(pipe, args.duration, cfg))
    except KeyboardInterrupt:
        pass
    print(json.dumps(pipe.stats.to_dict(), indent=2), file=sys.stderr)
    return 0


async def _watch(pipe, duration: float | None, cfg: dict | None = None) -> None:
    """Run the watcher, plus the MCP endpoint when ``mcp_server.enabled``; the
    endpoint stops with the watcher."""
    server = None
    mcp_cfg = (cfg or {}).get("mcp_server") or {}
    if mcp_cfg.get("enabled"):
        from . import mcp_server
        server = await mcp_server.start(mcp_cfg, cfg, pipe.store)
    try:
        await pipe.run(duration=duration)
    finally:
        if server is not None:
            await mcp_server.stop(server)


def cmd_check(args: argparse.Namespace) -> int:
    from .pipeline import Pipeline
    cfg = load_config(args.config)
    pipe = Pipeline.from_config(cfg, dry_run=True)
    print("config OK")
    print("  sources :", ", ".join(s.describe() for s in pipe.sources))
    print("  decider :", pipe.decider.type_name)
    print("  analyzer:", pipe.analyzer.type_name if pipe.analyzer else "none")
    print("  channels:", ", ".join(f"{n}({c.type_name})" for n, c in pipe.channels.items()) or "none")
    disabled = [n for n, spec in cfg["channels"].items() if not channel_enabled(n, spec)]
    if disabled:
        print("  disabled:", ", ".join(disabled))
    if pipe.manual_channels:
        print("  manual  :", ", ".join(sorted(pipe.manual_channels)), "(auto: false; portal sends only)")
    for r in pipe.routes:
        lv = ",".join(sorted(l.label for l in r.levels)) if r.levels else "*"
        print(f"  route   : levels={lv} min_urgency={r.min_urgency} → {', '.join(r.channels)}")
    asyncio.run(pipe.aclose())
    return 0


def cmd_providers(_: argparse.Namespace) -> int:
    for kind in KINDS:
        print(f"{kind:9}: {', '.join(available(kind))}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    from .adhoc import triage_text
    cfg = load_config(args.config)
    text = open(args.file).read() if args.file and args.file != "-" else sys.stdin.read()
    alerts = asyncio.run(triage_text(cfg, text, host=args.host, service=args.service,
                                     analyze=not args.no_analysis))
    if not alerts:
        print("no warn/error/fatal/panic lines found")
        return 0
    for a in alerts:
        attach_link(a, public_url(cfg))
    if args.json:
        print(json.dumps([a.to_dict() for a in alerts], indent=2, default=str))
    else:
        for a in alerts:
            print(render_text(a, max_len=10_000) + "\n")
    if args.notify:
        from .pipeline import Pipeline
        pipe = Pipeline.from_config(cfg)
        async def deliver():
            try:
                for a in alerts:
                    if a.decision.notify:
                        await pipe._deliver(a, pipe.route(a))
            finally:
                await pipe.aclose()
        asyncio.run(deliver())
    return 0


def cmd_test_channel(args: argparse.Namespace) -> int:
    from .pipeline import build_notifier
    cfg = load_config(args.config)
    names = [args.channel] if args.channel else [n for n, spec in cfg["channels"].items()
                                                  if channel_enabled(n, spec)]
    now = time.time()
    ev = LogEvent(message="herjev test alert: synthetic ERROR to verify channel delivery",
                  level=Level.ERROR, source="herjev-test", host="localhost", service="herjev")
    inc = Incident(fingerprint="test", level=Level.ERROR, source="herjev-test", host="localhost",
                   service="herjev", title=ev.message, first_seen=now, last_seen=now,
                   samples=[ev], id="test")
    alert = attach_link(Alert(inc, Decision(notify=True, urgency=1, category="noise",
                                            reason="manual channel test", provider="cli")), public_url(cfg))
    rc = 0

    async def go() -> None:
        nonlocal rc
        for n in names:
            ch = build_notifier(n, cfg["channels"][n])
            try:
                await ch.send(alert)
                print(f"✓ {n}")
            except Exception as exc:
                print(f"✗ {n}: {exc}")
                rc = 1
            finally:
                await ch.aclose()

    asyncio.run(go())
    return rc


def cmd_status(args: argparse.Namespace) -> int:
    """Print watcher status; exit 1 if it's missing or older than --max-age
    (used as the Docker healthcheck)."""
    from .paths import status_path as _status_path
    cfg = None
    try:
        cfg = load_config(args.config)
    except (FileNotFoundError, ValueError):
        pass
    status_path = _status_path(cfg)
    if not status_path.exists():
        print(f"watcher not running (no {status_path})")
        return 1
    data = json.loads(status_path.read_text())
    age = time.time() - data.get("updated_at", 0)
    data["age_seconds"] = round(age)
    print(json.dumps(data, indent=2))
    if args.max_age and age > args.max_age:
        print(f"stale: last update {age:.0f}s ago > {args.max_age}s", file=sys.stderr)
        return 1
    return 0


def _store(args: argparse.Namespace):
    from .store import Store
    cfg = load_config(args.config) if args.config else {}
    return Store((cfg.get("pipeline") or {}).get("store_dsn"))


def cmd_incidents(args: argparse.Namespace) -> int:
    rows = _store(args).recent(limit=args.limit, since_hours=args.hours, notified_only=args.notified,
                               min_level=args.min_level)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    for r in rows:
        ts = time.strftime("%m-%d %H:%M", time.localtime(r["created_at"]))
        flag = "🔔" if r["notify"] else "  "
        print(f"{flag} {ts} {r['level']:5} u{r['urgency']} {r['source']:12} x{r['count']:<4} "
              f"{r['id']}  {r['title'][:70]}")
    return 0


def cmd_incident(args: argparse.Namespace) -> int:
    """Print one incident (samples, context, triage, analysis) as JSON."""
    row = _store(args).get(args.id)
    if row is None:
        print(f"herjev: no incident {args.id!r}", file=sys.stderr)
        return 1
    print(json.dumps(row, indent=2, default=str, ensure_ascii=False))
    return 0


def cmd_mute(args: argparse.Namespace) -> int:
    """Mute an incident's fingerprint; a running watcher picks it up from the store."""
    store = _store(args)
    row = store.get(args.id)
    fp = row["fingerprint"] if row else args.id
    store.mute(fp, args.minutes * 60, args.reason)
    print(json.dumps({"muted": fp, "minutes": args.minutes, "known_incident": row is not None}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="herjev", description=__doc__)
    ap.add_argument("--version", action="version", version=f"herjev {__version__}")
    ap.add_argument("-c", "--config", help="path to herjev.yaml (default: $HERJEV_CONFIG, ./herjev.yaml, $HERJEV_HOME/herjev.yaml, ~/.config/herjev/herjev.yaml)")
    ap.add_argument("-v", "--verbose", action="count", default=0)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="watch sources and alert (daemon)")
    p.add_argument("--dry-run", action="store_true", help="print alerts instead of delivering")
    p.add_argument("--duration", type=float, help="stop after N seconds (for cron/tick usage)")
    p.set_defaults(func=cmd_run)

    sub.add_parser("check", help="validate config and show wiring").set_defaults(func=cmd_check)
    sub.add_parser("providers", help="list available provider types").set_defaults(func=cmd_providers)

    p = sub.add_parser("analyze", help="triage + analyze pasted logs (stdin or file)")
    p.add_argument("file", nargs="?", help="log file ('-' or omit for stdin)")
    p.add_argument("--host", default="")
    p.add_argument("--service", default="")
    p.add_argument("--no-analysis", action="store_true")
    p.add_argument("--notify", action="store_true", help="also deliver alerts that pass triage")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("test-channel", help="send a synthetic alert to one/all channels")
    p.add_argument("channel", nargs="?")
    p.set_defaults(func=cmd_test_channel)

    p = sub.add_parser("status", help="watcher status (exit 1 if not running / stale)")
    p.add_argument("--max-age", type=float, default=0, help="fail if status older than N seconds")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("incidents", help="show recent incidents from the store")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--hours", type=float)
    p.add_argument("--notified", action="store_true")
    p.add_argument("--min-level", choices=["warn", "error", "fatal", "panic"])
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_incidents)

    p = sub.add_parser("incident", help="show one incident in full as JSON")
    p.add_argument("id", help="incident id or fingerprint")
    p.set_defaults(func=cmd_incident)

    p = sub.add_parser("mute", help="mute alerts for an incident's fingerprint")
    p.add_argument("id", help="incident id or fingerprint")
    p.add_argument("--minutes", type=float, default=60)
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_mute)

    handoff.add_cli(sub)

    # A provider that needs its own setup command (e.g. a channel's account sign-in) defines
    # `add_cli(subparsers)`; the CLI never names a source or channel itself.
    for kind in KINDS:
        for cls in dict.fromkeys(registered(kind).values()):
            if "add_cli" in vars(cls):
                cls.add_cli(sub)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"herjev: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
