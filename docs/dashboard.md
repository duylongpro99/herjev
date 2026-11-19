# Dashboard

A web UI over the incident store, laid out as a swimlane ticket board. A ticket
is every incident with the same fingerprint; its card sits in one of five
columns: Mới (new), Đang xử lý (in progress), Chờ review (review), Hoàn thành
(done) and Đã huỷ (cancelled). Each row is a lane, one per project by default
(`?group=service` or `?group=level` regroups them); the lane with the worst open
level comes first, then the one with the most open tickets. Filter by level,
project, outcome and text from the chips above the board; the time range
applies to the New, Cancelled and Done columns, while In progress and Review
always show every ticket. Above the board, the stats panel shows open tickets,
open fatal/panic tickets and an activity chart. ⌘K / Ctrl+K (or `/`) opens a
command palette that finds tickets and runs the filter and grouping commands.
Below 960px wide, the lanes become an accordion. Move a card by dragging it, with
the keyboard (focus it, Space to pick it up, arrows to choose a column, Space
or Enter to drop) or with its "Chuyển sang…" menu. New, In progress and Review
move freely between each other and to Cancelled or Done; Cancelled and Done are
final. When the error comes back, a Done ticket returns to New (marked
"mở lại"); a Cancelled one stays cancelled.

"Gửi tới kênh" in the ticket panel sends the ticket's newest occurrence to the
channels you pick, `auto: false` ones included, with the same message an
automatic alert has. It skips the decider, routes, rate limit and cooldown. The
portal only queues the request; the watcher delivers it within a few seconds,
and the panel shows each send as pending, sent, failed (with the error) or
expired. While the watcher is offline the action is unavailable, and a request
it has not delivered within 10 minutes expires.

Clicking a card opens it in a drawer at `/tickets/<fingerprint>`; the expand
button (`?panel=full`) shows it full screen, and Esc or the browser's back
button returns to the board. The drawer leads with the runbook from the LLM
analysis (summary, impact, probable causes, a checklist of what to do and
copyable diagnostic commands), then lists the ticket's occurrences;
`?occ=<incident id>` selects one. Pick one to read its log
lines (samples highlighted inside the surrounding context) and the numbered
decision timeline: 1 aggregate, 2 classify (Jev / rules / LLM, one entry per
attempt), 3 policy, 4 decision, 5 analyze, 6 route, 7 deliver. Steps that did
not run say why, and every Jev or LLM request and response can be expanded
verbatim.

The portal is a Cloudflare Worker in `dashboard/`: it serves the UI and
`/api/*`, reading the same Postgres as the watcher through Hyperdrive.
`docs/deployment.md` covers deploying it. Set the portal's address so alerts
link to it:

```yaml
dashboard:
  public_url: https://herjev.example.workers.dev   # optional: adds the alert link below
```

**Alert links**: with `dashboard.public_url` set, every alert ends with one
extra line, `open: <public_url>/tickets/<fingerprint>?occ=<incident id>`; the
rest of the alert text is unchanged, and the line survives truncation. Opened
from that link, the drawer says how the ticket has changed since the alert was
sent, and closing it goes to the board with the card highlighted. The drawer's
copy-link button builds the same URL (from the page's own address when
`public_url` is unset).

Its only write is a ticket's status, in the `tickets` table; its database role
cannot change anything else. Status changes are
`POST /api/tickets/<fingerprint>/status` with a JSON body
`{"from": ..., "to": ...}`; any other content type gets `415`, and a stale
`from` or a forbidden move gets `409`.

**Access**: set the Worker secret `HERJEV_DASHBOARD_TOKEN`. Every `/api` call
then needs `Authorization: Bearer <token>` or the `herjev_token` cookie, which
the UI's login page sets; repeated wrong tokens get `429`. Without a token the
portal only answers requests addressed to `127.0.0.1`, `localhost` or `::1`
(local development) and refuses every other host, so a deploy that forgot the
secret is closed rather than open. Stored log lines can contain customer data:
never deploy it without a token.

**Outcomes**: `notified` (at least one channel delivered), `not_notified` (the
decider said no), `rate_limited` (`max_alerts_per_hour` reached), `suppressed`
(same fingerprint inside the cooldown or a mute; the decider is not called, and
repeats within one cooldown period or mute add to a single row),
`delivery_failed` (every channel failed), `no_route` (no route matched).

**Projects** come from `project:` on the source, else the Loki `project` stream
label, else the source name.

**What is stored**: each incident row keeps its project, outcome, raw decider
answer and total processing time; `incident_steps` keeps every trace step in
order (message, body up to 20 KB, elapsed ms). Static blocks such as Jev's
question set and the LLM system prompt are stored once in `trace_blobs`.
`pipeline.store_retention_days` (default 30, `0` = keep forever) deletes older
incidents hourly.

**Database**: Postgres, every table in schema `herjev`. Point the watcher at it
with `HERJEV_DATABASE_URL` (or `pipeline.store_dsn`). Nothing in herjev migrates:
the schema is `supabase/migrations/`, applied to production by the deploy
workflow and to a local or test database by
`scripts/apply-migrations.sh <url>` (the compose Postgres applies it on first
start). Without a database URL the watcher refuses to start. If the
database is unreachable or has no schema at start, it logs the error and keeps
alerting without history until it is restarted; `herjev status` stays healthy, so
check the log after a deploy. Portal queries are
capped at 3 s so a broad search cannot hold up the watcher's writes.

**Portal development**: with the compose Postgres running,

```bash
cd dashboard && npm ci
npm run dev:worker   # the Worker on http://127.0.0.1:8790, reading Postgres as herjev_portal
npm run dev          # the UI with hot reload, proxying /api to the Worker
npm test             # UI and Worker tests
```

To hand a ticket to your own coding agent, see [handoff.md](handoff.md).
