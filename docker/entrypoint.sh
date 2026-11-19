#!/bin/sh
# Seed the config on first start and give mounted ssh keys the perms ssh wants.
set -eu

HERJEV_HOME="${HERJEV_HOME:-/data}"
HERJEV_CONFIG="${HERJEV_CONFIG:-$HERJEV_HOME/herjev.yaml}"
mkdir -p "$HERJEV_HOME"

if [ ! -f "$HERJEV_CONFIG" ]; then
    cp /opt/herjev/examples/herjev.docker.yaml "$HERJEV_CONFIG"
    echo "[herjev] seeded $HERJEV_CONFIG — edit sources/channels, then restart"
fi

# Keys mounted at /opt/herjev-ssh are often owned by another user or too
# open; copy them into the state dir with strict perms.
if [ -d /opt/herjev-ssh ] && [ -n "$(ls -A /opt/herjev-ssh 2>/dev/null)" ]; then
    mkdir -p "$HERJEV_HOME/ssh"
    cp -R /opt/herjev-ssh/. "$HERJEV_HOME/ssh/"
    chmod 700 "$HERJEV_HOME/ssh"
    find "$HERJEV_HOME/ssh" -type f ! -name '*.pub' ! -name known_hosts -exec chmod 600 {} +
fi

# `herjev status` (the healthcheck) reads this file; one left by the previous container
# would let a new one look healthy before it has run at all.
if [ "${1:-}" = herjev ] && [ "${2:-}" = run ]; then
    rm -f "$HERJEV_HOME/status.json"
fi

exec "$@"
