#!/usr/bin/env bash
# Let herjev post alerts to a Microsoft Teams group chat as a Teams user
# (Microsoft Graph, delegated permissions; no bot, no admin needed).
#
# It signs the Teams account in with a device code (herjev teams login), lets
# you pick the group chat and writes TEAMS_APP_ID, TEAMS_TENANT_ID and
# TEAMS_CHAT_ID to herjev's .env. The token lives in $HERJEV_HOME/teams_token.json;
# --relogin switches account.
#
#   scripts/setup-teams.sh                                 # IDs from ./.env, pick a chat
#   scripts/setup-teams.sh --chat "ops-alerts" --test
#   HERJEV="docker exec -it herjev herjev" scripts/setup-teams.sh --env-file ./herjev-data/.env
set -euo pipefail

usage() {
  cat <<'USAGE'
usage: setup-teams.sh [options]
  --env-file PATH     herjev .env holding TEAMS_APP_ID / TEAMS_TENANT_ID (default: <repo>/.env)
  --app-id ID         Entra app registration (client) ID (default: TEAMS_APP_ID from --env-file)
  --tenant-id ID      Entra directory (tenant) ID (default: TEAMS_TENANT_ID from --env-file)
  --chat-id ID        group chat to post to (default: pick from a list)
  --chat NAME         group chat to post to, by its exact topic
  --relogin           sign in again, e.g. with another Teams account
  --test              send a test alert through the teams channel (needs it in herjev.yaml)

The herjev command defaults to `herjev` on PATH, else `uv run herjev` in this
checkout; set HERJEV to override (e.g. "docker exec -it herjev herjev").

One-time app registration (any tenant member, if app registration is allowed):
  entra.microsoft.com → App registrations → New registration (single tenant,
  no redirect URI) → Authentication: Allow public client flows = Yes →
  API permissions → Microsoft Graph → Delegated: ChatMessage.Send,
  Chat.ReadBasic, User.Read. Put its IDs in .env as TEAMS_APP_ID and
  TEAMS_TENANT_ID.
USAGE
}

REPO="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib-env.sh
. "$REPO/scripts/lib-env.sh"
env_file="$REPO/.env" app_id="" tenant_id="" chat_id="" chat_name="" relogin=0 test_msg=0

while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) env_file="${2:?--env-file needs a path}"; shift 2 ;;
    --app-id) app_id="${2:?--app-id needs a value}"; shift 2 ;;
    --tenant-id) tenant_id="${2:?--tenant-id needs a value}"; shift 2 ;;
    --chat-id) chat_id="${2:?--chat-id needs a value}"; shift 2 ;;
    --chat) chat_name="${2:?--chat needs a name}"; shift 2 ;;
    --relogin) relogin=1; shift ;;
    --test) test_msg=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -n "${HERJEV:-}" ]; then read -r -a herjev <<< "$HERJEV"
elif command -v herjev >/dev/null; then herjev=(herjev)
else herjev=(uv run --project "$REPO" herjev)
fi

[ -n "$app_id" ] || app_id="$(env_get TEAMS_APP_ID "$env_file")"
[ -n "$tenant_id" ] || tenant_id="$(env_get TEAMS_TENANT_ID "$env_file")"
if [ -z "$app_id" ] || [ -z "$tenant_id" ]; then
  echo "missing TEAMS_APP_ID / TEAMS_TENANT_ID (in $env_file, or --app-id/--tenant-id)" >&2
  echo >&2; usage >&2; exit 2
fi
ids=(--app-id "$app_id" --tenant-id "$tenant_id")

# 1. Sign in (device code: open the URL on any device, sign in as the Teams account).
if [ "$relogin" = 1 ] || ! me="$("${herjev[@]}" teams whoami "${ids[@]}" 2>/dev/null)"; then
  "${herjev[@]}" teams login "${ids[@]}"
  me="$("${herjev[@]}" teams whoami "${ids[@]}")"
fi
echo "Teams account: $me"

# 2. Pick the group chat.
if [ -z "$chat_id" ]; then
  chats="$("${herjev[@]}" teams chats "${ids[@]}")"
  [ -n "$chats" ] || { echo "this account has no group chats" >&2; exit 1; }
  if [ -n "$chat_name" ]; then
    matches="$(printf '%s\n' "$chats" | awk -F'\t' -v n="$chat_name" '$2 == n')"
    count="$(printf '%s' "$matches" | grep -c . || true)"
    [ "$count" = 1 ] || { echo "found $count group chats named \"$chat_name\"; use --chat-id" >&2; exit 1; }
    chat_id="$(printf '%s\n' "$matches" | cut -f1)"
  else
    [ -t 0 ] || { echo "no terminal to pick a chat; pass --chat-id or --chat" >&2; exit 2; }
    printf '%s\n' "$chats" | awk -F'\t' '{ m = $3; if (length(m) > 70) m = substr(m, 1, 67) "..."; printf "%3d) %s  [%s]\n", NR, $2, m }'
    total="$(printf '%s\n' "$chats" | wc -l | tr -d ' ')"
    read -r -p "Group chat number (1-$total): " pick
    [[ "$pick" =~ ^[0-9]+$ ]] && [ "$pick" -ge 1 ] && [ "$pick" -le "$total" ] || { echo "invalid choice" >&2; exit 2; }
    chat_id="$(printf '%s\n' "$chats" | sed -n "${pick}p" | cut -f1)"
  fi
fi
case "$chat_id" in 19:*@thread.*) ;; *) echo "unexpected chat id: $chat_id" >&2; exit 1 ;; esac

# 3. Save the settings where herjev reads them.
env_set "$env_file" TEAMS_APP_ID "$app_id"
env_set "$env_file" TEAMS_TENANT_ID "$tenant_id"
env_set "$env_file" TEAMS_CHAT_ID "$chat_id"
echo "wrote TEAMS_APP_ID, TEAMS_TENANT_ID, TEAMS_CHAT_ID to $env_file"

if [ "$test_msg" = 1 ]; then
  "${herjev[@]}" test-channel teams
fi

cat <<'MSG'

Add the channel to herjev.yaml (and to a route's channels if you use routes),
then restart `herjev run`:
  channels:
    teams: {type: teams}
MSG
