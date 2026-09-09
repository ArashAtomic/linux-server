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

# Wait up to 10 seconds for endpoint files if not yet written
for i in {1..10}; do
    if [ -s /tmp/panel_url.txt ] && [ -s /tmp/ssh_cmd.txt ]; then
        break
    fi
    sleep 1
done

PANEL_URL="http://localhost:8080"
if [ -s /tmp/panel_url.txt ]; then
    PANEL_URL=$(cat /tmp/panel_url.txt)
fi

SSH_CMD="ssh admin@localhost"
if [ -s /tmp/ssh_cmd.txt ]; then
    SSH_CMD=$(cat /tmp/ssh_cmd.txt)
fi

USER_NAME="${SERVER_USERNAME:-admin}"
NOW=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
STATUS_LOVE=$(bot_status_plain "❤️ Love Whispers" "/tmp/love-whispers.pid")
STATUS_PACK=$(bot_status_plain "🎒 PackTogether" "/tmp/packtogether.pid")
CPU_INFO=$(get_cpu)
RAM_INFO=$(get_memory)

TELEGRAM_MSG="<b>🚀 BOT SERVER IS ONLINE</b>

<b>🌐 Web Control Panel</b>
<a href=\"${PANEL_URL}\">${PANEL_URL}</a>
<i>Username:</i> <code>${USER_NAME}</code>

<b>💻 SSH Terminal Access (private Tailscale network)</b>
<code>${SSH_CMD}</code>

<b>🤖 Bot Status</b>
${STATUS_LOVE}
${STATUS_PACK}

<b>📊 System Resources</b>
CPU load: ${CPU_INFO}
RAM: ${RAM_INFO}
Started at: ${NOW}"

PLAIN_MSG="==================================================
  🚀 BOT SERVER IS ONLINE
  🌐 Web Control Panel   : ${PANEL_URL}
  💻 SSH Terminal Access : ${SSH_CMD}
  👤 Username            : ${USER_NAME}
  ------------------------------------------------
  🤖 Bot Status:
    ${STATUS_LOVE}
    ${STATUS_PACK}
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
