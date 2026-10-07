#!/usr/bin/env bash
# Termux-Brücke für Isaac S8-Linux-Chroot einrichten.
# In Termux ausführen: bash /pfad/zu/isaac/scripts/setup_termux_bridge.sh
set -euo pipefail

ISAAC_DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
TERMUX_HOME="${HOME:-/data/data/com.termux/files/home}"
BRIDGE_DIR="$TERMUX_HOME/.isaac/bridge"
ISAAC_DATA="$ISAAC_DIR/data"
KEY_PATH="$ISAAC_DATA/termux_bridge_id"
PUB_PATH="$KEY_PATH.pub"

echo "=== Isaac Termux-Brücke Setup ==="
echo "Isaac: $ISAAC_DIR"

TERMUX_BASH="/data/data/com.termux/files/usr/bin/bash"
if ! command -v pkg >/dev/null 2>&1; then
  if [ -x "$TERMUX_BASH" ] && [ -z "${ISAAC_TERMUX_SETUP_REEXEC:-}" ]; then
    echo "Chroot erkannt — starte Setup in Termux …"
    export ISAAC_TERMUX_SETUP_REEXEC=1
    exec "$TERMUX_BASH" "$ISAAC_DIR/scripts/setup_termux_bridge.sh" "$ISAAC_DIR"
  fi
  cat >&2 <<'EOF'
Fehler: pkg nicht gefunden.

Dieses Skript muss in der Termux-App laufen (nicht im Linux-Chroot).

So geht's:
  1) Termux-App auf dem Handy öffnen
  2) Falls Isaac dort noch fehlt:
       pkg install git
       git clone https://github.com/hansheute88/isaac.git ~/Isaac
     Oder nur das Skript kopieren:
       mkdir -p ~/isaacnew/scripts
       cp /pfad/zum/setup_termux_bridge.sh ~/isaacnew/scripts/
  3) In Termux ausführen:
       cd ~/Isaac    # oder ~/isaacnew
       bash scripts/setup_termux_bridge.sh

Falls Termux noch nicht installiert ist: zuerst Termux aus F-Droid installieren.
EOF
  exit 1
fi

pkg update -y
pkg install -y termux-api openssh tsu
mkdir -p "$BRIDGE_DIR" "$ISAAC_DATA"
chmod 700 "$BRIDGE_DIR"

if ! command -v termux-screenshot >/dev/null 2>&1; then
  echo "Hinweis: termux-screenshot fehlt — Termux:API-App installieren und Berechtigungen erlauben."
fi

if ! pgrep -x sshd >/dev/null 2>&1; then
  sshd || true
fi

if [ ! -f "$KEY_PATH" ]; then
  ssh-keygen -t ed25519 -N "" -f "$KEY_PATH" -C "isaac-termux-bridge"
fi

AUTH_LINE="$(cat "$PUB_PATH")"
MARKER="# isaac-termux-bridge"
AUTH_FILE="$TERMUX_HOME/.ssh/authorized_keys"
mkdir -p "$TERMUX_HOME/.ssh"
chmod 700 "$TERMUX_HOME/.ssh"
touch "$AUTH_FILE"
chmod 600 "$AUTH_FILE"
if ! grep -Fq "$MARKER" "$AUTH_FILE" 2>/dev/null; then
  echo "$AUTH_LINE $MARKER" >> "$AUTH_FILE"
fi

TERMUX_UID="$(stat -c '%u' /data/data/com.termux 2>/dev/null || echo "")"
SSH_USER="${ISAAC_TERMUX_SSH_USER:-u0_a${TERMUX_UID:-0}}"

ENV_FILE="$ISAAC_DIR/.env.local"
touch "$ENV_FILE"
grep -q '^ISAAC_TERMUX_BRIDGE=' "$ENV_FILE" 2>/dev/null || echo "ISAAC_TERMUX_BRIDGE=auto" >> "$ENV_FILE"
grep -q '^ISAAC_TERMUX_SSH_HOST=' "$ENV_FILE" 2>/dev/null || echo "ISAAC_TERMUX_SSH_HOST=127.0.0.1" >> "$ENV_FILE"
grep -q '^ISAAC_TERMUX_SSH_PORT=' "$ENV_FILE" 2>/dev/null || echo "ISAAC_TERMUX_SSH_PORT=8022" >> "$ENV_FILE"
grep -q '^ISAAC_TERMUX_SSH_IDENTITY=' "$ENV_FILE" 2>/dev/null || echo "ISAAC_TERMUX_SSH_IDENTITY=$KEY_PATH" >> "$ENV_FILE"
if ! grep -q '^ISAAC_TERMUX_SSH_USER=' "$ENV_FILE" 2>/dev/null; then
  echo "ISAAC_TERMUX_SSH_USER=$SSH_USER" >> "$ENV_FILE"
else
  # keep user in sync with detected Termux uid
  sed -i "s/^ISAAC_TERMUX_SSH_USER=.*/ISAAC_TERMUX_SSH_USER=$SSH_USER/" "$ENV_FILE" 2>/dev/null || true
fi

# Shared storage so Kali-Chroot can pick up the private key (bind mounts differ)
if [ -d /sdcard ] || [ -d /storage/emulated/0 ]; then
  SD="${SDCARD:-/sdcard}"
  [ -d "$SD" ] || SD="/storage/emulated/0"
  if [ -d "$SD" ]; then
    cp -f "$KEY_PATH" "$SD/isaac_termux_bridge_id" 2>/dev/null || true
    cp -f "$PUB_PATH" "$SD/isaac_termux_bridge.pub" 2>/dev/null || true
    chmod 600 "$SD/isaac_termux_bridge_id" 2>/dev/null || true
    echo "  Shared key (für Chroot): $SD/isaac_termux_bridge_id"
  fi
fi

echo ""
echo "Setup fertig."
echo "  Bridge-Workdir: $BRIDGE_DIR"
echo "  SSH-Key:        $KEY_PATH"
echo "  SSH-User:       $SSH_USER (ggf. in .env.local anpassen)"
echo ""
echo "Falls Isaac im Kali-Chroot den Key nicht sieht, dort ausführen:"
echo "  cp /sdcard/isaac_termux_bridge_id ~/isaacnew/data/termux_bridge_id"
echo "  chmod 600 ~/isaacnew/data/termux_bridge_id"
echo ""
echo "Test in Isaac (Linux-Chroot):"
echo "  python3 -c \"from termux_bridge import diagnose_bridge; print(diagnose_bridge())\""
echo "  python3 -c \"import asyncio; from computer_use import get_computer_use, parse_agent_body; print(asyncio.run(get_computer_use().execute(parse_agent_body('diagnose'))))\""
echo ""
echo "Wichtig: Termux:API-App öffnen und Berechtigungen (Storage, Draw over, etc.) erlauben."