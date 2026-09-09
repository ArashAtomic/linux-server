#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="$HOME/bot-server"
BOT_DIR="$BASE_DIR/bots"
PANEL_DIR="$BASE_DIR/panel"
STATE_FILE="$BASE_DIR/state.json"
HERMES_HOME_DIR="$HOME/.hermes"
NINEROUTER_HOME_DIR="$HOME/.9router"

LOVE_WHISPERS_DIR="$BOT_DIR/love-whispers-bot"
PACKTOGETHER_DIR="$BOT_DIR/PackTogether"

echo "======================================"
echo "Starting bots, Panel, Cloudflare Tunnel, and private Tailscale SSH"
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

# Hermes Agent API and gateway
echo
echo "==> Starting Hermes Agent"
export PATH="$HOME/.local/bin:$HOME/.hermes/bin:$PATH"
export HERMES_HOME="$HERMES_HOME_DIR"
export API_SERVER_ENABLED="true"
export API_SERVER_HOST="127.0.0.1"
export API_SERVER_PORT="8642"
export API_SERVER_KEY="${HERMES_API_SERVER_KEY:-}"
export HERMES_API_SERVER_KEY="$API_SERVER_KEY"

if [ -z "$API_SERVER_KEY" ]; then
    echo "ERROR: HERMES_API_SERVER_KEY is not configured."
    exit 1
fi
if ! command -v hermes >/dev/null 2>&1; then
    echo "ERROR: Hermes Agent is not installed."
    exit 1
fi

nohup env HERMES_HOME="$HERMES_HOME" API_SERVER_ENABLED="$API_SERVER_ENABLED" \
    API_SERVER_HOST="$API_SERVER_HOST" API_SERVER_PORT="$API_SERVER_PORT" \
    API_SERVER_KEY="$API_SERVER_KEY" hermes gateway > /tmp/hermes.log 2>&1 &
HERMES_PID=$!
echo "$HERMES_PID" > /tmp/hermes.pid

echo "Waiting for Hermes API..."
HERMES_READY=false
for i in {1..30}; do
    if curl -fsS --max-time 2 http://127.0.0.1:8642/health >/dev/null 2>&1; then
        HERMES_READY=true
        break
    fi
    if ! kill -0 "$HERMES_PID" 2>/dev/null; then
        echo "ERROR: Hermes exited during startup."
        tail -n 40 /tmp/hermes.log || true
        exit 1
    fi
    sleep 1
done
if [ "$HERMES_READY" != "true" ]; then
    echo "ERROR: Hermes API did not become ready within 30 seconds."
    tail -n 40 /tmp/hermes.log || true
    exit 1
fi
echo "Hermes Agent PID: $HERMES_PID (API 127.0.0.1:8642)"

# 9Router OpenAI-compatible gateway
echo
echo "==> Starting 9Router"
if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: Docker is required to run 9Router."
    exit 1
fi
if ! docker info >/dev/null 2>&1 && ! sudo -n docker info >/dev/null 2>&1; then
    echo "ERROR: Docker daemon is not available for 9Router."
    exit 1
fi

DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
    DOCKER=(sudo -n docker)
fi

> /tmp/9router.log
"${DOCKER[@]}" pull decolua/9router:latest >> /tmp/9router.log 2>&1
"${DOCKER[@]}" rm -f 9router >> /tmp/9router.log 2>&1 || true
"${DOCKER[@]}" run -d --name 9router --restart unless-stopped \
    -p 127.0.0.1:20128:20128 \
    -v "$NINEROUTER_HOME_DIR:/app/data" \
    --env-file "$NINEROUTER_HOME_DIR/.env" \
    -e DATA_DIR=/app/data -e PORT=20128 -e HOSTNAME=0.0.0.0 \
    decolua/9router:latest >> /tmp/9router.log 2>&1

NINEROUTER_READY=false
for i in {1..30}; do
    HTTP_STATUS=$(curl -sS --max-time 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:20128/dashboard 2>/dev/null || true)
    if [[ "$HTTP_STATUS" =~ ^[234][0-9][0-9]$ ]]; then
        NINEROUTER_READY=true
        break
    fi
    sleep 1
done
if [ "$NINEROUTER_READY" != "true" ]; then
    echo "ERROR: 9Router did not become ready within 30 seconds."
    "${DOCKER[@]}" logs --tail 40 9router >> /tmp/9router.log 2>&1 || true
    echo "9Router diagnostics:"
    tail -n 60 /tmp/9router.log || true
    exit 1
fi
echo "9Router ready at http://127.0.0.1:20128 (dashboard /dashboard, API /v1)"
MODEL_STATUS=$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:20128/v1/models 2>/dev/null || true)
echo "9Router model endpoint HTTP status: ${MODEL_STATUS:-unavailable}"

# Keep the dashboard password aligned with the SSH/server password. The
# supported local reset clears only the stored dashboard hash; provider data
# and usage history remain in the persistent SQLite database.
NINEROUTER_RESET=$("${DOCKER[@]}" exec 9router node -e \
    "fetch('http://127.0.0.1:20128/api/auth/reset-password',{method:'POST'}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))" \
    >/dev/null 2>&1; echo $?)
if [ "$NINEROUTER_RESET" -eq 0 ]; then
    echo "9Router dashboard password reset to the configured server password."
else
    echo "WARNING: Could not reset the 9Router dashboard password from inside the container."
fi

# Setup OpenSSH Server
echo
echo "==> Configuring OpenSSH Server"
sudo sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config 2>/dev/null || true
sudo sed -i 's/PasswordAuthentication no/PasswordAuthentication yes/' /etc/ssh/sshd_config 2>/dev/null || true
sudo systemctl restart ssh || sudo service ssh restart || true

SSH_USER="${SERVER_USERNAME:-admin}"
SSH_PASS="${SERVER_PASSWORD:-admin}"

echo "==> Configuring SSH User: $SSH_USER"
sudo useradd -m -s /bin/bash "$SSH_USER" 2>/dev/null || true
echo "$SSH_USER:$SSH_PASS" | sudo chpasswd
sudo usermod -aG sudo "$SSH_USER" 2>/dev/null || true
echo "$SSH_USER ALL=(ALL) NOPASSWD:ALL" | sudo tee "/etc/sudoers.d/$SSH_USER" >/dev/null

# Management Panel
echo
echo "==> Starting Management Panel GUI"

cd "$PANEL_DIR"
source .venv/bin/activate

export SERVER_USERNAME="$SSH_USER"
export SERVER_PASSWORD="$SSH_PASS"
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
echo "==> Starting Cloudflare Tunnel for Web Panel"
rm -f /tmp/cloudflared.url /tmp/panel_url.txt /tmp/ssh_cmd.txt
nohup cloudflared tunnel --url http://localhost:8080 > /tmp/cloudflared.log 2>&1 &
CF_PID=$!
echo "$CF_PID" > /tmp/cloudflared.pid

echo "Waiting for Cloudflare Panel URL..."
for i in {1..15}; do
    if [ ! -s /tmp/cloudflared.url ]; then
        grep -o 'https://[-a-zA-Z0-9.]*\.trycloudflare\.com' /tmp/cloudflared.log | head -n 1 > /tmp/cloudflared.url || true
    fi
    if [ -s /tmp/cloudflared.url ]; then
        break
    fi
    sleep 1
done

if [ -s /tmp/cloudflared.url ]; then
    cp /tmp/cloudflared.url /tmp/panel_url.txt
else
    echo "WARNING: Cloudflare Tunnel URL was not discovered."
    echo "http://localhost:8080" > /tmp/panel_url.txt
fi

# Connect Tailscale for private SSH access over the tailnet
echo
echo "==> Connecting Tailscale"

if [ -n "${TAILSCALE_AUTHKEY:-}" ]; then
    sudo tailscale up --authkey="$TAILSCALE_AUTHKEY" --hostname="bot-server" --accept-routes || true
else
    echo "WARNING: TAILSCALE_AUTHKEY is not configured."
fi

# Remove Funnel state left by older deployments before enabling private SSH.
sudo systemctl disable --now tailscale-funnel.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/tailscale-funnel.service
sudo systemctl daemon-reload 2>/dev/null || true
sudo tailscale funnel off 2>/dev/null || true

# Verify Tailscale status
tailscale status || true

TS_DOMAIN=$(tailscale status --json 2>/dev/null | jq -r '.Self.DNSName // empty' | sed 's/\.$//' || true)
if [ -z "$TS_DOMAIN" ]; then
    TS_DOMAIN=$(tailscale status 2>/dev/null | grep -v '#' | awk 'NR==1 {print $2}' || true)
fi

echo "Tailscale Domain: ${TS_DOMAIN:-unknown}"

# SSH remains on port 22 and is reachable through Tailscale MagicDNS only.
TS_IP=$(tailscale ip -4 2>/dev/null | head -n 1 || true)
if [ -n "$TS_IP" ]; then
    printf 'ListenAddress %s\n' "$TS_IP" | sudo tee /etc/ssh/sshd_config.d/tailscale.conf >/dev/null
    if sudo sshd -t; then
        sudo systemctl restart ssh || sudo service ssh restart || true
        echo "SSH is listening on Tailscale address $TS_IP only."
    else
        echo "WARNING: Invalid SSH configuration; restoring unrestricted SSH listener."
        sudo rm -f /etc/ssh/sshd_config.d/tailscale.conf
        sudo systemctl restart ssh || sudo service ssh restart || true
    fi
else
    echo "WARNING: Tailscale IPv4 address unavailable; SSH listener was not restricted."
fi

echo
echo "==> Tailscale Status:"
tailscale status || true

# Construct private tailnet SSH command & Panel URL
if [ -n "$TS_DOMAIN" ]; then
    SSH_CMD="ssh $SSH_USER@$TS_DOMAIN"
    echo "$SSH_CMD" > /tmp/ssh_cmd.txt
else
    SSH_CMD="SSH unavailable: Tailscale is not connected"
    echo "$SSH_CMD" > /tmp/ssh_cmd.txt
fi

echo
echo "======================================"
echo "Processes, Cloudflare Tunnel, and private Tailscale SSH Active"
echo "======================================"

echo "Web Panel URL : $(cat /tmp/panel_url.txt)"
echo "SSH Command   : $(cat /tmp/ssh_cmd.txt)"

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
