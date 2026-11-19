# Running herjev in Docker

The image (`docker/Dockerfile`) is a plain Python image with herjev, the ssh
extra and the OpenSSH client. It runs `herjev run`, which watches the configured
sources and records incidents in Postgres. `docker-compose.yml` runs it next to a
local Postgres (`herjev-postgres`) that gets herjev's schema and both database
roles on first start. The portal is a Cloudflare Worker, not part of the image;
[docs/dashboard.md](docs/dashboard.md) shows how to run it locally, and
`docs/deployment.md` covers the cloud setup (VM, Supabase, Worker).

## Quick start

```bash
cp .env.docker.example .env
# set HERJEV_UID/HERJEV_GID to `id -u` / `id -g` so files in the data dir are yours

# 1. Keys (Jev via OpenRouter, Gemini for analysis) + webhook token
mkdir -p herjev-data
cat >> herjev-data/.env <<'KEYS'
OPENROUTER_API_KEY=sk-or-v1-...
GEMINI_API_KEY=...
HERJEV_WEBHOOK_TOKEN=pick-a-long-random-string
KEYS

# 2. Start. The first boot seeds herjev-data/herjev.yaml.
docker compose up -d --build
docker compose logs -f

# 3. Edit herjev-data/herjev.yaml (sources, channels, routes), then:
docker compose restart
```

Check it:

```bash
docker compose ps                        # STATUS shows (healthy) once the watcher runs
docker exec herjev herjev check          # validate config and wiring
docker exec herjev herjev test-channel   # send a synthetic alert to every channel
curl -H "Authorization: Bearer $TOKEN" -H 'Content-Type: text/plain' \
     -d 'panic: runtime error: nil map' http://localhost:8787/logs
docker exec herjev herjev incidents
```

The watcher connects as `herjev_watcher` to the compose Postgres by default. To
use another database (Supabase, say), set `HERJEV_DATABASE_URL` in `.env`; the
schema must already be there (`scripts/apply-migrations.sh <url>`).

## What happens at boot

`docker/entrypoint.sh` runs as the `herjev` user (built with your
`HERJEV_UID`/`HERJEV_GID`), then starts `herjev run`:

* seeds `/data/herjev.yaml` from `examples/herjev.docker.yaml` if missing,
* copies SSH keys from `/opt/herjev-ssh` into `/data/ssh` with `600` perms.

`HERJEV_HOME=/data` and `HERJEV_CONFIG=/data/herjev.yaml`, so the config, its
`.env`, the status file and `known_hosts` all live in the data dir. Incidents
live in Postgres, in the `herjev-pg` volume.

## Getting logs into the container

| Where logs are | Source to use | Docker bits |
|---|---|---|
| Remote servers, pushed by Fluent Bit/Vector/app | `webhook` (default in the starter config) | port `8787` published; **set a token** |
| Remote servers over SSH | `ssh` with `identity_file: /data/ssh/id_ed25519` | put keys in `./ssh/`; host keys pinned on first connect into `/data/known_hosts` |
| Syslog senders | `syslog` | port `5514/udp` published |
| Loki / HTTP API on the Docker host | `loki` / `http_poll` with `http://host.docker.internal:…` | `extra_hosts` already set |
| Files on the Docker host | `file` with `path: /host/log/...` | uncomment the `/var/log:/host/log:ro` volume |
| k8s / docker logs | `command` (`kubectl logs -f …`) | only if you add `kubectl` + kubeconfig to the image |

## Delivery

Channels are configured in `herjev.yaml`: `telegram`, `slack`, `discord`,
`webhook` and `console`. `console` alerts appear in `docker compose logs`.

To hand incidents to a chat agent on the Docker host (for example a Hermes
webhook route on port 8644), use a `webhook` channel with
`url: http://host.docker.internal:8644/webhooks/<route>` and the route's secret
in a static header (see the commented `agent` channel in the starter config).

An agent can also call back into this herjev instance: set `mcp_server.enabled:
true` in `herjev.yaml`, put `HERJEV_MCP_TOKEN` in `$HERJEV_DATA/.env` and
restart. The MCP endpoint listens on port `8789`, which compose publishes on the
host's `127.0.0.1`; an agent container on the same Docker host reaches it at
`http://host.docker.internal:8789/mcp` (Docker Desktop) or `http://herjev:8789/mcp`
on a shared Docker network. It offers only herjev's on-call tools, so the agent
never gets a shell in the container. See [docs/agents.md](docs/agents.md) for the
skill setup.

For the `teams` channel, sign the Teams account in once inside the container
(the token is kept in `/data/teams_token.json`):
`docker exec -it herjev herjev teams login`, then
`docker exec herjev herjev teams chats` to find `TEAMS_CHAT_ID`.

## Configuration reference (compose `.env`)

| Variable | Default | Meaning |
|---|---|---|
| `HERJEV_DATA` | `./herjev-data` | host dir mounted at `/data` (`herjev.yaml`, `.env`, status) |
| `HERJEV_UID` / `HERJEV_GID` | `1000` | host user to own the data files (build args) |
| `HERJEV_SSH_DIR` | `./ssh` | SSH keys for `ssh` sources |
| `HERJEV_WEBHOOK_PORT` / `HERJEV_SYSLOG_PORT` | `8787` / `5514` | published ports |
| `HERJEV_MCP_PORT` | `8789` | host port of the agent MCP endpoint, published on `127.0.0.1` only |
| `HERJEV_POSTGRES_PORT` | `5432` | host port of the local Postgres, published on `127.0.0.1` only (tests, `wrangler dev`) |
| `POSTGRES_PASSWORD` | `postgres` | password of the local `postgres` superuser |
| `HERJEV_WATCHER_PASSWORD` / `HERJEV_PORTAL_PASSWORD` | `watcher` / `portal` | local passwords of the two herjev roles; applied only when the `herjev-pg` volume is created |
| `HERJEV_DATABASE_URL` | the compose Postgres as `herjev_watcher` | the watcher's incident store |

## Notes

* **Upgrading herjev**: pull the repo, apply new migrations to the local
  database (`scripts/apply-migrations.sh postgresql://postgres:postgres@127.0.0.1:5432/postgres`;
  the compose Postgres applies them by itself only when its volume is new), then
  `docker compose up -d --build`. State in `herjev-data/` and `herjev-pg` is kept.
  Back up first with `docker exec herjev-postgres pg_dump -U postgres --schema=herjev -Fc > herjev.dump`.
* **Changing UID/GID**: they are build args, so rebuild after changing them.
* **Starting over**: `docker compose down -v` deletes the `herjev-pg` volume and
  every incident in it.
