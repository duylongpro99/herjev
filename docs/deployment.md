# Deployment

How herjev runs in the cloud, where every secret lives, how to build the environment
from nothing, and how to roll back. Everything here fits the free tiers of the
services involved. For a tick-box walkthrough of a deploy, see
[deploy-runbook.md](deploy-runbook.md).

## Architecture

```
 Loki / Grafana ──poll──▶ watcher (Docker, GCP e2-micro VM)
                              │  herjev_watcher role
                              ▼
                     Supabase Postgres, schema "herjev"
                              ▲  herjev_portal role, through Hyperdrive
                              │
 browser ──HTTPS──▶ portal Worker (Cloudflare, *.workers.dev): UI + /api/*

 watcher ──webhook (NetBird)──▶ Hermes on your machine, :8644
 Hermes  ──MCP/HTTP (NetBird)─▶ watcher's mcp_server, VM :8789
 watcher ──Graph (HTTPS)──────▶ Teams group chat, as a signed-in user
 GitHub Actions ──moves :production──▶ GHCR ◀──pull every 2 min── VM (herjev-update.timer)
```

- **Watcher**: the `herjev` image from `docker/Dockerfile`, run by
  `deploy/compose.prod.yml` on the VM. It is the only process that writes incidents.
- **Database**: Supabase Postgres. All tables live in schema `herjev`, which is not
  exposed to Supabase's Data API. Migrations are in `supabase/migrations/`; only the
  deploy workflow applies them to production.
- **Portal**: the Cloudflare Worker in `dashboard/` (`worker/` is the API,
  `src/` is the UI). It reads incidents, moves tickets and queues manual channel
  sends (which the watcher delivers) through Hyperdrive.
- **Network**: the VM listens on nothing public, and nothing connects to it to deploy:
  it pulls each release itself. Hermes reaches it over NetBird; admin access is GCP IAP
  SSH.
- **Release**: CI builds `ghcr.io/duylongpro99/herjev:<sha>` and, once the database and
  the portal are deployed, points the `:production` tag at it. On the VM,
  `herjev-update.timer` runs `deploy/update-watcher.sh` every 2 minutes: when
  `:production` names a new commit, it switches the container and waits for the health
  check; an unhealthy release is rolled back on the VM and not retried. CI's `verify`
  job then waits until the portal's `/api/status` reports the watcher on that commit.

### Database roles

| Role | Used by | Rights |
|------|---------|--------|
| `postgres` | CI only (`SUPABASE_DB_URL`) | owner; runs backups and migrations |
| `herjev_watcher` | watcher (`HERJEV_DATABASE_URL`) | read and write every herjev table |
| `herjev_portal` | Worker, via Hyperdrive | read everything; insert and update `tickets`; insert the request columns of `channel_sends`; nothing else; 3 s statement timeout |

The roles are created by the first migration without passwords. You set the
passwords once by hand (step 3 below); migrations never touch them again.

## Secrets

Never commit any of these. `.env*` files other than the examples are git-ignored.

### GitHub: repository → Settings → Environments → `production`

| Secret | What it is | Used by |
|--------|------------|---------|
| `SUPABASE_DB_URL` | session pooler URL as `postgres` (port 5432) | `migrate`: backup and `supabase db push` |
| `BACKUP_AGE_RECIPIENT` | `age1...` public key; the private key stays offline with you | `migrate`: encrypts the backup |
| `CLOUDFLARE_API_TOKEN` | token from the *Edit Cloudflare Workers* template, plus *Hyperdrive: Edit* (the first deploy also enables the workers.dev route; if it fails on permissions, check the token first) | `worker` |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account id | `worker` |
| `HERJEV_DASHBOARD_TOKEN` | the portal login token (the same value as the Worker secret) | `verify`: reads `/api/status` |

Also add the environment **variable** (not secret) `PORTAL_URL`, the portal's base URL
(`https://herjev.<subdomain>.workers.dev`), used by `verify`.

CI pushes and tags the image with the workflow's own `GITHUB_TOKEN`. The VM pulls it with
a read-only token of its own (step 7).

### Cloudflare Worker secret

| Secret | What it is |
|--------|------------|
| `HERJEV_DASHBOARD_TOKEN` | the portal login token. Sent as cookie `herjev_token` (set by the login page) or `Authorization: Bearer <token>` |

The Hyperdrive config holds the `herjev_portal` password; it is set when the config is
created and never appears in `wrangler.jsonc`.

### VM: `/opt/herjev/data/.env` (mode 640, owner and group 1000; read by compose as root)

| Variable | What it is |
|----------|------------|
| `HERJEV_DATABASE_URL` | session pooler URL as `herjev_watcher` |
| `OPENROUTER_API_KEY` | Jev triage (`decider` in `herjev.yaml`) |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | root-cause analyzer |
| `GRAFANA_VIEWER_TOKEN` | Grafana Viewer service-account token for the Loki source |
| `HERMES_WEBHOOK_TOKEN` | secret of the Hermes webhook route |
| `HERMES_HOST` | NetBird IP of the machine running Hermes |
| `HERJEV_MCP_TOKEN` | bearer token of the MCP endpoint Hermes calls (`openssl rand -hex 32`) |
| `TEAMS_APP_ID`, `TEAMS_TENANT_ID`, `TEAMS_CHAT_ID` | Teams channel: Entra app, tenant and group chat |
| `TEAMS_ENABLED`, `HERMES_ENABLED` | optional, `true` when unset; `false` or an empty value (`TEAMS_ENABLED=`) stops alerts to that channel (restart the watcher) |

`/opt/herjev/.env` holds only `NETBIRD_IP`, written by `deploy/vm-bootstrap.sh`;
compose publishes the MCP endpoint on that address. The GHCR read token lives in root's
Docker credentials (`/root/.docker/config.json`), written by `docker login`.

## First deploy, from nothing

Do the steps in order. Where a value is needed later, it is named in **bold**.

### 1. Supabase

1. Create a project in region **us-west-1** (same coast as the VM). Keep the
   `postgres` password in your password manager.
2. Settings → Database → Connection string → **Session pooler** (port 5432). With
   the password filled in, this is **`SUPABASE_DB_URL`**:
   `postgresql://postgres.<ref>:<password>@aws-0-us-west-1.pooler.supabase.com:5432/postgres`
3. Settings → API → Exposed schemas: confirm `herjev` is **not** listed.

### 2. GitHub secrets for the first migration

1. Create an age key pair on your machine and keep the private key offline:
   `age-keygen -o herjev-backup.key` prints the public key → **`BACKUP_AGE_RECIPIENT`**.
2. Repository → Settings → Environments → New environment `production`. Add
   `SUPABASE_DB_URL` and `BACKUP_AGE_RECIPIENT`.

### 3. Run the migrations and set role passwords

1. Actions → deploy → Run workflow on `main`. The `migrate` job skips the backup
   (there is no schema yet) and creates schema `herjev` and both roles. The `worker` job fails until the Cloudflare
   secrets exist, and `release` and `verify` are skipped; that is expected.
2. In the Supabase SQL editor, set the passwords (generate each with
   `openssl rand -base64 24`):

   ```sql
   alter role herjev_watcher password '<watcher password>';
   alter role herjev_portal  password '<portal password>';
   ```

   The pooler user names are `herjev_watcher.<ref>` and `herjev_portal.<ref>`.

### 4. Cloudflare

1. My Profile → API Tokens → Create token with *Account · Workers Scripts · Edit* and
   *Account · Hyperdrive · Edit* → **`CLOUDFLARE_API_TOKEN`**. The account id is on
   the Workers overview page → **`CLOUDFLARE_ACCOUNT_ID`**. Add both to the
   `production` environment.
2. Create the Hyperdrive config as `herjev_portal`, with caching off so a ticket move
   is visible on the next read:

   ```sh
   cd dashboard
   npx wrangler login
   npx wrangler hyperdrive create herjev --caching-disabled \
     --connection-string "postgresql://herjev_portal.<ref>:<portal password>@aws-0-us-west-1.pooler.supabase.com:5432/postgres"
   ```

   Put the printed `id` into `dashboard/wrangler.jsonc` (`hyperdrive[0].id`) and commit
   it; the id is not a secret.
3. Set the login token (paste the output of `openssl rand -hex 32` when asked, and
   keep it in your password manager):

   ```sh
   npx wrangler secret put HERJEV_DASHBOARD_TOKEN
   ```

4. After the first Worker deploy, the URL is `https://herjev.<subdomain>.workers.dev`.
   Set it as `HERJEV_PUBLIC_URL` in `wrangler.jsonc` `vars`, and as
   `dashboard.public_url` in the VM's `herjev.yaml` so alerts link to tickets.

### 5. GCP VM

1. Attach a billing account to the project (required even for the free tier) and
   create a budget alert at 1 USD.
2. Firewall: allow tcp:22 ingress only from `35.235.240.0/20` (IAP); no other ingress
   rule. Delete the default `allow-ssh`, `allow-rdp` and `allow-icmp` rules if present.

   ```sh
   gcloud compute firewall-rules create herjev-iap-ssh --network=default \
     --direction=INGRESS --allow=tcp:22 --source-ranges=35.235.240.0/20
   ```

3. Create the VM. It keeps an ephemeral public IP for outbound traffic (Cloud NAT
   costs money), but the firewall admits nothing on it.

   ```sh
   gcloud compute instances create herjev-vm --zone=us-west1-b --machine-type=e2-micro \
     --image-family=debian-12 --image-project=debian-cloud \
     --boot-disk-size=30GB --boot-disk-type=pd-standard
   ```

### 6. NetBird

The VM and your machine join one NetBird network (NetBird Cloud, `https://api.netbird.io`).
Access is granted by group policies; nothing is open once the Default policy is gone.
CI does not join it.

1. Install NetBird on your machine (the one running Hermes), sign in with `netbird up`,
   and note its address (`netbird status -4`) → **`HERMES_HOST`**.
2. Dashboard → Peers → your machine → add it to group **`hermes`**.
3. Access Control → Policies: add these two policies, then **delete the `Default`
   policy** (it lets every peer reach every other peer on any port):

   | Name | Source | Destination | Protocol | Ports | Why |
   |------|--------|-------------|----------|-------|-----|
   | `hermes-mcp` | `hermes` | `herjev-vm` | TCP | 8789 | Hermes calls the watcher's MCP endpoint |
   | `herjev-alerts` | `herjev-vm` | `hermes` | TCP | 8644 | the watcher posts alerts to Hermes |

   Keep each policy unidirectional; replies to an allowed connection still flow.
4. Setup Keys → create a **one-off** key auto-assigned to group **`herjev-vm`** for the
   bootstrap. It is used once and not stored.

### 7. Bootstrap the VM

1. Copy the bootstrap with the two files it installs, and run it over IAP:

   ```sh
   gcloud compute scp deploy/vm-bootstrap.sh deploy/update-watcher.sh deploy/compose.prod.yml \
     herjev-vm:~ --zone=us-west1-b --tunnel-through-iap
   gcloud compute ssh herjev-vm --zone=us-west1-b --tunnel-through-iap -- \
     "sudo NB_SETUP_KEY=<one-off key> bash vm-bootstrap.sh"
   ```

   It adds 1 GB swap, installs Docker and NetBird, joins NetBird as `herjev-vm` (with
   NetBird's own SSH server off), disables password and root ssh login, lays out
   `/opt/herjev`, and enables `herjev-update.timer`. It prints the VM's NetBird address
   → **`VM_NB_HOST`**, used by Hermes. Running it again is safe; run it again whenever
   `compose.prod.yml` or `update-watcher.sh` changes, since CI no longer copies them.
2. Let the VM pull the private image. On GitHub, create a **classic** personal access
   token with only the `read:packages` scope (GHCR does not accept fine-grained tokens),
   then on the VM:

   ```sh
   sudo docker login ghcr.io -u duylongpro99   # paste the token as the password
   ```

   If the token expires, the VM stops picking up releases and `verify` fails.
3. Fill the watcher's secrets and config (see the VM table above for the variables):

   ```sh
   gcloud compute scp herjev.yaml herjev-vm:~ --zone=us-west1-b --tunnel-through-iap
   gcloud compute ssh herjev-vm --zone=us-west1-b --tunnel-through-iap
   # on the VM:
   sudo install -m 640 -o 1000 -g 1000 ~/herjev.yaml /opt/herjev/data/herjev.yaml
   sudo -u '#1000' sh -c 'cat > /opt/herjev/data/.env'   # paste the variables, then Ctrl-D
   ```

   The updater does nothing until both files are filled in. Set `dashboard.public_url`
   in `/opt/herjev/data/herjev.yaml` once the portal URL is known. The watcher uses the
   session pooler URL as `herjev_watcher.<ref>`.

### 8. Deploy

Actions → deploy → Run workflow on `main` (or push to `main`). Jobs, in order:

1. `test`: the same checks as `ci.yml`.
2. `image`: builds and pushes `ghcr.io/duylongpro99/herjev:<sha>`. There is no `:latest` tag; the VM always runs a named sha.
3. `migrate`: encrypted `pg_dump` of schema `herjev` uploaded as artifact
   `db-backup-<sha>` (kept 7 days), then `supabase db push`.
4. `worker`, once `migrate` and `image` pass: builds the UI and runs `wrangler deploy`.
5. `release`, once `worker` passes: points `ghcr.io/duylongpro99/herjev:production` at
   the release (this commit, or `image_tag`). Nothing is rebuilt.
6. `verify`: waits up to 15 minutes until `PORTAL_URL/api/status` reports the watcher
   on the release with a fresh status. Within that time the VM pulls `:production`,
   switches the container and waits up to 3 minutes for it to be healthy. If it is not,
   the VM goes back to the previous commit, records the bad one in
   `/opt/herjev/failed-tag` so it is not retried, and `verify` fails. Read why on the VM
   (`journalctl -u herjev-update`, `docker logs herjev`); the watcher's log can contain
   customer log lines, so it stays off CI.

The first deploy publishes a private GHCR package; the VM reads it with the token from
step 7. Images built before the `HERJEV_VERSION` build argument existed carry no commit
label, and the VM refuses them as a release.

### 9. Connect Hermes and Teams

Hermes stays on your machine and reaches the watcher's MCP endpoint over NetBird.
Run the installer locally with a scratch env file; it generates the token there,
gives Hermes the same token, installs the `herjev-oncall` skill and creates the
webhook route that posts each alert to the chat as it is:

```sh
scripts/install-hermes-skill.sh --docker hermes --connect \
  --herjev-url http://<VM_NB_HOST>:8789/mcp --env-file ./herjev-connect.env
cat ./herjev-connect.env   # HERJEV_MCP_TOKEN=...
```

Put that `HERJEV_MCP_TOKEN` line, and the route secret the installer prints as
`HERMES_WEBHOOK_TOKEN`, into `/opt/herjev/data/.env`; delete the scratch file.
The VM's `herjev.yaml` needs the `mcp_server` block from this repository's
`herjev.yaml` (`host: ${HERJEV_MCP_HOST:-127.0.0.1}`, set to `0.0.0.0` by
`compose.prod.yml`). Then recreate the container on the VM so it reads the files
again (the same command after any `.env` change):

```sh
cd /opt/herjev
tag="$(sudo docker inspect herjev --format '{{.Config.Image}}')"
sudo HERJEV_IMAGE_TAG="${tag##*:}" docker compose -f compose.prod.yml up -d --force-recreate --wait
```

The installer's reachability check (`hermes mcp test herjev`) fails if it runs
before the token is on the VM; run `hermes mcp test herjev` again afterwards.

For Teams, add `TEAMS_APP_ID`, `TEAMS_TENANT_ID` and `TEAMS_CHAT_ID` to
`/opt/herjev/data/.env`, add `teams: {type: teams}` to the VM's `herjev.yaml`
channels, recreate the container, and sign the posting account in once inside
it (the token is kept in `/opt/herjev/data/teams_token.json`):

```sh
sudo docker exec -it herjev herjev teams login
sudo docker exec herjev herjev test-channel teams
```

### 10. Verify

- [ ] Every job of the deploy run is green, `verify` included.
- [ ] On the VM, `systemctl list-timers herjev-update.timer` shows the next run, and
      `journalctl -u herjev-update` ends with `healthy on <sha>`.
- [ ] On the VM, `docker logs herjev` has no `incident store unavailable` line. The
      watcher keeps alerting without history when the database is wrong at start, and
      its health check does not notice.
- [ ] `https://herjev.<subdomain>.workers.dev` asks for the token and accepts it.
- [ ] `curl -H "Authorization: Bearer <token>" https://herjev.<subdomain>.workers.dev/api/status`
      returns the watcher status with a recent timestamp.
- [ ] An error log in Loki (or a real one) shows up on the board within a minute and
      Hermes receives the webhook.
- [ ] Moving a ticket survives a reload. Moving the same ticket from two tabs gives the
      second tab a conflict.
- [ ] "Gửi tới kênh" on a ticket lists the watcher's channels; a send turns to "đã gửi"
      within a few seconds and the message arrives in the channel.
- [ ] Hermes can run `herjev incidents` through the skill.
- [ ] From a machine outside NetBird, `nmap -Pn <VM public IP>` shows no open
      ports. From a NetBird peer outside group `hermes`, port 8789 on `VM_NB_HOST` is
      refused or times out.
- [ ] Run the deploy workflow with an older `image_tag` (one built with this release
      flow); `verify` passes on it. Then run it again with the current sha.
- [ ] After a day, check the database size (`select pg_database_size(current_database())`
      in the SQL editor), Hyperdrive queries per day (Cloudflare dashboard) and the
      VM's memory (`docker stats --no-stream`). Lower `pipeline.store_retention_days`
      if the database grows faster than about 5 MB a day.

## Rollback

| What | How |
|------|-----|
| Portal | `cd dashboard && npx wrangler deployments list`, then `npx wrangler rollback <version-id> -m "<reason>"` |
| Watcher | Actions → deploy → Run workflow with `image_tag` = an earlier full commit sha. Tests, migrations and the Worker deploy run again from the current `main` first, so this only works while `main` itself passes; otherwise use the next row |
| Watcher, without CI | Move the tag from your machine: `docker buildx imagetools create -t ghcr.io/duylongpro99/herjev:production ghcr.io/duylongpro99/herjev:<sha>` (after `docker login ghcr.io` with a `write:packages` token). The VM follows within 2 minutes |
| Watcher, VM only | IAP ssh to the VM, `sudo systemctl stop herjev-update.timer`, then `cd /opt/herjev && sudo HERJEV_IMAGE_TAG=<sha> docker compose -f compose.prod.yml up -d --wait`. Start the timer again once `:production` is fixed |
| Database | see below |

Migrations only move forward. To undo one, write a new migration that reverses it and
deploy that. Restoring a backup is for data loss, not for schema changes.

### Restore a backup

1. Actions → the deploy run before the bad change → Artifacts → download
   `db-backup-<sha>` and unzip it.
2. Decrypt with the offline key and restore the schema in place:

   ```sh
   age -d -i herjev-backup.key herjev-<sha>.dump.age > herjev.dump
   pg_restore --dbname="$SUPABASE_DB_URL" --clean --if-exists --schema=herjev \
     --no-owner herjev.dump
   rm herjev.dump
   ```

   `pg_restore` must be major version 17 or newer, matching Supabase. Stop the watcher
   first (`docker stop herjev` on the VM) so it does not write during the restore, and
   start it again afterwards.

## Rotating secrets

| Secret | Rotation |
|--------|----------|
| Portal token | `npx wrangler secret put HERJEV_DASHBOARD_TOKEN`; applies at once and logs everyone out. Update the GitHub secret `HERJEV_DASHBOARD_TOKEN` to the same value |
| `herjev_portal` password | `alter role herjev_portal password '...'`, then `npx wrangler hyperdrive update <id> --origin-password '...'` |
| `herjev_watcher` password | `alter role ...`, update `HERJEV_DATABASE_URL` in `/opt/herjev/data/.env`, recreate the container (step 9) |
| `postgres` password | Supabase → Settings → Database → Reset password, then update `SUPABASE_DB_URL` |
| GHCR read token (VM) | new classic token with `read:packages`; `sudo docker login ghcr.io -u duylongpro99` on the VM; delete the old token on GitHub |
| Backup key | new `age-keygen`, update `BACKUP_AGE_RECIPIENT`; keep the old private key until the last backup made with it expires (7 days) |
| API keys in `data/.env` | edit the file, recreate the container (step 9) |

## Free-tier limits

| Service | Limit that matters | herjev's use |
|---------|-------------------|--------------|
| Supabase Free | 500 MB database; the project pauses after 7 days without activity | the watcher writes every poll, so it never idles; `store_retention_days: 14` bounds the size |
| Cloudflare Workers Free | 100 000 requests a day; 10 ms CPU per request | the board polls every few seconds per open tab |
| Hyperdrive (Free plan) | a daily query quota | one or two queries per API request |
| GCP e2-micro | one free instance in us-west1, us-central1 or us-east1; 30 GB standard disk; 1 GB egress a month | the watcher's limit is 512 MB of memory, with 1 GB swap; it polls the database for mutes every 5 s, so watch egress in the first week |
| GitHub Actions | free minutes on public repos; 500 MB package storage on the free plan for private ones | prune old image tags from GHCR when storage grows |

Check current quotas on each provider's pricing page; they change.

## Local development

The same Postgres schema runs in Docker for development; see `DOCKER.md` and the
README's Development section. `scripts/apply-migrations.sh <url>` applies the
migrations to a fresh database.
