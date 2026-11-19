# Deploy runbook

A step-by-step checklist for deploying herjev to production. Tick each box as you go.
It follows [deployment.md](deployment.md), which has the architecture, the full
secret tables, rotation, and free-tier limits. Use this file to do the work and that
one to look things up.

Production is made of three parts, and each part is deployed differently:

| Part | Where it runs | How it is deployed |
|------|---------------|--------------------|
| Database | Supabase Postgres, schema `herjev` | `migrate` job (`supabase db push`) |
| Portal | Cloudflare Worker `herjev` | `worker` job (`wrangler deploy`) |
| Watcher | Docker on a GCP e2-micro VM | `release` job moves the `:production` image tag; the VM pulls it itself every 2 minutes (`deploy/update-watcher.sh`) |

Every push to `main` runs `.github/workflows/deploy.yml` in this order:
`test → image + migrate → worker → release → verify`. A job runs only when the jobs
before it passed. Nothing connects to the VM to deploy, so CI needs no SSH key, no VM
address and no VPN.

Fill in this table as you go. Store every value in your password manager, never in
the repository.

| Value | Obtained in | Goes to |
|-------|-------------|---------|
| `<ref>` (Supabase project ref) | A2 | connection strings |
| `SUPABASE_DB_URL` | A2 | GitHub `production` env (secret) |
| `BACKUP_AGE_RECIPIENT` + `herjev-backup.key` | A3 | GitHub env + offline storage |
| watcher / portal DB passwords | B2 | Hyperdrive, VM `.env` |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | C1 | GitHub env (secrets) |
| Hyperdrive id | C2 | `dashboard/wrangler.jsonc` (committed) |
| `HERJEV_DASHBOARD_TOKEN` | C3 | Worker secret + GitHub env (secret) |
| `PORTAL_URL` | C4 | GitHub env (variable), `wrangler.jsonc` vars, VM `herjev.yaml` |
| `HERMES_HOST` | D4 | VM `.env` |
| `VM_NB_HOST` | E1 | Hermes (`--herjev-url`) |
| GHCR read token | E2 | VM only (`docker login`) |

---

## Phase 0: Prerequisites

- [ ] Admin access to the GitHub repository `duylongpro99/herjev` (Settings and Actions).
- [ ] Accounts: Supabase, Cloudflare, Google Cloud (with billing), NetBird Cloud.
- [ ] Local tools: `gcloud`, `node` 22 with `npx`, `age`, `openssl`, `netbird`, and
      `psql` (optional).
- [ ] API keys ready: `OPENROUTER_API_KEY`, the LLM provider (`LLM_BASE_URL`,
      `LLM_MODEL`, `LLM_API_KEY`), and `GRAFANA_VIEWER_TOKEN` for the Loki source.
- [ ] `main` is green locally:

  ```sh
  uv sync --all-extras --locked && uv run pytest -q
  (cd dashboard && npm ci && npm run typecheck && npm test)
  ```

---

## Phase A: Supabase and backup key

- [ ] **A1.** Create a Supabase project in region **us-west-1** and save the
      `postgres` password.
- [ ] **A2.** Settings → Database → Connection string → **Session pooler** (port 5432),
      with the password filled in. This is `SUPABASE_DB_URL`:
      `postgresql://postgres.<ref>:<password>@aws-0-us-west-1.pooler.supabase.com:5432/postgres`
- [ ] Settings → API → Exposed schemas: make sure `herjev` is **not** listed.
- [ ] **A3.** Create the backup key and keep the private key offline:

  ```sh
  age-keygen -o herjev-backup.key   # prints "Public key: age1..." → BACKUP_AGE_RECIPIENT
  ```

- [ ] **A4.** GitHub → Settings → Environments → New environment `production`. Add the
      secrets `SUPABASE_DB_URL` and `BACKUP_AGE_RECIPIENT`.

## Phase B: First migration and role passwords

- [ ] **B1.** Actions → **deploy** → Run workflow on `main`.
  - Expected: `test`, `image` and `migrate` pass. `migrate` logs
    `schema herjev does not exist yet; skipping the backup`.
  - Expected: `worker` **fails** because the Cloudflare secrets are not set yet, so
    `release` and `verify` are skipped. That is fine at this point.
- [ ] **B2.** In the Supabase SQL editor, set both role passwords (generate each one with
      `openssl rand -hex 24`, which avoids characters that need escaping in URLs):

  ```sql
  alter role herjev_watcher password '<watcher password>';
  alter role herjev_portal  password '<portal password>';
  ```

  The pooler user names are `herjev_watcher.<ref>` and `herjev_portal.<ref>`.

- [ ] Check that the watcher role can log in:
      `psql "postgresql://herjev_watcher.<ref>:<pw>@aws-0-us-west-1.pooler.supabase.com:5432/postgres" -c "select 1"`

## Phase C: Cloudflare portal

- [ ] **C1.** My Profile → API Tokens → create a token with *Account · Workers Scripts ·
      Edit* and *Account · Hyperdrive · Edit*. Add `CLOUDFLARE_API_TOKEN` and
      `CLOUDFLARE_ACCOUNT_ID` (from the Workers overview page) to the `production`
      environment.
- [ ] **C2.** Create the Hyperdrive config as `herjev_portal`, with caching off:

  ```sh
  cd dashboard
  npx wrangler login
  npx wrangler hyperdrive create herjev --caching-disabled \
    --connection-string "postgresql://herjev_portal.<ref>:<portal pw>@aws-0-us-west-1.pooler.supabase.com:5432/postgres"
  ```

  Replace the placeholder `"id": "00000000000000000000000000000000"` in
  `dashboard/wrangler.jsonc` with the printed id. **Do not push yet.** You will commit
  it in F1 so that one push deploys everything.
- [ ] **C3.** Create the portal login token and store it in **both** places:

  ```sh
  openssl rand -hex 32            # copy this
  npx wrangler secret put HERJEV_DASHBOARD_TOKEN
  ```

  Add the same value to the GitHub `production` environment as the secret
  `HERJEV_DASHBOARD_TOKEN`. The `verify` job uses it to read `/api/status`.
- [ ] **C4.** Cloudflare → Workers & Pages → your `*.workers.dev` subdomain. The portal
      URL will be `https://herjev.<subdomain>.workers.dev`. Add it to the GitHub
      `production` environment as the **variable** (not a secret) `PORTAL_URL`, with no
      trailing slash.

## Phase D: GCP VM and NetBird

- [ ] **D1.** Attach billing to the GCP project and create a **1 USD budget alert**.
- [ ] **D2.** Firewall: allow ssh only from IAP (for your admin access), and delete the
      default open rules:

  ```sh
  gcloud compute firewall-rules create herjev-iap-ssh --network=default \
    --direction=INGRESS --allow=tcp:22 --source-ranges=35.235.240.0/20
  gcloud compute firewall-rules list   # delete default-allow-ssh / -rdp / -icmp if present
  ```

- [ ] **D3.** Create the VM:

  ```sh
  gcloud compute instances create herjev-vm --zone=us-west1-b --machine-type=e2-micro \
    --image-family=debian-12 --image-project=debian-cloud \
    --boot-disk-size=30GB --boot-disk-type=pd-standard
  ```

- [ ] **D4.** On the machine that runs Hermes, install NetBird and join it
      (`netbird up`, then sign in), then run `netbird status -4` → `HERMES_HOST`.
- [ ] **D5.** In the NetBird dashboard (app.netbird.io), put your machine in group
      `hermes`. Then create the two policies from
      [deployment.md § 6](deployment.md#6-netbird) and **delete the `Default` policy**:
  - `hermes` → `herjev-vm`, TCP 8789
  - `herjev-vm` → `hermes`, TCP 8644
- [ ] **D6.** Setup Keys → create a **one-off** key auto-assigned to group `herjev-vm`.
      You will use it once, in E1. Do not store it.

## Phase E: Bootstrap the VM

- [ ] **E1.** Copy the bootstrap with the two files it installs, then run it over IAP.
      The command is safe to run again.

  ```sh
  gcloud compute scp deploy/vm-bootstrap.sh deploy/update-watcher.sh deploy/compose.prod.yml \
    herjev-vm:~ --zone=us-west1-b --tunnel-through-iap
  gcloud compute ssh herjev-vm --zone=us-west1-b --tunnel-through-iap -- \
    "sudo NB_SETUP_KEY=<one-off key from D6> bash vm-bootstrap.sh"
  ```

  The output includes `done: NetBird address 100.x.y.z`. That address is `VM_NB_HOST`.
  In the NetBird dashboard, check that peer `herjev-vm` is in group `herjev-vm`.

- [ ] **E2.** Let the VM pull the private image. On GitHub → Settings → Developer
      settings → Personal access tokens → **Tokens (classic)**, create a token with
      only the `read:packages` scope. GHCR does not accept fine-grained tokens. Give it
      a long expiry and note the date. Then, on the VM:

  ```sh
  gcloud compute ssh herjev-vm --zone=us-west1-b --tunnel-through-iap
  sudo docker login ghcr.io -u duylongpro99   # paste the token as the password
  ```

- [ ] **E3.** Still on the VM, write the config and the secrets file. Copy
      `herjev.yaml` from your machine first
      (`gcloud compute scp herjev.yaml herjev-vm:~ --zone=us-west1-b --tunnel-through-iap`):

  ```sh
  sudo install -m 640 -o 1000 -g 1000 ~/herjev.yaml /opt/herjev/data/herjev.yaml
  sudo -u '#1000' sh -c 'cat > /opt/herjev/data/.env'   # paste, then Ctrl-D
  ```

  Contents of `/opt/herjev/data/.env` for the first deploy:

  ```sh
  HERJEV_DATABASE_URL=postgresql://herjev_watcher.<ref>:<watcher pw>@aws-0-us-west-1.pooler.supabase.com:5432/postgres
  OPENROUTER_API_KEY=sk-or-v1-...
  LLM_BASE_URL=...
  LLM_MODEL=...
  LLM_API_KEY=...
  GRAFANA_VIEWER_TOKEN=...
  HERMES_HOST=<from D4>
  # Off until Phase G wires them; teams fails at startup without TEAMS_* set.
  TEAMS_ENABLED=false
  HERMES_ENABLED=false
  ```

  The repository's `herjev.yaml` enables the `teams` and `hermes` channels by default.
  The `teams` channel raises an error at startup when `TEAMS_APP_ID` is missing, so keep
  both channels off until Phase G.

  Also set `dashboard.public_url: <PORTAL_URL>` in `/opt/herjev/data/herjev.yaml`, so
  alerts link to tickets.

- [ ] **E4.** Check that the updater is scheduled:
      `systemctl list-timers herjev-update.timer`. Until the first release exists,
      `journalctl -u herjev-update` shows a failed pull of `:production`. That is
      expected.

## Phase F: Full deploy

- [ ] **F1.** Commit the Hyperdrive id and the portal URL, then merge. The merge
      triggers the deploy:

  ```sh
  git checkout -b chore/production-portal
  # dashboard/wrangler.jsonc: hyperdrive[0].id from C2, vars.HERJEV_PUBLIC_URL = PORTAL_URL
  git add dashboard/wrangler.jsonc && git commit -m "chore(dashboard): set the production Hyperdrive id and URL"
  # open a PR, let ci pass, merge into main
  ```

- [ ] Watch Actions → **deploy**. Every job must be green: `test`, `image`, `migrate`,
      `worker`, `release` and `verify`.
  - `verify` waits up to 15 minutes until the portal reports the watcher on the new
    commit. It usually passes within about 5 minutes.
  - If `verify` fails, the VM has already rolled back to the previous commit. On the
    first deploy there is nothing to roll back to. Read why **on the VM**, not in CI:
    `sudo journalctl -u herjev-update -n 50` and `sudo docker logs herjev`. After fixing
    the cause, let the VM try the same commit again: `sudo rm /opt/herjev/failed-tag`.
- [ ] **F2.** Open `PORTAL_URL` and log in with the token from C3.

## Phase G: Hermes and Teams (optional channels)

- [ ] **G1.** On the machine that runs Hermes:

  ```sh
  scripts/install-hermes-skill.sh --docker hermes --connect \
    --herjev-url http://<VM_NB_HOST>:8789/mcp --env-file ./herjev-connect.env
  cat ./herjev-connect.env
  ```

  Add `HERJEV_MCP_TOKEN=...` (from the file) and the printed `HERMES_WEBHOOK_TOKEN`
  to the VM's `/opt/herjev/data/.env`, and set `HERMES_ENABLED=true`. Then delete
  `herjev-connect.env`.
- [ ] **G2.** Teams: add `TEAMS_APP_ID`, `TEAMS_TENANT_ID` and `TEAMS_CHAT_ID` to the
      VM `.env` and set `TEAMS_ENABLED=true`. If the app is not registered yet, follow
      [teams.md](teams.md).
- [ ] **G3.** Recreate the watcher (see below), then sign in to Teams and test both
      channels:

  ```sh
  sudo docker exec -it herjev herjev teams login     # device code, sign in as the posting account
  sudo docker exec herjev herjev test-channel teams
  hermes mcp test herjev                             # on the Hermes machine
  ```

## Phase H: Verification

- [ ] Every job of the latest deploy run is green, `verify` included.
- [ ] `sudo docker ps` shows `herjev` as `(healthy)`, and `sudo docker logs herjev`
      contains **no** `incident store unavailable` line. The health check does not catch
      a broken database connection, so you must check the log.
- [ ] `curl -H "Authorization: Bearer <token>" $PORTAL_URL/api/status` shows
      `watcher.version` equal to the deployed commit and a recent `updated_at`.
- [ ] An error log line in Loki appears on the board within about a minute and reaches
      the enabled channels.
- [ ] Moving a ticket survives a page reload.
- [ ] From outside NetBird, `nmap -Pn <VM public IP>` shows **no open ports**.
- [ ] From the Hermes machine, `nc -zv <VM_NB_HOST> 8789` connects. From a NetBird peer
      outside group `hermes`, it is refused or times out. This proves the policy covers
      the port Docker publishes.
- [ ] Rollback drill: Run workflow with `image_tag` set to an older full commit sha (one
      built after this release flow existed). `verify` should pass on it. Then run it
      again with the current sha.
- [ ] After 24 hours, check the DB size, Hyperdrive queries, and `docker stats
      --no-stream` on the VM (see [deployment.md § 10](deployment.md#10-verify)).

---

## Routine deploys (after the first one)

1. Open a PR. `ci.yml` runs tests, typecheck and the image build.
2. Merge into `main`. `deploy.yml` backs up the DB, migrates, deploys the Worker, then
   releases the watcher. The VM picks it up within about 2 minutes.
3. Check that the run is green. `verify` already confirms that the watcher runs the new
   commit.

Two kinds of change do **not** go through CI:

- A config or secret change on the VM (`herjev.yaml` or `data/.env`): edit the file,
  then recreate the watcher (below).
- A change to `deploy/compose.prod.yml` or `deploy/update-watcher.sh`: copy both files
  with `vm-bootstrap.sh` and run E1 again.

### Recreate the watcher

```sh
gcloud compute ssh herjev-vm --zone=us-west1-b --tunnel-through-iap
cd /opt/herjev
tag="$(sudo docker inspect herjev --format '{{.Config.Image}}')"
sudo HERJEV_IMAGE_TAG="${tag##*:}" docker compose -f compose.prod.yml up -d --force-recreate --wait
```

## Rollback (quick reference)

| What broke | Do this |
|------------|---------|
| Portal | `cd dashboard && npx wrangler deployments list`, then `npx wrangler rollback <version-id> -m "<reason>"` |
| Watcher (CI works) | Actions → deploy → Run workflow, `image_tag` = the last good full sha |
| Watcher (CI broken) | From your machine, with a `write:packages` token: `docker buildx imagetools create -t ghcr.io/duylongpro99/herjev:production ghcr.io/duylongpro99/herjev:<sha>`. The VM follows within 2 minutes |
| Watcher (pin on the VM) | `sudo systemctl stop herjev-update.timer`, then `cd /opt/herjev && sudo HERJEV_IMAGE_TAG=<sha> docker compose -f compose.prod.yml up -d --wait`. Start the timer again once `:production` is fixed |
| Schema | Write a new forward migration that reverses the change. Migrations never go backwards |
| Lost data | Restore the encrypted `db-backup-<sha>` artifact (kept for 7 days); see [deployment.md § Restore a backup](deployment.md#restore-a-backup) |

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `worker` job: authentication or permission error | The token lacks *Hyperdrive: Edit*, or the workers.dev route is not enabled | Recreate the token with both permissions (C1) |
| `worker` job: Hyperdrive not found | The id in `wrangler.jsonc` is still the zero placeholder | Do C2 and F1 |
| `release` job: `image_tag must be a full commit sha` | A short sha was given | Use the full 40-character sha |
| `verify` fails, running version stays old | The VM cannot pull: GHCR token missing or expired, or the timer is not running | On the VM: `sudo journalctl -u herjev-update -n 20`; redo E2 if it says `unauthorized` |
| `verify` fails, `journalctl` says `not healthy; rolling back` | The new release does not start | `sudo docker logs herjev`, fix, merge again (or `sudo rm /opt/herjev/failed-tag` to retry the same commit) |
| `journalctl` says `production image has no commit label` | `image_tag` points at an image built before this release flow | Release a newer commit |
| `journalctl` says `waiting for data/.env and data/herjev.yaml` | E3 is not done | Do E3 |
| `verify` fails with an empty response | `PORTAL_URL` variable or `HERJEV_DASHBOARD_TOKEN` secret is wrong | Check C3 and C4 |
| Container restarts; logs mention `teams needs TEAMS_APP_ID` | Teams is enabled without its IDs | Set `TEAMS_ENABLED=false`, or complete G2 |
| `incident store unavailable` in logs | Wrong `HERJEV_DATABASE_URL`: user must be `herjev_watcher.<ref>`, port 5432 | Fix `.env`, then recreate the watcher |
| `teams` channel: `not signed in` / `token refresh failed` | No Teams token, or it expired | `sudo docker exec -it herjev herjev teams login` |
| Portal loads but shows no data | Hyperdrive password is wrong, or the role password was not set | Repeat B2, then `npx wrangler hyperdrive update <id> --origin-password '...'` |
| Hermes cannot reach `:8789` although the `hermes-mcp` policy exists | NetBird's firewall and Docker's port forwarding disagree about traffic to the published port | On the VM, check `sudo netbird status -d` (Hermes peer connected) and `sudo ss -ltnp \| grep 8789` (bound to the NetBird IP). Then test from the Hermes machine with `nc -zv <VM_NB_HOST> 8789` |
