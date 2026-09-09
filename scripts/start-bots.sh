#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="$HOME/bot-server"
BOT_DIR="$BASE_DIR/bots"
PANEL_DIR="$BASE_DIR/panel"
STATE_FILE="$BASE_DIR/state.json"

LOVE_WHISPERS_DIR="$BOT_DIR/love-whispers-bot"
PACKTOGETHER_DIR="$BOT_DIR/PackTogether"

echo "======================================"
echo "Starting bots, Panel, Cloudflare Tunnel, and Tailscale Funnel"
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

# Connect Tailscale and configure Tailscale Funnel for SSH
echo
echo "==> Connecting Tailscale"

if [ -n "${TAILSCALE_AUTHKEY:-}" ]; then
    sudo tailscale up --authkey="$TAILSCALE_AUTHKEY" --hostname="bot-server" --accept-routes || true
else
    echo "WARNING: TAILSCALE_AUTHKEY is not configured."
fi

# Verify Tailscale status
tailscale status || true

TS_DOMAIN=$(tailscale status --json 2>/dev/null | jq -r '.Self.DNSName // empty' | sed 's/\.$//' || true)
if [ -z "$TS_DOMAIN" ]; then
    TS_DOMAIN=$(tailscale status 2>/dev/null | grep -v '#' | awk 'NR==1 {print $2}' || true)
fi

echo "Tailscale Domain: ${TS_DOMAIN:-unknown}"

# Configure Tailscale Funnel for SSH (Port 443 preferred, fallback 8443, 10000)
FUNNEL_PORT=""
setup_ssh_funnel() {
    local target_port="$1"
    echo "Trying Tailscale Funnel on port $target_port -> localhost:22..."
    
    # Try background funnel command
    if sudo tailscale funnel --bg "$target_port" tcp://localhost:22 2>/dev/null; then
        return 0
    fi

    # Try serve tcp + funnel on
    if sudo tailscale serve --bg --tcp "$target_port" tcp://localhost:22 2>/dev/null || \
       sudo tailscale serve --bg "$target_port" tcp://localhost:22 2>/dev/null; then
        sudo tailscale funnel "$target_port" on 2>/dev/null || true
        return 0
    fi

    # Try foreground funnel in background
    if nohup sudo tailscale funnel "$target_port" tcp://localhost:22 > /tmp/funnel_ssh.log 2>&1 & then
        sleep 2
        return 0
    fi

    return 1
}

for PORT in 443 8443 10000; do
    if setup_ssh_funnel "$PORT"; then
        FUNNEL_PORT="$PORT"
        echo "SUCCESS: Tailscale Funnel active for SSH on port $FUNNEL_PORT"
        break
    fi
done

# Persist Funnel configuration across reboots via systemd service
if [ -n "$FUNNEL_PORT" ]; then
    cat <<EOF | sudo tee /etc/systemd/system/tailscale-funnel.service >/dev/null
[Unit]
Description=Tailscale Funnel Public SSH Forwarding
After=tailscaled.service
Wants=tailscaled.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/tailscale funnel --bg ${FUNNEL_PORT} tcp://localhost:22
ExecStop=/usr/bin/tailscale funnel ${FUNNEL_PORT} off

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload 2>/dev/null || true
    sudo systemctl enable tailscale-funnel.service 2>/dev/null || true
fi

# Check Tailscale Funnel status
echo
echo "==> Tailscale Funnel Status:"
tailscale funnel status 2>/dev/null || tailscale serve status 2>/dev/null || true

# Construct public SSH command & Panel URL
if [ -n "$TS_DOMAIN" ] && [ -n "$FUNNEL_PORT" ]; then
    SSH_CMD="ssh -p $FUNNEL_PORT $SSH_USER@$TS_DOMAIN"
    echo "$SSH_CMD" > /tmp/ssh_cmd.txt
elif [ -n "$TS_DOMAIN" ]; then
    TS_IP=$(tailscale ip -4 2>/dev/null || echo "127.0.0.1")
    SSH_CMD="ssh $SSH_USER@$TS_IP"
    echo "$SSH_CMD" > /tmp/ssh_cmd.txt
else
    echo "ssh $SSH_USER@localhost" > /tmp/ssh_cmd.txt
fi

echo
echo "======================================"
echo "Processes, Cloudflare Tunnel, and SSH Funnel Active"
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
