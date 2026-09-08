#!/usr/bin/env bash

set -u

send_telegram() {
    local MESSAGE="$1"

    if [ -z "${STATUS_BOT_TOKEN:-}" ]; then
        echo "STATUS_BOT_TOKEN is not configured."
        return
    fi

    if [ -z "${STATUS_CHAT_ID:-}" ]; then
        echo "STATUS_CHAT_ID is not configured."
        return
    fi

    curl -sS \
        --max-time 15 \
        -X POST \
        "https://api.telegram.org/bot${STATUS_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${STATUS_CHAT_ID}" \
        --data-urlencode "text=${MESSAGE}" \
        --data-urlencode "parse_mode=HTML" \
        >/dev/null || true
}

get_uptime() {
    uptime -p 2>/dev/null || echo "just started"
}

get_memory() {
    free -h 2>/dev/null | awk '/Mem:/ {print $3 " / " $2}' || echo "unknown"
}

get_cpu() {
    awk '{print $1}' /proc/loadavg 2>/dev/null || echo "unknown"
}

bot_status_plain() {
    local NAME="$1"
    local PID_FILE="$2"

    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "🟢 $NAME: RUNNING (PID $PID)"
            return
        fi
    fi

    echo "🔴 $NAME: STOPPED"
}

get_tailscale_ip() {
    tailscale ip -4 2>/dev/null || echo "127.0.0.1"
}

TS_IP=$(get_tailscale_ip)
SSH_USER="${SSH_USER:-admin}"
NOW=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
STATUS_LOVE=$(bot_status_plain "❤️ Love Whispers" "/tmp/love-whispers.pid")
STATUS_PACK=$(bot_status_plain "🎒 PackTogether" "/tmp/packtogether.pid")
STATUS_PANEL=$(bot_status_plain "🖥️ Control Panel" "/tmp/panel.pid")
CPU_INFO=$(get_cpu)
RAM_INFO=$(get_memory)

TELEGRAM_MSG="<b>🚀 BOT SERVER IS ONLINE</b>

<b>🌐 Web Control Panel</b>
<a href=\"http://${TS_IP}:8080\">http://${TS_IP}:8080</a>

<b>💻 SSH Terminal Access</b>
<code>ssh ${SSH_USER}@${TS_IP}</code>

<b>🤖 Bot Status</b>
${STATUS_LOVE}
${STATUS_PACK}
${STATUS_PANEL}

<b>📊 System Resources</b>
CPU load: ${CPU_INFO}
RAM: ${RAM_INFO}
Started at: ${NOW}"

PLAIN_MSG="==================================================
  🚀 BOT SERVER IS ONLINE
  🌐 Web Control Panel  : http://${TS_IP}:8080
  💻 SSH Terminal Access : ssh ${SSH_USER}@${TS_IP}
  ------------------------------------------------
  🤖 Bot Status:
    ${STATUS_LOVE}
    ${STATUS_PACK}
    ${STATUS_PANEL}
  ------------------------------------------------
  📊 System Resources:
    CPU load: ${CPU_INFO}
    RAM: ${RAM_INFO}
    Started: ${NOW}
=================================================="

echo "$PLAIN_MSG"
echo

send_telegram "$TELEGRAM_MSG"
echo "Startup notification sent to Telegram."
