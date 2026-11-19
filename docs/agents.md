# Chat agents and MCP

How to hand alerts to a chat agent, and let the agent query herjev back.

To let a chat agent follow up on alerts, point a `webhook` channel at it: herjev
POSTs the alert as JSON (`text` plus the published alert shape: `schema_version`,
`link`, `incident`, `decision`, `analysis`; see "Contract schemas" in
`ARCHITECTURE.md`) and the agent takes it from there. For a [Hermes Agent](https://hermes-agent.nousresearch.com/),
create a webhook route that posts `text` to a chat as it is
(`hermes webhook subscribe herjev --prompt '{text}' --deliver slack --deliver-only`;
`--connect` below does this) and send its secret as a static header:

```yaml
channels:
  agent:
    type: webhook
    url: http://127.0.0.1:8644/webhooks/herjev
    headers: {X-Gitlab-Token: "${AGENT_WEBHOOK_TOKEN}"}
```

To let the agent look up incidents, analyze pasted logs and mute noise, herjev
serves an MCP endpoint and the `herjev-oncall` skill uses its tools (`status`,
`incidents`, `incident`, `analyze`, `mute`). There is no shell and no file access
behind them; alert text comes from production logs and must never reach a
terminal. In a chat, ask the agent about an alert and it finds the incident
through these tools. It needs
`herjev[mcp]` (installed by default in this checkout and the Docker image) and a
bearer token:

```yaml
mcp_server:
  enabled: true
  host: 127.0.0.1        # 0.0.0.0 inside a container
  port: 8789             # http://<host>:8789/mcp
  token: ${HERJEV_MCP_TOKEN}
```

From the herjev instance, `--connect` wires a Hermes instance to it in both
directions. It:
- installs the skill;
- generates `HERJEV_MCP_TOKEN` in herjev's `.env` when missing and copies it to
  the Hermes `.env`;
- adds the `herjev` MCP server to the Hermes config and checks it with `hermes mcp test`;
- creates the webhook route and makes it post each alert's `text` as it is,
  with no agent run (`deliver_only`), so the chat shows the same message as
  every other channel. A route without a chat (`--deliver log`) runs the skill
  instead, limited to the `mcp-herjev` toolset.

It then prints the `herjev.yaml` settings and the `.env` secret to add:

```bash
scripts/install-hermes-skill.sh --connect --herjev-url http://127.0.0.1:8789/mcp \
  --deliver slack --chat-id C0123456789
scripts/install-hermes-skill.sh --docker hermes --connect --herjev-url http://herjev:8789/mcp
```

`--herjev-url` is the endpoint as the Hermes instance reaches it (for example
`http://host.docker.internal:8789/mcp`, or `http://herjev:8789/mcp` on a shared
Docker network). Use `--ssh ops@agent-box` instead of `--docker` for a Hermes
instance on another machine. Re-running is safe: it keeps the token, and it keeps
an existing route unless you pass `--replace-route`. Without `--connect` the
script only installs or updates the skill (a changed one is kept as a
timestamped backup). Restart the Hermes gateway afterwards.
