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

# Wait up to 10 extra seconds for tunnels if not yet written
for i in {1..10}; do
    if [ -s /tmp/cloudflared.url ] && [ -s /tmp/ssh_cmd.txt ]; then
        break
    fi
    sleep 1
done

CF_URL="Pending Cloudflare..."
if [ -s /tmp/cloudflared.url ]; then
    CF_URL=$(cat /tmp/cloudflared.url)
fi

SSH_CMD="Pending SSH tunnel..."
if [ -s /tmp/ssh_cmd.txt ]; then
    SSH_CMD=$(cat /tmp/ssh_cmd.txt)
elif [ -z "${NGROK_AUTHTOKEN:-}" ]; then
    SSH_CMD="⚠️ Add NGROK_AUTHTOKEN to GitHub Secrets"
elif [ -f /tmp/ngrok.log ]; then
    ERR_MSG=$(grep -oiE 'err_ngrok_[0-9]+' /tmp/ngrok.log | head -n 1 || true)
    if [ -n "$ERR_MSG" ]; then
        SSH_CMD="⚠️ ngrok error: $ERR_MSG"
    fi
fi

USER_NAME="${SERVER_USERNAME:-admin}"
NOW=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
STATUS_LOVE=$(bot_status_plain "❤️ Love Whispers" "/tmp/love-whispers.pid")
STATUS_PACK=$(bot_status_plain "🎒 PackTogether" "/tmp/packtogether.pid")
CPU_INFO=$(get_cpu)
RAM_INFO=$(get_memory)

TELEGRAM_MSG="<b>🚀 BOT SERVER IS ONLINE</b>

<b>🌐 Web Control Panel</b>
<a href=\"${CF_URL}\">${CF_URL}</a>
<i>Username:</i> <code>${USER_NAME}</code>

<b>💻 SSH Terminal Access</b>
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
  🌐 Web Control Panel  : ${CF_URL}
  💻 SSH Terminal Access : ${SSH_CMD}
  👤 Username           : ${USER_NAME}
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
