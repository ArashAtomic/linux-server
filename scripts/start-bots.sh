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
nohup cloudflared tunnel --url http://localhost:8080 > /tmp/cloudflared.log 2>&1 &
CF_PID=$!
echo "$CF_PID" > /tmp/cloudflared.pid

# Start sshx Web Terminal
echo
echo "==> Starting sshx Web Terminal"
nohup sshx > /tmp/sshx.log 2>&1 &
SSHX_PID=$!
echo "$SSHX_PID" > /tmp/sshx.pid

echo "Waiting for public tunnel URLs..."
for i in {1..15}; do
    if [ ! -f /tmp/cloudflared.url ]; then
        grep -o 'https://[-a-zA-Z0-0.]*\.trycloudflare\.com' /tmp/cloudflared.log | head -n 1 > /tmp/cloudflared.url || true
    fi
    if [ ! -f /tmp/sshx.url ]; then
        grep -o 'https://sshx\.io/s/[-a-zA-Z0-9#]*' /tmp/sshx.log | head -n 1 > /tmp/sshx.url || true
    fi
    if [ -s /tmp/cloudflared.url ] && [ -s /tmp/sshx.url ]; then
        break
    fi
    sleep 1
done

echo
echo "======================================"
echo "Processes and Tunnels Active"
echo "======================================"

if [ -f /tmp/cloudflared.url ]; then
    echo "Cloudflare Panel URL: $(cat /tmp/cloudflared.url)"
fi

if [ -f /tmp/sshx.url ]; then
    echo "sshx Terminal URL: $(cat /tmp/sshx.url)"
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
