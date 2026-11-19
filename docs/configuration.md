# Configuration and CLI

## Pipeline

`sources → aggregate → Jev triage → LLM analysis → route → channels`.
A channel with `enabled: false` (or `enabled: ${VAR:-true}` set from `.env`)
receives nothing and is skipped by every route; `herjev check` lists it as
disabled. A channel with `auto: false` (same flag values) is built but skipped by
every route, including the default one: it only receives tickets a person sends
from the portal, and `herjev check` lists it as manual.
See [ARCHITECTURE.md](../ARCHITECTURE.md) for the design, the extension API, and
how to switch providers.

| Kind | Built-in types |
|---|---|
| source (poll) | `ssh` (tail -F / journalctl over SSH), `command` (kubectl/docker/journalctl…), `http_poll`, `loki`, `file` |
| source (listen) | `webhook` (Fluent Bit / Vector / apps POST logs), `syslog` (UDP) |
| decider | `jev`, `rules`, `llm`, `chain` |
| analyzer | `gemini`, `openai` (OpenRouter/OpenAI/Ollama/…), `chain`, `none` |
| notifier | `telegram`, `slack`, `discord`, `teams`, `webhook`, `console` |

### Loki service names

A `loki` source names each event's service after the stream's `service_name`
(else `app`, else `job`) label. On Coolify that label carries a resource id, so
the source can rename it:

```yaml
- name: coolify-loki
  type: loki
  strip_service_ids: true          # `jobs-scheduler-<id>` → `jobs-scheduler`
  service_names:                   # label or Coolify id → shown name
    k3v9q2m7x1c8b4n6z0p5r2t7: web-api   # `<id>-proxy` → `web-api-proxy`
```

To watch chosen services instead of everything the query matches, list them by
their shown name under `services:`. The source adds a `service_name` matcher for
them to the query's stream selector, so Loki returns only those services. List the
services a source can see, with their shown names and which are watched, with:

```
herjev loki services [source]
```

The config is only the default. In the portal, the **Service** button (or
"Chọn và đổi tên service" in the command palette) lists each Loki source's
services, as the watcher last read them from Loki (refreshed every minute, limited
to the query's stream selector). Ticking services there makes the watcher watch
exactly those; unticking them all, or "Về cấu hình", hands the choice back to
`services:`. A name typed there replaces the config's name. Choices live in
`herjev.source_services`, and the watcher applies them within about 15 seconds,
without a restart.

The service is part of an incident's fingerprint, so a renamed service starts new
tickets; tickets created before the rename keep the old name.

## Alert format

The format is defined once, in `src/herjev/formatting.py` (`render_text`); change
it there and every channel follows. The full analysis (impact, causes, actions,
diagnostic commands) and the decision trace are on the ticket behind `open:`
(when `dashboard.public_url` is set) and in the webhook payload.

## Config and state

Config is found at `--config`, `$HERJEV_CONFIG`, `./herjev.yaml`,
`$HERJEV_HOME/herjev.yaml` or `~/.config/herjev/herjev.yaml`. State (incident DB,
status, ssh known_hosts) lives in `$HERJEV_HOME`, default `~/.herjev`.

## Keys

| Key | Used for | Get it |
|---|---|---|
| `OPENROUTER_API_KEY` | Jev (`typesafe/jev-1.13`) and the OpenRouter LLM fallback | https://openrouter.ai/settings/keys |
| `GEMINI_API_KEY` | Gemini analysis | https://ai.google.dev/gemini-api/docs/api-key |

Keys are read from the environment or a `.env` next to the config.

## CLI

```
herjev run [--dry-run] [--duration N]   watch and alert
herjev check                            validate config, print wiring
herjev providers                        list provider types
herjev analyze [file|-] [--notify]      triage + analyze pasted logs
herjev test-channel [name]              send a synthetic alert
herjev status [--max-age N]             watcher status (exit 1 if not running or stale)
herjev incidents [--hours N] [--min-level L] [--json]
                                        recent incident history
herjev incident <id|fingerprint>        one incident in full, as JSON
herjev mute <id|fingerprint> [--minutes N] [--reason R]
                                        mute an incident's alerts (default 60 minutes)
herjev teams login|whoami|chats         Teams account for the teams channel
herjev loki services [source]           services a loki source sees (* = watched)
herjev handoff setup [--portal-url U] [--agent A] [--runtime R] [--terminal T] [--template S]
                                        register herjev:// links for the portal's Handoff button
herjev handoff setup --uninstall [--purge]
                                        unregister them (--purge also deletes handoff.json)
herjev handoff open <herjev://…>        what a herjev:// link runs
```

## Pushing logs (listen mode)

Fluent Bit:

```ini
[OUTPUT]
    Name   http
    Match  *
    Host   herjev.internal
    Port   8787
    URI    /logs
    Format json
    Header Authorization Bearer ${HERJEV_WEBHOOK_TOKEN}
```

Or: `curl -H "Authorization: Bearer $T" -d 'panic: boom' -H 'Content-Type: text/plain' http://host:8787/logs`
