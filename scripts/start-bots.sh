#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="$HOME/bot-server"
BOT_DIR="$BASE_DIR/bots"
PANEL_DIR="$BASE_DIR/panel"

LOVE_WHISPERS_DIR="$BOT_DIR/love-whispers-bot"
PACKTOGETHER_DIR="$BOT_DIR/PackTogether"

echo "======================================"
echo "Starting bots and Management Panel"
echo "======================================"

# Love Whispers
echo
echo "==> Starting Love Whispers"

cd "$LOVE_WHISPERS_DIR"
source .venv/bin/activate

nohup python -u bot.py \
    > /tmp/love-whispers.log 2>&1 &

LOVE_PID=$!
echo "$LOVE_PID" > /tmp/love-whispers.pid

deactivate

echo "Love Whispers PID: $LOVE_PID"

# PackTogether
echo
echo "==> Starting PackTogether"

cd "$PACKTOGETHER_DIR"
source .venv/bin/activate

nohup python -u packtogether/bot.py \
    > /tmp/packtogether.log 2>&1 &

PACK_PID=$!
echo "$PACK_PID" > /tmp/packtogether.pid

deactivate

echo "PackTogether PID: $PACK_PID"

# Management Panel
echo
echo "==> Starting Management Panel GUI"

cd "$PANEL_DIR"
source .venv/bin/activate

export SSH_USER="${SSH_USER:-admin}"
export GH_PAT="${GH_PAT:-}"
export STATUS_BOT_TOKEN="${STATUS_BOT_TOKEN:-}"
export STATUS_CHAT_ID="${STATUS_CHAT_ID:-}"
export GITHUB_REPO="${GITHUB_REPO:-ArashAtomic/linux-server}"
export GITHUB_REF_NAME="${GITHUB_REF_NAME:-main}"

nohup python -u app.py \
    > /tmp/panel.log 2>&1 &

PANEL_PID=$!
echo "$PANEL_PID" > /tmp/panel.pid

deactivate

echo "Management Panel PID: $PANEL_PID (Port 8080)"

echo
echo "======================================"
echo "Processes started"
echo "======================================"

sleep 3

echo
echo "Love Whispers:"
ps -p "$LOVE_PID" -o pid,etime,cmd || true

echo
echo "PackTogether:"
ps -p "$PACK_PID" -o pid,etime,cmd || true

echo
echo "Management Panel:"
ps -p "$PANEL_PID" -o pid,etime,cmd || true
