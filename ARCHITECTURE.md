# herjev architecture

## Flow

```
             poll                                    listen
  ┌──────────┬──────────┬───────────┬─────────┐  ┌──────────┬─────────┐
  │ ssh      │ command  │ http_poll │ loki    │  │ webhook  │ syslog  │   file
  └────┬─────┴────┬─────┴─────┬─────┴────┬────┘  └────┬─────┴────┬────┘
       └──────────┴───────────┴──── LogEvent ─────────┴──────────┘
                                     │  parsing.py: JSON/logfmt/text → level
                                     ▼
                         ┌──────────────────────┐  window grouping, fingerprints,
                         │     Aggregator       │  multi-line stack traces,
                         │ (warn/error/fatal/   │  context ring buffer, cooldown,
                         │  panic → Incident)   │  mutes
                         └──────────┬───────────┘
                                    ▼
                         ┌──────────────────────┐  Jev (System One) answers 3 typed
                         │  Decider  (triage)   │  questions in one call:
                         │  jev │ rules │ llm   │  should_notify (noul) · urgency (score)
                         │  chain = fallbacks   │  · category (choice) + policy guardrails
                         └──────────┬───────────┘
                     notify=false ──┤── notify=true
                     (stored only)  ▼
                         ┌──────────────────────┐  LLM reasons over samples + preceding
                         │ Analyzer (thinking)  │  context → summary, ranked causes,
                         │ gemini │ openai │ …  │  impact, actions, diagnostic cmds
                         └──────────┬───────────┘
                                    ▼
                         ┌──────────────────────┐  routes match level / urgency /
                         │ Router → Notifiers   │  source / category → named channels
                         │ telegram │ slack │   │  (webhook = full incident as JSON, e.g.
                         │ discord │ webhook │… │   to a chat agent that follows up)
                         └──────────┬───────────┘
                                    ▼
                  Store (Postgres, Supabase) ──► herjev incidents / herjev status (CLI)
                             └──────────────► portal: Cloudflare Worker + React ticket board
```

Every stage also reports to `trace.step()`. While an incident is processed the
pipeline installs a per-incident collector, so the ordered steps (message, raw
request/response body, elapsed ms) are written with the incident to
`incident_steps`, in the same transaction. Incidents held back by cooldown are
recorded too, with outcome `suppressed`.

The store is Postgres (Supabase in production), every table in schema `herjev`.
Two login roles share it, and the split is the main invariant: `herjev_watcher`
(the watcher) reads and writes everything, while `herjev_portal` (the portal
Worker, through Hyperdrive) reads everything but may only insert and update
`tickets`, insert send requests into `channel_sends`, and insert or update the
service choices in `source_services` (never delete them), with a 3 s statement
timeout. Neither role migrates: the schema lives
in `supabase/migrations/`, and the deploy workflow is the only place migrations
run against production (`scripts/apply-migrations.sh` does it for a local or
test database). History never blocks alerting: if the store cannot be opened at start (unreachable,
or no schema), the watcher logs it and runs without history, and every store call
after that runs beside the alert path, never in front of it.

The portal's one write is ticket status: a ticket is every incident with the
same fingerprint, and its status (`new`, `inprogress`, `review`, `cancel`,
`done`) lives in the `tickets` table. A move names the status the client saw and
is refused with a conflict if the ticket no longer has it. `cancel` and `done`
are terminal for people (`dashboard/worker/queries.ts`); when the watcher
records a new incident for a `done` ticket it moves the ticket back to `new` and
sets `reopened_at`, while a `cancel` ticket stays cancelled. Pruning
old incidents also removes tickets left with none.

The portal's other write is a manual send. The portal holds no channel secrets
and cannot reach the VM, so it only queues the request: one `channel_sends` row
per channel, naming the ticket's newest occurrence. It may insert only the
request columns, so every row starts `pending` at the database's time, and it only accepts channel
names the watcher published in its status (`channels`: name, type, `auto`)
while that status is under a minute old. Every ~5 s the watcher expires requests
older than 10 minutes, claims the pending ones for its own channels (`sending`,
with `FOR UPDATE SKIP LOCKED`, so none goes out twice; a dry run claims none), rebuilds the `Alert` from the stored incident
row (`store.alert_from_row`) and delivers it with the channel's own notifier, so
the message is the same as an automatic one. It then records `sent` or `failed`
with the error. Manual sends skip the decider, routes, rate limit and cooldown. A
channel with `auto: false` is built but left out of every route, so this is the
only way anything reaches it.

Handoff is read-only. `GET /api/tickets/<fp>/handoff` renders one Markdown bundle
per occurrence (`dashboard/worker/handoff.ts`): the incident context, the log
lines marked as untrusted, the analyzer's prediction and a task for a coding
agent. The portal's Handoff button opens `herjev://handoff?ticket=<fp>&occ=<id>`.
On the developer's machine, a handler registered by `herjev handoff setup`
(`src/herjev/handoff_install.py`) runs `herjev handoff open`
(`src/herjev/handoff.py`), which fetches the bundle with the token in its local
config, writes it to a private file and opens a terminal that starts the
developer's agent on it. herjev neither runs agents nor collects their results.
The analyzer's log-based prediction is unchanged and is part of the bundle.

## Why each stage is separate

| Stage | Question it answers | Why a separate provider |
|---|---|---|
| Source | *What happened?* | Infra differs per server: SSH, k8s, Loki, push agents |
| Aggregator | *Is this new?* | Deterministic, cheap; stops a crash loop from becoming 500 alerts |
| **Decider (Jev)** | *Should a human care, how urgently?* | Needs to be fast, cheap and return **typed values code can branch on** — exactly what Jev is for. Output tokens are free; one request, three parallel questions |
| **Analyzer (LLM)** | *Why, and what do I do?* | Needs generative reasoning; only runs for incidents that passed triage, so cost scales with real problems, not log volume |
| Notifier | *How does the user hear?* | Chat platforms directly, or a webhook so another system or agent owns delivery and follow-up |

## Extensibility

All four pluggable kinds use one registry (`herjev/registry.py`). Config
chooses implementations by `type:`; constructor kwargs are the rest of the
YAML block.

```python
from herjev.registry import register
from herjev.notifiers.base import Notifier

@register("notifier", "pagerduty")
class PagerDuty(Notifier):
    def __init__(self, name, routing_key, **kw):
        super().__init__(name, **kw); self.key = routing_key
    async def send(self, alert):
        ...  # alert.incident / alert.decision / alert.analysis
```

Ship it in your own package with an entry point and herjev loads it automatically:

```toml
[project.entry-points."herjev.providers"]
pagerduty = "mypkg.pagerduty"
```

Contracts:

| Kind | Implement | Gets / returns |
|---|---|---|
| `source` | `async def stream() -> AsyncIterator[LogEvent]` | use `self.line()` / `self.record()` to parse, `self.backoff()` to reconnect; put anything the core needs (e.g. `attributes["project"]`) on the event itself |
| `decider` | `async def decide(incident) -> Decision` | wrap with `apply_policy()` for guardrails |
| `analyzer` | `async def complete_json(system, payload, schema) -> dict` | prompt + parsing are shared in the base class, so a new LLM is ~30 lines |
| `notifier` | `async def send(alert)` | `self.render(alert)` gives the standard text, ending with the `open:` ticket link when the pipeline set `alert.link` (from `dashboard.public_url`) |

### Contract schemas

Sources and channels touch the core only through closed shapes defined in
`src/herjev/models.py`:

* **`LogEvent`** is the one shape every source delivers, whatever its raw data:
  `message`, `level`, `source`, `host`, `service`, `project`, `timestamp`, `raw`,
  `explicit_level`. A source maps its native data (Loki labels, JSON keys, syslog
  headers) onto these fields; everything else stays only in `raw`. There is no
  untyped extras bag, so unknown information never reaches the core.
* **`Decision`** and **`Analysis`** are what deciders and analyzers return.
* **`Alert`** (incident, decision, analysis, `link`) is the one shape every
  channel renders from. Channels may write different content, but a channel
  that hands an alert to another system sends `Alert.to_dict()`, the published
  form versioned by `ALERT_SCHEMA_VERSION`; the decision trace and the
  provider's raw response stay out of it.

Every shape checks itself (`validate()`, also run on construction) and the
pipeline enforces them where data crosses a boundary (`conform()`): a malformed
event is dropped and counted in `rejected_by_source` while its source keeps
streaming; a decider that breaks its contract counts as failed, so a chain
moves to the next entry and the pipeline falls back by level; an analyzer that
breaks its contract leaves the alert without analysis. To carry new
information, add a typed field (with a default) to the shape; that is additive
and keeps the schema version. Removing, renaming or retyping a published field
bumps `ALERT_SCHEMA_VERSION`.

The core never reads a source's native shape and a channel never learns about
the portal or the analyzer. A provider that needs its own setup command defines
a classmethod `add_cli(subparsers)` and the CLI picks it up from the registry,
as the teams channel does for `herjev teams login`.

## Switching providers

* **Jev host** — `decider.base_url` / `endpoint` / `api_key_env` (default OpenRouter Decisions API, `typesafe/jev-1.13`).
* **No Jev** — `decider: {type: llm, llm: {type: gemini}}` or `{type: rules}`.
* **LLM** — `analyzer.type: gemini` (Interactions or generateContent API) or `openai` (OpenRouter, OpenAI, Groq, vLLM, Ollama…).
* **Fallbacks** — `type: chain` for deciders and analyzers. Entries that can't initialise (missing key) are skipped, failures at runtime fall through to the next entry. If every decider fails, the pipeline still alerts on ERROR+ by level so serious events are never silently dropped.
* **Channels** — any number of named channels; routes pick them.

## Handing off to an agent

herjev delivers alerts itself; every channel (Teams included, `notifiers/teams.py`)
sends the one message format from `formatting.py`. A `webhook` channel POSTs each
alert as JSON (`text`, `incident`, `decision`, `analysis`) with static headers, so
any agent with an HTTP ingress (for example a Hermes webhook route, authenticated
by an `X-Gitlab-Token` header and posting `text` with `deliver_only`, so no model
rewrites it) can relay `text` and hold the conversation. For the
agent's follow-up, `herjev run` serves an MCP endpoint (`mcp_server`,
`src/herjev/mcp_server.py`): streamable HTTP behind a bearer token, with the
tools `status`, `incidents`, `incident`, `analyze` and `mute` running in-process
against the watcher's store and config. There is no shell behind it, so an agent
route fed with production log text can be limited to these tools.
`integrations/hermes/herjev-oncall/SKILL.md` teaches a Hermes agent to use them,
and `scripts/install-hermes-skill.sh --connect` sets up both directions from the
herjev side: the token, the MCP server entry, the skill and the webhook route. Nothing in `src/` depends on Hermes.
The portal is not part of the Python package: it is a Cloudflare Worker in
`dashboard/` that serves the React UI and `/api/*` from the same database
(`docs/deployment.md`).

## Guardrails

* `always_notify: [fatal, panic]` overrides a model that says "ignore".
* `min_urgency` drops low-urgency alerts; `category == noise` never notifies.
* Cooldown per fingerprint unless the level escalates; `max_alerts_per_hour` cap.
* Every incident — alerted, not alerted, or suppressed by cooldown — is recorded with its
  outcome, the reason and the full decision trace, for auditing the model's decisions.
