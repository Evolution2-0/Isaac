#!/bin/bash
# Isaac — Android Remote-Admin Setup (Termux)
# Installiert Isaac mit Admin-Modus und optional SSH für Fernzugriff vom iPhone.
#
# Direkt in Termux:
#   curl -sL https://raw.githubusercontent.com/hansheute88/isaac/feature/phase-3-refine/android_remote_setup.sh | bash -s admin
#
# Oder lokal:
#   bash android_remote_setup.sh admin

set -euo pipefail

MODE="${1:-admin}"
OWNER="${ISAAC_OWNER:-Steffen}"
ENABLE_SSH="${ENABLE_SSH:-1}"
REPO_URL="${ISAAC_REPO_URL:-https://github.com/hansheute88/isaac.git}"
ISAAC_DIR="${ISAAC_DIR:-$HOME/Isaac}"

echo "╔══════════════════════════════════════════════════════╗"
echo "║  Isaac Android Remote-Admin Setup (Termux)          ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "Mode:       $MODE"
echo "Owner:      $OWNER"
echo "SSH remote: $ENABLE_SSH"
echo ""

if command -v pkg &>/dev/null; then
    PKG_CMD="pkg"
    echo "📦 Termux erkannt"
elif command -v apt &>/dev/null; then
    PKG_CMD="apt"
    echo "📦 apt-basiertes System erkannt"
else
    echo "❌ Weder pkg noch apt gefunden. Bitte in Termux ausführen."
    exit 1
fi

echo ""
echo "[1/7] Pakete installieren..."
if [ "$PKG_CMD" = "pkg" ]; then
    pkg update -y
    pkg install -y python git curl openssh termux-api
else
    apt update -qq
    apt install -y python3 git curl python3-venv openssh-client
fi

echo "[2/7] Isaac klonen oder aktualisieren..."
if [ ! -d "$ISAAC_DIR/.git" ]; then
    git clone "$REPO_URL" "$ISAAC_DIR"
else
    echo "  (Isaac existiert — git pull)"
    git -C "$ISAAC_DIR" pull --ff-only || true
fi
cd "$ISAAC_DIR"

echo "[3/7] Python-Umgebung..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -U pip
pip install -q -r requirements.txt

echo "[4/7] .env mit Admin-Modus..."
cat > .env <<ENVEOF
ISAAC_OWNER=$OWNER
ACTIVE_PROVIDER=groq
ISAAC_DISABLE_VECTOR_MEMORY=1
ISAAC_PRIVILEGE_MODE=$MODE
ISAAC_RUNTIME_ENV=termux
MONITOR_PORT=8765
DASHBOARD_PORT=8766
MONITOR_HTTP_PORT=8766
ENVEOF

if [ -n "${GROQ_API_KEY:-}" ]; then
    echo "GROQ_API_KEY=$GROQ_API_KEY" >> .env
fi

echo "[5/7] Termux-Berechtigungen..."
if command -v termux-setup-storage &>/dev/null; then
    termux-setup-storage || true
fi

echo "[6/7] Optional: SSH für Fernzugriff (iPhone → Android)..."
if [ "$ENABLE_SSH" = "1" ] && command -v sshd &>/dev/null; then
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    if [ ! -f "$HOME/.ssh/authorized_keys" ]; then
        touch "$HOME/.ssh/authorized_keys"
        chmod 600 "$HOME/.ssh/authorized_keys"
    fi
    if ! pgrep -x sshd >/dev/null 2>&1; then
        sshd || true
    fi
    SSH_PORT="$(grep -E '^Port ' "$PREFIX/etc/ssh/sshd_config" 2>/dev/null | awk '{print $2}')"
    SSH_PORT="${SSH_PORT:-8022}"
    echo "  SSH läuft auf Port $SSH_PORT (Termux-Standard: 8022)"
    echo "  Vom iPhone: Termius/Blink + Tailscale oder gleiches WLAN"
else
    echo "  SSH übersprungen (sshd nicht verfügbar oder ENABLE_SSH=0)"
fi

echo "[7/7] Autostart-Helfer..."
mkdir -p "$HOME/bin"
cat > "$HOME/bin/isaac-start" <<'STARTEOF'
#!/data/data/com.termux/files/usr/bin/bash
cd "$HOME/Isaac" || exit 1
export ISAAC_RUNTIME_ENV=termux
export ISAAC_DISABLE_VECTOR_MEMORY=1
[ -f .env ] && set -a && source .env && set +a
exec bash start_termux.sh "$(pwd)"
STARTEOF
chmod +x "$HOME/bin/isaac-start"

echo ""
echo "✅ Setup abgeschlossen"
echo ""
echo "Isaac starten:"
echo "  isaac-start"
echo ""
echo "Dashboard (lokal auf dem Android):"
echo "  http://127.0.0.1:8766"
echo ""
if [ "$MODE" = "admin" ]; then
    echo "🔓 Admin-Modus aktiv — Isaac hat volle lokale Rechte (ISAAC_PRIVILEGE_MODE=admin)"
else
    echo "🔒 User-Modus aktiv"
fi
echo ""
echo "Fernzugriff vom iPhone:"
echo "  1) Tailscale auf Android + iPhone installieren"
echo "  2) In Termux: sshd (falls nicht läuft)"
echo "  3) IP anzeigen: tailscale ip -4  (oder: ip route | awk '/wlan/ {print \$9; exit}')"
echo "  4) iPhone: SSH-App → user@<android-ip> Port 8022"
echo ""
echo "Hinweis: Ein E-Mail-Klick allein kann Android nicht fernsteuern."
echo "         Du musst Termux einmal öffnen und den Setup-Befehl ausführen."