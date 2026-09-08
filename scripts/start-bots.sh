#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="$HOME/bot-server"
BOT_DIR="$BASE_DIR/bots"
PANEL_DIR="$BASE_DIR/panel"
STATE_FILE="$BASE_DIR/state.json"

LOVE_WHISPERS_DIR="$BOT_DIR/love-whispers-bot"
PACKTOGETHER_DIR="$BOT_DIR/PackTogether"

echo "======================================"
echo "Starting bots, Panel, and Tunnels"
echo "======================================"

# Read persistent bot enabled flags
LOVE_ENABLED=true
PACK_ENABLED=true

if [ -f "$STATE_FILE" ]; then
    if command -v jq >/dev/null 2>&1; then
        LOVE_ENABLED=$(jq -r '."love-whispers" // true' "$STATE_FILE")
        PACK_ENABLED=$(jq -r '."packtogether" // true' "$STATE_FILE")
    fi
fi

# Love Whispers
echo
if [ "$LOVE_ENABLED" = "true" ]; then
    echo "==> Starting Love Whispers (State: Enabled)"
    cd "$LOVE_WHISPERS_DIR"
    source .venv/bin/activate
    nohup python -u bot.py > /tmp/love-whispers.log 2>&1 &
    LOVE_PID=$!
    echo "$LOVE_PID" > /tmp/love-whispers.pid
    deactivate
    echo "Love Whispers PID: $LOVE_PID"
else
    echo "==> Love Whispers is DISABLED in saved state (Skipping startup)"
    rm -f /tmp/love-whispers.pid
fi

# PackTogether
echo
if [ "$PACK_ENABLED" = "true" ]; then
    echo "==> Starting PackTogether (State: Enabled)"
    cd "$PACKTOGETHER_DIR"
    source .venv/bin/activate
    nohup python -u packtogether/bot.py > /tmp/packtogether.log 2>&1 &
    PACK_PID=$!
    echo "$PACK_PID" > /tmp/packtogether.pid
    deactivate
    echo "PackTogether PID: $PACK_PID"
else
    echo "==> PackTogether is DISABLED in saved state (Skipping startup)"
    rm -f /tmp/packtogether.pid
fi

# Management Panel
echo
echo "==> Starting Management Panel GUI"

cd "$PANEL_DIR"
source .venv/bin/activate

export SERVER_USERNAME="${SERVER_USERNAME:-admin}"
export SERVER_PASSWORD="${SERVER_PASSWORD:-admin}"
export GH_PAT="${GH_PAT:-}"
export STATUS_BOT_TOKEN="${STATUS_BOT_TOKEN:-}"
export STATUS_CHAT_ID="${STATUS_CHAT_ID:-}"
export GITHUB_REPO="${GITHUB_REPO:-ArashAtomic/linux-server}"
export GITHUB_REF_NAME="${GITHUB_REF_NAME:-main}"

nohup python -u app.py > /tmp/panel.log 2>&1 &
PANEL_PID=$!
echo "$PANEL_PID" > /tmp/panel.pid
deactivate

echo "Management Panel PID: $PANEL_PID (Port 8080)"

# Start Cloudflare Tunnel for Web Panel
echo
echo "==> Starting Cloudflare Tunnel"
nohup cloudflared tunnel --url http://127.0.0.1:8080 --no-autoupdate > /tmp/cloudflared.log 2>&1 &
CF_PID=$!
echo "$CF_PID" > /tmp/cloudflared.pid

# Ensure SSH server is running (tmate tunnels into local sshd)
echo
echo "==> Configuring OpenSSH Server"
sudo sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config 2>/dev/null || true
sudo sed -i 's/PasswordAuthentication no/PasswordAuthentication yes/' /etc/ssh/sshd_config 2>/dev/null || true
sudo systemctl restart ssh || sudo service ssh restart || true

# Start tmate SSH Terminal (uses official public tmate servers, self-hosted fallback)
echo
echo "==> Starting tmate SSH Session"
rm -f /tmp/tmate.sock /tmp/ssh_cmd.txt /tmp/tmate.log /tmp/tmate_stderr.log

# tmate reads server config from ~/.tmate.conf; do NOT pin a broken host -
# default is tmate.io which auto-negotiates via SSH_FQDN. Try default first.
nohup tmate -S /tmp/tmate.sock -F > /tmp/tmate.log 2>&1 &
TM_PID=$!
echo "$TM_PID" > /tmp/tmate.pid

echo "Waiting for public tunnel endpoints..."
for i in {1..40}; do
    if [ ! -s /tmp/cloudflared.url ]; then
        if [ -f /tmp/cloudflared.log ]; then
            grep -oiE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' /tmp/cloudflared.log | head -n 1 > /tmp/cloudflared.url || true
        fi
    fi

    if [ ! -s /tmp/ssh_cmd.txt ] && [ -S /tmp/tmate.sock ]; then
        TM_CMD=$(tmate -S /tmp/tmate.sock display -p '#{tmate_ssh}' 2>/dev/null || true)
        if [[ "$TM_CMD" == ssh* ]]; then
            echo "$TM_CMD" > /tmp/ssh_cmd.txt
        fi
    fi

    if [ -s /tmp/cloudflared.url ] && [ -s /tmp/ssh_cmd.txt ]; then
        break
    fi
    sleep 1
done

echo
echo "======================================"
echo "Processes and Tunnels Active"
echo "======================================"

if [ -s /tmp/cloudflared.url ]; then
    echo "Cloudflare Panel URL: $(cat /tmp/cloudflared.url)"
else
    echo "WARNING: Cloudflare URL not ready. Log:"
    tail -n 10 /tmp/cloudflared.log || true
fi

if [ -s /tmp/ssh_cmd.txt ]; then
    echo "SSH Command: $(cat /tmp/ssh_cmd.txt)"
else
    echo "WARNING: tmate SSH command not ready. Log:"
    tail -n 20 /tmp/tmate.log 2>/dev/null || true
fi

sleep 2

if [ "$LOVE_ENABLED" = "true" ] && [ -n "${LOVE_PID:-}" ]; then
    echo
    echo "Love Whispers:"
    ps -p "$LOVE_PID" -o pid,etime,cmd || true
fi

if [ "$PACK_ENABLED" = "true" ] && [ -n "${PACK_PID:-}" ]; then
    echo
    echo "PackTogether:"
    ps -p "$PACK_PID" -o pid,etime,cmd || true
fi

echo
echo "Management Panel:"
ps -p "$PANEL_PID" -o pid,etime,cmd || true
