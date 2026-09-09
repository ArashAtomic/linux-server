#!/usr/bin/env bash

set +e

echo "======================================"
echo "Stopping server processes, Cloudflare Tunnel & Funnel"
echo "======================================"

stop_process() {
    NAME="$1"
    PID_FILE="$2"

    if [ ! -f "$PID_FILE" ]; then
        echo "$NAME: PID file not found"
        return
    fi

    PID=$(cat "$PID_FILE")

    if kill -0 "$PID" 2>/dev/null; then
        echo "$NAME: stopping PID $PID"

        kill "$PID" 2>/dev/null || true

        for i in {1..10}; do
            if ! kill -0 "$PID" 2>/dev/null; then
                echo "$NAME: stopped gracefully"
                rm -f "$PID_FILE"
                return
            fi

            sleep 1
        done

        echo "$NAME: did not stop gracefully, forcing termination"

        kill -9 "$PID" 2>/dev/null || true
    else
        echo "$NAME: already stopped"
    fi

    rm -f "$PID_FILE"
}

stop_process "Love Whispers" "/tmp/love-whispers.pid"
stop_process "PackTogether" "/tmp/packtogether.pid"
stop_process "Hermes Agent" "/tmp/hermes.pid"
stop_process "Management Panel" "/tmp/panel.pid"
stop_process "Cloudflare Tunnel" "/tmp/cloudflared.pid"

rm -f /tmp/panel_url.txt /tmp/cloudflared.url /tmp/ssh_cmd.txt /tmp/cloudflared.log /tmp/funnel_ssh.log

echo
echo "All processes stopped."
