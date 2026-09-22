#!/usr/bin/env bash
# Keeps the Hermes gateway running on a runner that has no service manager.
#
# Hermes' in-chat /restart (and crashes) end the gateway process. Under a supervisor Hermes exits
# with a restart status and this loop starts it again, so Telegram and the API come straight back.
# SIGTERM stops the loop and the gateway it started.
set -u

HERMES_BIN="${HERMES_BIN:-$(command -v hermes || echo "$HOME/.local/bin/hermes")}"

# Newer Hermes builds understand --external-supervisor (exit 75 on restart instead of spawning a
# detached copy the panel could not track). Older builds just run in the foreground.
args=(gateway)
if "$HERMES_BIN" gateway run --help 2>&1 | grep -q -- '--external-supervisor'; then
    args=(gateway run --external-supervisor)
fi

child=0
stopping=0
on_term() {
    stopping=1
    if [ "$child" -gt 0 ]; then kill -TERM "$child" 2>/dev/null; fi
}
trap on_term TERM INT

fast_failures=0
while [ "$stopping" -eq 0 ]; do
    started=$(date +%s)
    "$HERMES_BIN" "${args[@]}" &
    child=$!
    wait "$child"
    code=$?
    while kill -0 "$child" 2>/dev/null; do   # a signal interrupts `wait`; let the gateway finish draining
        wait "$child"
        code=$?
    done
    child=0
    [ "$stopping" -eq 1 ] && break

    lived=$(( $(date +%s) - started ))
    echo "[supervisor] gateway exited with status $code after ${lived}s"
    if [ "$lived" -lt 20 ]; then fast_failures=$((fast_failures + 1)); else fast_failures=0; fi
    delay=3
    [ "$fast_failures" -ge 3 ] && delay=30   # back off if it keeps dying right away
    echo "[supervisor] restarting in ${delay}s"
    sleep "$delay" &
    wait $!
done
echo "[supervisor] stopped"
