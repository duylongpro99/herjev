#!/usr/bin/env bash
# Run on the VM by herjev-update.timer every 2 minutes: if ghcr.io/...:production points at
# a new commit, switch the watcher to it and wait until it is healthy; if it is not within
# 3 minutes, go back to the commit that was running and skip the bad one from then on.
# Nothing connects to the VM to deploy: CI only moves the :production tag.
set -euo pipefail

IMAGE=ghcr.io/duylongpro99/herjev
cd /opt/herjev
# Until the secrets are filled in, there is nothing to start.
[ -s data/.env ] && [ -s data/herjev.yaml ] || { echo "waiting for data/.env and data/herjev.yaml"; exit 0; }

docker pull -q "$IMAGE:production" >/dev/null
want="$(docker image inspect "$IMAGE:production" \
  --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
[[ "$want" =~ ^[0-9a-f]{7,40}$ ]] || { echo "production image has no commit label: '$want'" >&2; exit 1; }

current="$(docker inspect herjev --format '{{.Config.Image}}' 2>/dev/null || true)"
current="${current##*:}"
# Already on it: a crashed container is restarted by Docker (restart: unless-stopped).
[ "$want" = "$current" ] && exit 0
# A commit that failed its health check is not retried; `rm /opt/herjev/failed-tag` to retry.
[ "$want" = "$(cat failed-tag 2>/dev/null || true)" ] && exit 0

up() { HERJEV_IMAGE_TAG="$1" docker compose -f compose.prod.yml up -d --pull missing --wait --wait-timeout 180; }

echo "deploying $want (was: ${current:-nothing})"
if up "$want"; then
  rm -f failed-tag
  docker image prune -f >/dev/null
  echo "healthy on $want"
  exit 0
fi

echo "$want" > failed-tag
docker inspect herjev --format '{{json .State.Health}}' || true
if [ -n "$current" ] && [ "$current" != "$want" ]; then
  echo "not healthy; rolling back to $current" >&2
  up "$current" || echo "rollback to $current is not healthy either" >&2
fi
exit 1
