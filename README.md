<p align="center">
  <img src="marketing/video/out/hero.png" alt="herjev: only the alerts that matter" width="100%">
</p>

<h1 align="center">herjev</h1>

<p align="center">
  <b>Watches your logs. Wakes you only for what matters.</b><br>
  Jev triages every warn, error, fatal and panic. An LLM finds the likely root cause.<br>
  One clear message lands in Telegram, Slack, Discord, Teams or your agent.
</p>

<p align="center">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="Postgres" src="https://img.shields.io/badge/store-Postgres-4169E1?logo=postgresql&logoColor=white">
  <img alt="Cloudflare Workers" src="https://img.shields.io/badge/portal-Cloudflare%20Workers-F38020?logo=cloudflare&logoColor=white">
  <img alt="MCP" src="https://img.shields.io/badge/agents-MCP-111111">
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#documentation">Docs</a> ·
  <a href="marketing/video/out/herjev-intro.mp4">Watch the 30s intro</a>
</p>

---

## From a wall of logs to one message

<p align="center">
  <a href="marketing/video/out/herjev-intro.mp4">
    <img src="marketing/video/out/herjev-intro.gif" alt="herjev intro: log flood, Jev triage, LLM analysis, delivery" width="800">
  </a>
</p>

Every channel receives the same message:

```
loại: fatal
thời gian: 02:13:05 30/10
nội dung: FATAL pq: remaining connection slots are reserved for non-replication superuser connections
service: payments
root cause dự đoán: Connection leak in payments after the 01:50 deploy (pool never returns conns)
open: https://herjev.example.workers.dev/tickets/9480943d491588a4?occ=3f9c1a2b7d10
```

The link opens the ticket with the full analysis: impact, causes, a checklist of
actions, diagnostic commands and the decision trace.

## How it works

```mermaid
flowchart LR
  S["📥 Sources<br/>ssh · loki · file<br/>webhook · syslog"] --> A["🧺 Aggregate"]
  A --> J{"🧠 Jev triage<br/>worth a human?"}
  J -- no --> X["🔕 stored, silent"]
  J -- yes --> L["🔍 LLM analysis<br/>root cause + advice"]
  L --> R["🧭 Route"]
  R --> C["💬 Telegram · Slack · Discord<br/>Teams · Webhook · Console"]
```

## What you get

| | |
|---|---|
| 🧠 **AI triage**<br>[Jev](https://docs.typesafe.ai/introduction) decides whether a warn, error, fatal or panic deserves a person, with rules and LLM fallbacks. | 🔍 **Root cause in the alert**<br>Gemini (or any OpenAI-compatible model) predicts the cause and writes a runbook. |
| 💬 **One format, every channel**<br>Telegram, Slack, Discord, Teams, JSON webhook or console, all from `src/herjev/formatting.py`. | 🗂️ **Ticket board**<br>A Cloudflare Worker portal with swimlanes, drag-and-drop status and the full decision timeline. |
| 🤖 **Agent-ready**<br>An MCP endpoint (`status`, `incidents`, `incident`, `analyze`, `mute`) and a one-command Hermes setup. | 🛠️ **Handoff to your coding agent**<br>One click opens Claude Code, Codex or Gemini on the ticket to check the prediction against your code. |

## Quick start

```bash
# install and start the incident store
uv sync --extra dev
docker compose up -d postgres
export HERJEV_DATABASE_URL=postgresql://herjev_watcher:watcher@127.0.0.1:5432/postgres

# try it with no keys and no servers
herjev -c examples/local-demo.yaml run &
echo 'panic: runtime error: invalid memory address' >> demo.log
```

For a real setup, copy `examples/herjev.yaml` and `.env.example`, add your keys,
then run `herjev check` and `herjev test-channel <name>`. `docker compose up -d`
runs the same watcher in a container.

| Key | Used for |
|---|---|
| `OPENROUTER_API_KEY` | Jev triage and the OpenRouter LLM fallback ([get one](https://openrouter.ai/settings/keys)) |
| `GEMINI_API_KEY` | Gemini analysis ([get one](https://ai.google.dev/gemini-api/docs/api-key)) |

## Documentation

| Guide | What it covers |
|---|---|
| [Configuration and CLI](docs/configuration.md) | Pipeline, provider types, config lookup, keys, every command, pushing logs |
| [Dashboard](docs/dashboard.md) | The ticket board, alert links, access tokens, storage, local development |
| [Handoff](docs/handoff.md) | Sending a ticket to Claude Code, Codex, agy or Gemini |
| [Chat agents and MCP](docs/agents.md) | Webhook to an agent, the MCP server, Hermes `--connect` |
| [Microsoft Teams](docs/teams.md) | Posting alerts to a Teams group chat |
| [Docker](DOCKER.md) | Compose setup, getting logs in, SSH keys |
| [Deployment](docs/deployment.md) | Cloud setup, secrets and rollback ([runbook](docs/deploy-runbook.md)) |
| [Architecture](ARCHITECTURE.md) | Design, extension API, switching providers |

## Development

```bash
docker compose up -d postgres
uv run --extra dev pytest -q
cd dashboard && npm ci && npm run build && npm test
```

Both suites need a Postgres they can create databases in:
`HERJEV_TEST_DATABASE_URL`, defaulting to the compose Postgres as `postgres`.
CI runs the same on every pull request (`.github/workflows/ci.yml`).

Marketing media (intro video, hero and social images) is rendered with Remotion
from `marketing/video/`.
