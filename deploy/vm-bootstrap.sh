#!/usr/bin/env bash
# Prepare a fresh Debian/Ubuntu VM to run the herjev watcher. Safe to run again; run it
# again after changing compose.prod.yml or update-watcher.sh, which it installs.
#
#   gcloud compute scp deploy/vm-bootstrap.sh deploy/update-watcher.sh deploy/compose.prod.yml \
#     herjev-vm:~ --tunnel-through-iap
#   gcloud compute ssh herjev-vm --tunnel-through-iap -- 'sudo NB_SETUP_KEY=... bash vm-bootstrap.sh'
#
# NB_SETUP_KEY is a one-off NetBird setup key that auto-assigns the peer to group
# herjev-vm; it is only needed on the first run and is never written to disk.
# Nothing connects to the VM to deploy: herjev-update.timer pulls the :production image
# (update-watcher.sh). Nothing here opens a port on the public address; the GCP firewall
# should allow tcp:22 from the IAP range only, for admin access.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CONTAINER_UID=1000   # the herjev user in docker/Dockerfile
for f in update-watcher.sh compose.prod.yml; do
  [ -f "$HERE/$f" ] || { echo "copy $f next to this script first" >&2; exit 1; }
done

[ "$(id -u)" = 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
# shellcheck source=/dev/null
. /etc/os-release
export DEBIAN_FRONTEND=noninteractive

echo "== swap"
if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
  [ -f /swapfile ] || { fallocate -l 1G /swapfile; chmod 600 /swapfile; mkswap /swapfile; }
  swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

echo "== packages"
apt-get update -q
apt-get install -yq ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
if [ ! -f /etc/apt/sources.list.d/docker.list ]; then
  curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc]" \
    "https://download.docker.com/linux/$ID $VERSION_CODENAME stable" > /etc/apt/sources.list.d/docker.list
fi
if [ ! -f /etc/apt/sources.list.d/netbird.list ]; then
  curl -fsSL https://pkgs.netbird.io/debian/public.key | gpg --dearmor --yes -o /etc/apt/keyrings/netbird.gpg
  echo "deb [signed-by=/etc/apt/keyrings/netbird.gpg] https://pkgs.netbird.io/debian stable main" \
    > /etc/apt/sources.list.d/netbird.list
fi
apt-get update -q
apt-get install -yq docker-ce docker-ce-cli containerd.io docker-compose-plugin netbird
systemctl enable --now docker
# The package ships the CLI only; this registers and starts the netbird systemd unit.
systemctl cat netbird >/dev/null 2>&1 || netbird service install
systemctl enable --now netbird

echo "== netbird"
nb_ip() { netbird status -4 2>/dev/null | grep -Eo '^100\.[0-9.]+' || true; }
if [ -z "$(nb_ip)" ]; then
  : "${NB_SETUP_KEY:?the VM is not on NetBird yet; set NB_SETUP_KEY}"
  # NetBird's own SSH server stays off; admin access is IAP ssh.
  netbird up --setup-key "$NB_SETUP_KEY" --hostname herjev-vm --allow-server-ssh=false
  for _ in $(seq 30); do [ -n "$(nb_ip)" ] && break; sleep 1; done
fi
NB_IP="$(nb_ip)"
[ -n "$NB_IP" ] || { echo "netbird has no address; check: netbird status" >&2; exit 1; }

# compose publishes the MCP endpoint on the NetBird address; if Docker starts the container
# before netbird has it, the bind fails and the watcher stays down after a reboot.
install -d /etc/systemd/system/docker.service.d
rm -f /etc/systemd/system/docker.service.d/10-herjev-tailscale.conf
cat > /etc/systemd/system/docker.service.d/10-herjev-netbird.conf <<'CONF'
[Unit]
After=netbird.service
Wants=netbird.service

[Service]
ExecStartPre=/bin/sh -c 'for i in $(seq 90); do netbird status -4 2>/dev/null | grep -q "^100\\." && exit 0; sleep 1; done; exit 0'
CONF
systemctl daemon-reload

echo "== sshd (admin access through IAP only)"
cat > /etc/ssh/sshd_config.d/50-herjev.conf <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
CONF
sshd -t
systemctl reload ssh 2>/dev/null || systemctl reload sshd

echo "== /opt/herjev"
install -d -m 755 /opt/herjev
install -m 755 "$HERE/update-watcher.sh" /opt/herjev/update-watcher.sh
install -m 644 "$HERE/compose.prod.yml" /opt/herjev/compose.prod.yml
# Owned by the container's user; compose runs as root and reads data/.env itself.
install -d -m 750 -o "$CONTAINER_UID" -g "$CONTAINER_UID" /opt/herjev/data
# The watcher's secrets; the updater waits until this file is filled.
[ -f /opt/herjev/data/.env ] || install -m 640 -o "$CONTAINER_UID" -g "$CONTAINER_UID" /dev/null /opt/herjev/data/.env
chown "$CONTAINER_UID:$CONTAINER_UID" /opt/herjev/data/.env
chmod 640 /opt/herjev/data/.env
# Read by `docker compose` in /opt/herjev: publish the MCP endpoint on the NetBird address only.
echo "NETBIRD_IP=$NB_IP" > /opt/herjev/.env

echo "== updater"
cat > /etc/systemd/system/herjev-update.service <<'UNIT'
[Unit]
Description=Switch the herjev watcher to the :production image when it changes
After=docker.service netbird.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/opt/herjev/update-watcher.sh
UNIT
cat > /etc/systemd/system/herjev-update.timer <<'UNIT'
[Unit]
Description=Check for a new herjev release every 2 minutes

[Timer]
OnBootSec=1min
OnUnitInactiveSec=2min

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now herjev-update.timer

echo "done: NetBird address $NB_IP."
echo "Next: sudo docker login ghcr.io, then fill /opt/herjev/data/.env and herjev.yaml (docs/deployment.md)."
