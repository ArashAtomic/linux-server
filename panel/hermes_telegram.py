#!/usr/bin/env python3
"""Telegram bridge for the local Hermes OpenAI-compatible gateway."""

import json
import os
import signal
import time
import urllib.parse
import urllib.request
from pathlib import Path
from threading import Lock

import requests


BOT_TOKEN = os.environ.get("HERMES_TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_ID = str(os.environ.get("STATUS_CHAT_ID", ""))
HERMES_API_URL = os.environ.get("HERMES_API_URL", "http://127.0.0.1:8642").rstrip("/")
HERMES_API_KEY = os.environ.get("HERMES_API_SERVER_KEY", "")
HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
STATE_PATH = Path(os.environ.get("HERMES_TELEGRAM_STATE", str(HERMES_HOME / "telegram-bridge.json")))
PID_PATH = Path(os.environ.get("HERMES_TELEGRAM_PID", "/tmp/hermes-telegram.pid"))
MAX_MESSAGE_LENGTH = 3900
MAX_HISTORY = 40
POLL_TIMEOUT = 25

state_lock = Lock()
stop_requested = False


def load_state():
    default = {"offset": 0, "active": {}, "sessions": {}}
    try:
        with STATE_PATH.open("r", encoding="utf-8") as state_file:
            value = json.load(state_file)
        if isinstance(value, dict):
            default.update(value)
    except (OSError, ValueError):
        pass
    return default


state = load_state()


def save_state():
    STATE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path = STATE_PATH.with_suffix(".tmp")
    with temporary_path.open("w", encoding="utf-8") as state_file:
        json.dump(state, state_file, indent=2)
    os.chmod(temporary_path, 0o600)
    temporary_path.replace(STATE_PATH)


def telegram_call(method, values=None, timeout=30):
    if not BOT_TOKEN:
        raise RuntimeError("HERMES_TELEGRAM_BOT_TOKEN is not configured")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    encoded = urllib.parse.urlencode(values or {}).encode("utf-8")
    request = urllib.request.Request(url, data=encoded, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description", f"Telegram {method} failed"))
    return payload.get("result")


def send_message(chat_id, text, reply_markup=None):
    text = str(text or "No response.")
    chunks = [text[index:index + MAX_MESSAGE_LENGTH] for index in range(0, len(text), MAX_MESSAGE_LENGTH)]
    for index, chunk in enumerate(chunks or ["No response."]):
        values = {"chat_id": chat_id, "text": chunk}
        if reply_markup and index == len(chunks) - 1:
            values["reply_markup"] = json.dumps(reply_markup)
        telegram_call("sendMessage", values)


def hermes_headers():
    return {"Authorization": f"Bearer {HERMES_API_KEY}", "Accept": "application/json"}


def configured_model():
    config_path = HERMES_HOME / "config.yaml"
    try:
        import yaml

        with config_path.open("r", encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file) or {}
        model = (config.get("model") or {}).get("default")
        if isinstance(model, str) and model.strip():
            return model.strip()
    except (OSError, ValueError, ImportError):
        pass
    return os.environ.get("HERMES_MODEL", "hermes-agent")


def sessions_for(chat_id):
    return state.setdefault("sessions", {}).setdefault(str(chat_id), {})


def active_session(chat_id):
    chat_key = str(chat_id)
    session_id = state.setdefault("active", {}).get(chat_key)
    sessions = sessions_for(chat_id)
    if session_id not in sessions:
        session_id = f"telegram-{chat_key}-default"
        sessions[session_id] = {"messages": [], "created": int(time.time()), "updated": int(time.time())}
        state["active"][chat_key] = session_id
        save_state()
    return session_id, sessions[session_id]


def chat_with_hermes(session):
    response = requests.post(
        f"{HERMES_API_URL}/v1/chat/completions",
        headers={**hermes_headers(), "Content-Type": "application/json"},
        json={"model": configured_model(), "messages": session["messages"], "stream": False},
        timeout=(5, 180),
    )
    if not response.ok:
        raise RuntimeError(f"Hermes returned HTTP {response.status_code}")
    payload = response.json()
    choices = payload.get("choices") or []
    message = choices[0].get("message", {}) if choices else {}
    content = message.get("content", "")
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return content.strip() or "Hermes returned an empty response."


def write_model(model):
    import yaml

    config_path = HERMES_HOME / "config.yaml"
    config = {}
    if config_path.is_file():
        with config_path.open("r", encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file) or {}
    config.setdefault("model", {})["default"] = model
    temporary_path = config_path.with_suffix(".tmp")
    with temporary_path.open("w", encoding="utf-8") as config_file:
        yaml.safe_dump(config, config_file, sort_keys=False)
    os.chmod(temporary_path, 0o600)
    temporary_path.replace(config_path)


def handle_command(chat_id, text):
    parts = text.split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower()
    argument = parts[1].strip() if len(parts) == 2 else ""

    if command in ("/start", "/help"):
        return ("Hermes control is connected.\n\n"
                "/sessions - list saved sessions\n"
                "/resume <id> - select a session\n"
                "/new - create a session\n"
                "/reset - clear the selected session\n"
                "/models - list gateway models\n"
                "/model <id> - select a model\n"
                "/status - Hermes and server status\n"
                "/redeploy - request a fresh runner\n\n"
                "Dangerous tool actions require explicit approval. The approval adapter is being enabled next.")

    if command == "/sessions":
        sessions = sessions_for(chat_id)
        active, _ = active_session(chat_id)
        lines = [f"{'* ' if session_id == active else ''}{session_id} ({len(item.get('messages', []))} messages)"
                 for session_id, item in sessions.items()]
        return "Saved sessions:\n" + "\n".join(lines)

    if command == "/new":
        session_id = f"telegram-{chat_id}-{int(time.time())}"
        sessions_for(chat_id)[session_id] = {"messages": [], "created": int(time.time()), "updated": int(time.time())}
        state["active"][str(chat_id)] = session_id
        save_state()
        return f"Created and selected `{session_id}`."

    if command == "/resume":
        sessions = sessions_for(chat_id)
        if argument not in sessions:
            return "Unknown session. Use /sessions to see valid session IDs."
        state["active"][str(chat_id)] = argument
        save_state()
        return f"Resumed `{argument}`."

    if command == "/reset":
        _, selected = active_session(chat_id)
        selected["messages"] = []
        selected["updated"] = int(time.time())
        save_state()
        return "Selected session history cleared."

    if command == "/models":
        response = requests.get(f"{HERMES_API_URL}/v1/models", headers=hermes_headers(), timeout=15)
        if not response.ok:
            return f"Hermes model endpoint returned HTTP {response.status_code}."
        models = [item.get("id") for item in response.json().get("data", []) if isinstance(item, dict) and item.get("id")]
        return "Available models:\n" + "\n".join(models[:100]) if models else "Hermes returned no models."

    if command == "/model":
        if not argument or len(argument) > 300 or any(char in argument for char in "\r\n"):
            return f"Current model: `{configured_model()}`\nUse /model <model-id> to change it."
        try:
            write_model(argument)
            return f"Selected model `{argument}`."
        except (OSError, ValueError, ImportError) as error:
            return f"Could not save model: {error}"

    if command == "/status":
        try:
            response = requests.get(f"{HERMES_API_URL}/health", headers=hermes_headers(), timeout=5)
            return f"Hermes: {'ready' if response.ok else 'unavailable'}\nModel: {configured_model()}"
        except requests.RequestException:
            return "Hermes: unavailable"

    if command in ("/redeploy", "/tools", "/skills", "/provider", "/approve", "/deny", "/allowlist", "/bg", "/compress", "/sethome", "/stop"):
        return "This control is reserved for the Hermes adapter phase and is not enabled yet. No action was taken."

    return None


def handle_update(update):
    message = update.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    text = message.get("text", "").strip()
    if not chat_id or chat_id != ALLOWED_CHAT_ID or not text:
        return

    command_response = handle_command(chat_id, text) if text.startswith("/") else None
    if command_response is not None:
        send_message(chat_id, command_response)
        return

    session_id, selected = active_session(chat_id)
    with state_lock:
        selected["messages"] = (selected.get("messages") or [])[-(MAX_HISTORY - 1):]
        selected["messages"].append({"role": "user", "content": text})
        selected["updated"] = int(time.time())
        try:
            response = chat_with_hermes(selected)
        except (requests.RequestException, ValueError, RuntimeError) as error:
            selected["messages"].pop()
            save_state()
            send_message(chat_id, f"Hermes request failed: {error}")
            return
        selected["messages"].append({"role": "assistant", "content": response})
        save_state()
    send_message(chat_id, response)


def signal_handler(_signum, _frame):
    global stop_requested
    stop_requested = True


def main():
    if not BOT_TOKEN or not ALLOWED_CHAT_ID or not HERMES_API_KEY:
        raise SystemExit("HERMES_TELEGRAM_BOT_TOKEN, STATUS_CHAT_ID, and HERMES_API_SERVER_KEY are required")
    me = telegram_call("getMe", timeout=10)
    print(f"Hermes Telegram bridge authenticated as @{me.get('username', 'unknown')}", flush=True)
    health = requests.get(f"{HERMES_API_URL}/health", headers=hermes_headers(), timeout=5)
    if not health.ok:
        raise SystemExit(f"Hermes gateway health check failed with HTTP {health.status_code}")
    print(f"Hermes Telegram bridge is ready for chat {ALLOWED_CHAT_ID}", flush=True)
    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    try:
        while not stop_requested:
            try:
                updates = telegram_call("getUpdates", {"offset": state.get("offset", 0), "timeout": POLL_TIMEOUT}, timeout=35)
                for update in updates or []:
                    state["offset"] = update["update_id"] + 1
                    save_state()
                    handle_update(update)
            except Exception as error:
                print(f"Hermes Telegram bridge error: {error}", flush=True)
                time.sleep(5)
    finally:
        PID_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()