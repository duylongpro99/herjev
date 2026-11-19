#!/usr/bin/env bash
# Install the herjev on-call skill into a Hermes Agent instance.
#
# The skill reaches herjev through herjev's MCP endpoint (tools mcp__herjev__*),
# never through a shell. Run from the herjev instance, --connect also wires
# both directions: the MCP server entry and its token in Hermes, and the
# webhook route that posts herjev's alerts to a chat as they are.
#
#   scripts/install-hermes-skill.sh                          # local ~/.hermes
#   scripts/install-hermes-skill.sh --hermes-home ~/.hermes/profiles/oncall
#   scripts/install-hermes-skill.sh --connect --herjev-url http://127.0.0.1:8789/mcp \
#       --deliver slack --chat-id C0123456789
#   scripts/install-hermes-skill.sh --docker hermes --connect --herjev-url http://herjev:8789/mcp
set -euo pipefail

usage() {
  cat <<'USAGE'
usage: install-hermes-skill.sh [options]
  --hermes-home DIR   Hermes home on the target (default: $HERMES_HOME, else ~/.hermes there)
  --ssh HOST          install on a remote Hermes host over ssh
  --docker CONTAINER  install inside a running Hermes container
  --category NAME     Hermes skill category directory (default: devops)
  --print             print SKILL.md and exit

Connect a Hermes instance to this herjev instance (run where herjev runs):
  --connect           add the herjev MCP server to Hermes and create the webhook
                      route that posts herjev's alerts to a chat as they are
  --herjev-url URL    herjev's MCP endpoint as the Hermes instance reaches it
                      (required), e.g. http://127.0.0.1:8789/mcp
  --env-file PATH     herjev .env holding HERJEV_MCP_TOKEN; a token is generated
                      and written there when missing (default: <repo>/.env)
  --route NAME        Hermes webhook route and herjev channel name (default: herjev)
  --deliver TARGET    where Hermes posts the alerts: slack, telegram, discord, log ...
  --chat-id ID        chat or channel id for --deliver
  --hermes-url URL    Hermes webhook base URL as herjev reaches it (for the printed channel)
  --replace-route     recreate an existing route (new secret; the old file is backed up)
USAGE
}

REPO="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib-env.sh
. "$REPO/scripts/lib-env.sh"
SKILL_SRC="$REPO/integrations/hermes/herjev-oncall/SKILL.md"
hermes_home="" ssh_host="" container="" category="devops" print_only=0
connect=0 herjev_url="" env_file="$REPO/.env"
route="herjev" deliver="" chat_id="" hermes_url="" replace_route=0

while [ $# -gt 0 ]; do
  case "$1" in
    --hermes-home) hermes_home="${2:?--hermes-home needs a value}"; shift 2 ;;
    --ssh) ssh_host="${2:?--ssh needs a host}"; shift 2 ;;
    --docker) container="${2:?--docker needs a container}"; shift 2 ;;
    --category) category="${2:?--category needs a name}"; shift 2 ;;
    --print) print_only=1; shift ;;
    --connect) connect=1; shift ;;
    --herjev-url) herjev_url="${2:?--herjev-url needs a URL}"; shift 2 ;;
    --env-file) env_file="${2:?--env-file needs a path}"; shift 2 ;;
    --route) route="${2:?--route needs a name}"; shift 2 ;;
    --deliver) deliver="${2:?--deliver needs a target}"; shift 2 ;;
    --chat-id) chat_id="${2:?--chat-id needs an id}"; shift 2 ;;
    --hermes-url) hermes_url="${2:?--hermes-url needs a URL}"; shift 2 ;;
    --replace-route) replace_route=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -n "$ssh_host" ] && [ -n "$container" ]; then
  echo "choose one of --ssh or --docker" >&2; exit 2
fi
case "$category" in */*|.*|"") echo "invalid --category: $category" >&2; exit 2 ;; esac
if [ "$connect" = 1 ]; then
  [ -n "$herjev_url" ] || { echo "--connect needs --herjev-url" >&2; exit 2; }
  url_re='^https?://[][A-Za-z0-9._:/-]+$'
  [[ "$herjev_url" =~ $url_re ]] || { echo "invalid --herjev-url: $herjev_url" >&2; exit 2; }
  [[ "$route" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || { echo "invalid --route: $route" >&2; exit 2; }
fi

if [ "$print_only" = 1 ]; then cat "$SKILL_SRC"; exit 0; fi

# Single-quote a value for the target shell.
q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

# Run a POSIX sh script (on stdin) on the Hermes target.
on_target() {
  if [ -n "$ssh_host" ]; then ssh "$ssh_host" sh -s
  elif [ -n "$container" ]; then docker exec -i "$container" sh -s
  else sh -s
  fi
}

if [ -n "$ssh_host" ]; then where="$ssh_host"
elif [ -n "$container" ]; then where="container $container"
else where="this machine"
fi

# shellcheck disable=SC2016  # expanded by the target shell
home_expr='${HERMES_HOME:-$HOME/.hermes}'
[ -n "$hermes_home" ] && home_expr="$(q "$hermes_home")"
# Resolves the Hermes home on the target into $home.
home_prelude() {
  cat <<SH
set -e
home=$home_expr
if [ "\${home#\~}" != "\$home" ]; then home="\$HOME\${home#\~}"; fi
export HERMES_HOME="\$home"
SH
}

# 1. The skill; an existing different one is kept as a timestamped backup.
{ home_prelude; cat <<SH
dir="\$home/skills/$category/herjev-oncall"
mkdir -p "\$dir"
cat > "\$dir/SKILL.md.new" <<'HERJEV_SKILL_EOF'
SH
  cat "$SKILL_SRC"
  cat <<'SH'
HERJEV_SKILL_EOF
if [ -f "$dir/SKILL.md" ] && cmp -s "$dir/SKILL.md" "$dir/SKILL.md.new"; then
  rm "$dir/SKILL.md.new"; echo "unchanged $dir/SKILL.md"
else
  if [ -f "$dir/SKILL.md" ]; then
    cp "$dir/SKILL.md" "$dir/SKILL.md.bak-$(date +%Y%m%d-%H%M%S)"
  fi
  mv "$dir/SKILL.md.new" "$dir/SKILL.md"; echo "installed $dir/SKILL.md"
fi
SH
} | on_target

if [ "$connect" = 0 ]; then
  cat <<MSG
The skill on $where calls herjev's MCP tools. Next, from the herjev instance:
  scripts/install-hermes-skill.sh --connect --herjev-url http://<herjev-host>:8789/mcp ...
MSG
  exit 0
fi

# 2. The bearer token herjev's MCP endpoint expects, shared with Hermes.
token="$(env_get HERJEV_MCP_TOKEN "$env_file")"
if [ -z "$token" ]; then
  token="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  env_set "$env_file" HERJEV_MCP_TOKEN "$token"
  echo "generated HERJEV_MCP_TOKEN in $env_file"
fi

# 3. Hermes -> herjev: the MCP server entry; the token stays in the Hermes .env.
# shellcheck disable=SC2016  # ${HERJEV_MCP_TOKEN} is resolved by Hermes
{ home_prelude; cat "$REPO/scripts/lib-env.sh"; cat <<SH
command -v hermes >/dev/null || { echo "no hermes command on the Hermes instance" >&2; exit 1; }
command -v python3 >/dev/null || { echo "the Hermes instance needs python3" >&2; exit 1; }
env_set "\$home/.env" HERJEV_MCP_TOKEN $(q "$token")
hermes config set mcp_servers.herjev.url $(q "$herjev_url") >/dev/null
hermes config set mcp_servers.herjev.headers.Authorization 'Bearer \${HERJEV_MCP_TOKEN}' >/dev/null
echo "added MCP server herjev ($herjev_url) to \$home/config.yaml"
SH
} | on_target

status=0
set +e
test_out="$( { home_prelude; echo "hermes mcp test herjev 2>&1"; } | on_target )"
test_rc=$?
set -e
if [ "$test_rc" = 0 ]; then
  echo "the Hermes instance reached herjev's MCP endpoint"
else
  status=1
  cat >&2 <<MSG
warning: the Hermes instance could not reach herjev's MCP endpoint at $herjev_url:
$test_out
  Is \`herjev run\` running with mcp_server.enabled and the same HERJEV_MCP_TOKEN, bound to an address the Hermes instance reaches?
MSG
fi

# 4. herjev -> Hermes: the webhook route that posts each alert to a chat.
# `hermes webhook subscribe` overwrites a route of the same name, so an
# existing one is kept unless --replace-route.
secret=""
route_out="$( { home_prelude; cat <<SH
subs="\$home/webhook_subscriptions.json"
if [ -f "\$subs" ] && grep -q $(q "\"$route\": {") "\$subs"; then
  if [ $replace_route = 0 ]; then echo "EXISTS"; exit 0; fi
  cp "\$subs" "\$subs.bak-\$(date +%Y%m%d-%H%M%S)"
fi
hermes webhook subscribe $(q "$route") --skills herjev-oncall --description 'Incidents from herjev' \
  --prompt '{text}' \
  ${deliver:+--deliver $(q "$deliver")} ${chat_id:+--deliver-chat-id $(q "$chat_id")}
SH
  } | on_target )" || { echo "could not create the webhook route: $route_out" >&2; exit 1; }

if [ "$route_out" = EXISTS ]; then
  echo "kept the existing Hermes route '$route' (use --replace-route to recreate it with a new secret)"
elif secret="$(printf '%s\n' "$route_out" | sed -n 's/^ *Secret: //p')"; [ -n "$secret" ]; then
  echo "created Hermes route '$route' (deliver: ${deliver:-log})"
  if [ -z "$deliver" ] || [ "$deliver" = log ]; then
    echo "note: alerts only go to Hermes' log; pass --deliver and --chat-id to post them to a chat"
  fi
else
  echo "could not create the webhook route:" >&2; printf '%s\n' "$route_out" >&2; exit 1
fi

# 5. The route posts herjev's text as it is, without an agent run
# (deliver_only), so every channel shows the same message. Without a chat to
# post to, the agent runs, limited to the herjev MCP tools (Hermes allows this
# per route, by editing the subscriptions file only): alert text comes from
# production logs, so the route never gets a terminal.
{ home_prelude; cat <<SH
subs="\$home/webhook_subscriptions.json"
python3 - "\$subs" $(q "$route") <<'PY'
import json, os, sys
path, route = sys.argv[1:]
subs = json.load(open(path))
r = dict(subs[route], prompt="{text}", toolsets=["mcp-herjev"])
if r.get("deliver", "log") != "log":
    # Hermes prepends a route's skills to the rendered text even when it only
    # delivers it, so a deliver_only route carries none.
    r["deliver_only"] = True
    r.pop("skills", None)
if r != subs[route]:
    subs[route] = r
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(subs, f, indent=2)
    os.chmod(tmp, os.stat(path).st_mode & 0o777)
    os.replace(tmp, path)
print(f"route '{route}' " + ("posts herjev's alert text as it is" if r.get("deliver_only")
                             else "runs the skill with toolsets [mcp-herjev] only"))
PY
SH
} | on_target

if [ -z "$hermes_url" ]; then
  if [ -n "$container" ] && published="$(docker port "$container" 8644/tcp 2>/dev/null | head -1)" && [ -n "$published" ]; then
    hermes_url="http://${published/0.0.0.0/127.0.0.1}"
  elif [ -n "$ssh_host" ]; then
    hermes_url="http://${ssh_host#*@}:8644"
  elif [ -z "$container" ]; then
    hermes_url="http://127.0.0.1:8644"
  else
    hermes_url="http://<hermes-address>:8644"
  fi
fi
token_var="$(printf '%s' "$route" | tr 'a-z-' 'A-Z_')_WEBHOOK_TOKEN"
token_value="$secret"
[ -n "$token_value" ] || token_value="<the secret of route $route in the Hermes webhook_subscriptions.json>"

cat <<MSG

Finish on this herjev instance:
  1. herjev.yaml: serve the MCP endpoint and send alerts to the route:
       mcp_server:
         enabled: true
         token: \${HERJEV_MCP_TOKEN}
       channels:
         $route:
           type: webhook
           url: $hermes_url/webhooks/$route
           headers: {X-Gitlab-Token: "\${$token_var}"}
  2. .env ($env_file): $token_var=$token_value
  3. Restart \`herjev run\` and the Hermes gateway (hermes gateway restart),
     then: herjev test-channel $route
MSG
exit "$status"
