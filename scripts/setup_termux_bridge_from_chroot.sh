#!/usr/bin/env bash
# Wrapper: Setup aus Linux-Chroot an Termux delegieren.
set -euo pipefail

ISAAC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TERMUX_BASH="/data/data/com.termux/files/usr/bin/bash"

echo "Isaac: $ISAAC_DIR"

if [ ! -x "$TERMUX_BASH" ]; then
  cat <<EOF
Termux nicht gefunden unter $TERMUX_BASH

Bitte zuerst auf dem Handy:
  1) Termux installieren (F-Droid)
  2) Termux öffnen und einmal starten
  3) Dann in Termux:
       pkg install git
       git clone https://github.com/hansheute88/isaac.git ~/Isaac
       bash ~/Isaac/scripts/setup_termux_bridge.sh

Isaac im Chroot (/root/isaacnew) und Termux (~/Isaac) können getrennte Kopien sein —
wichtig ist nur, dass das Setup in Termux läuft.
EOF
  exit 1
fi

exec "$TERMUX_BASH" "$ISAAC_DIR/scripts/setup_termux_bridge.sh" "$ISAAC_DIR"