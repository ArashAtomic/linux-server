#!/usr/bin/env python3
"""Telegram bridge for the local Hermes OpenAI-compatible gateway."""

import json
import os
import signal
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

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
PROVIDER_KEYS = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "github_copilot": "COPILOT_GITHUB_TOKEN",
    "custom": "OPENAI_API_KEY",
    "ninerouter": "OPENAI_API_KEY",
}

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


def delete_message(chat_id, message_id):
    try:
        telegram_call("deleteMessage", {"chat_id": chat_id, "message_id": message_id}, timeout=10)
    except Exception as error:
        print(f"Could not delete credential message: {error}", flush=True)


def hermes_headers():
    return {"Authorization": f"Bearer {HERMES_API_KEY}", "Accept": "application/json"}


def read_provider_env():
    values = {}
    env_path = HERMES_HOME / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


def write_provider_env(values):
    HERMES_HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    env_path = HERMES_HOME / ".env"
    temporary_path = env_path.with_suffix(".tmp")
    temporary_path.write_text("".join(f"{key}={value}\n" for key, value in sorted(values.items())), encoding="utf-8")
    os.chmod(temporary_path, 0o600)
    temporary_path.replace(env_path)


def valid_provider_url(value):
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and len(value) <= 500


def restart_gateway():
    pid_path = Path("/tmp/hermes.pid")
    if pid_path.is_file():
        try:
            old_pid = int(pid_path.read_text(encoding="utf-8").strip())
            os.kill(old_pid, signal.SIGTERM)
            for _ in range(40):
                if not Path(f"/proc/{old_pid}").exists():
                    break
                time.sleep(0.25)
        except (OSError, ValueError):
            pass

    hermes_command = shutil.which("hermes") or str(Path.home() / ".local/bin/hermes")
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", str(Path.home())),
        "HERMES_HOME": str(HERMES_HOME),
        "API_SERVER_ENABLED": "true",
        "API_SERVER_HOST": "127.0.0.1",
        "API_SERVER_PORT": "8642",
        "API_SERVER_KEY": HERMES_API_KEY,
    }
    log_file = open("/tmp/hermes.log", "a", encoding="utf-8")
    process = subprocess.Popen(
        [hermes_command, "gateway"],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=environment,
        start_new_session=True,
    )
    pid_path.write_text(str(process.pid), encoding="utf-8")
    for _ in range(30):
        try:
            response = requests.get(f"{HERMES_API_URL}/health", headers=hermes_headers(), timeout=1)
            if response.ok:
                log_file.close()
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    log_file.close()
    raise RuntimeError("Hermes did not become ready after provider restart")


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


def write_model(provider, model, base_url=""):
    import yaml

    config_path = HERMES_HOME / "config.yaml"
    config = {}
    if config_path.is_file():
        with config_path.open("r", encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file) or {}
    model_config = config.setdefault("model", {})
    model_config["provider"] = provider
    model_config["default"] = model
    if base_url:
        model_config["base_url"] = base_url.rstrip("/")
    elif provider not in ("custom", "ninerouter"):
        model_config.pop("base_url", None)
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
                "/provider - show provider commands\n"
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
        if argument:
            parts = argument.split()
            base_url = parts[0].rstrip("/")
            api_key = parts[1] if len(parts) > 1 else ""
            if not valid_provider_url(base_url):
                return "Usage: /models <https://provider.example/v1> [api-key]"
            try:
                response = requests.get(
                    f"{base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                    timeout=15,
                )
                if not response.ok:
                    return f"Provider returned HTTP {response.status_code}: {response.text[:300]}"
                models = [item.get("id") for item in response.json().get("data", [])
                          if isinstance(item, dict) and item.get("id")]
                return "Available provider models:\n" + "\n".join(models[:100]) if models else "Provider returned no models."
            except (requests.RequestException, ValueError) as error:
                return f"Could not fetch provider models: {error}"
        response = requests.get(f"{HERMES_API_URL}/v1/models", headers=hermes_headers(), timeout=15)
        if not response.ok:
            return f"Hermes model endpoint returned HTTP {response.status_code}."
        models = [item.get("id") for item in response.json().get("data", []) if isinstance(item, dict) and item.get("id")]
        return "Available models:\n" + "\n".join(models[:100]) if models else "Hermes returned no models."

    if command == "/model":
        if not argument or len(argument) > 300 or any(char in argument for char in "\r\n"):
            return f"Current model: `{configured_model()}`\nUse /model <model-id> to change it."
        try:
            current_provider = "custom"
            config_path = HERMES_HOME / "config.yaml"
            if config_path.is_file():
                import yaml
                with config_path.open("r", encoding="utf-8") as config_file:
                    current_provider = (yaml.safe_load(config_file) or {}).get("model", {}).get("provider", "custom")
            write_model(current_provider, argument)
            return f"Selected model `{argument}`."
        except (OSError, ValueError, ImportError) as error:
            return f"Could not save model: {error}"

    if command == "/status":
        try:
            response = requests.get(f"{HERMES_API_URL}/health", headers=hermes_headers(), timeout=5)
            return f"Hermes: {'ready' if response.ok else 'unavailable'}\nModel: {configured_model()}"
        except requests.RequestException:
            return "Hermes: unavailable"

    if command == "/provider":
        if not argument or argument.lower() in ("help", "list"):
            configured = [name for name, env_key in PROVIDER_KEYS.items() if read_provider_env().get(env_key)]
            return ("Configured providers: " + (", ".join(configured) or "none") +
                    "\n\nSet a provider (the key is deleted from this Telegram message when possible):\n"
                    "/provider set <name> <base-url-or-> <model> <api-key>\n"
                    "Fetch models:\n/models <base-url> [api-key]")
        parts = argument.split(maxsplit=4)
        if len(parts) != 5 or parts[0].lower() != "set":
            return "Usage: /provider set <name> <base-url-or-> <model> <api-key>"
        provider, base_url, model, value = parts[1:]
        provider = provider.lower()
        if provider not in PROVIDER_KEYS:
            return "Unsupported provider. Use /provider to see configured providers."
        if base_url == "-":
            base_url = ""
        if provider in ("custom", "ninerouter") and not valid_provider_url(base_url):
            return "Custom and 9Router providers require a valid base URL."
        if any(len(item) > 5000 or "\n" in item or "\r" in item for item in (model, value)):
            return "Model or provider key is invalid."
        try:
            values = read_provider_env()
            values[PROVIDER_KEYS[provider]] = value
            write_provider_env(values)
            write_model(provider, model, base_url)
            restart_gateway()
            return f"Provider `{provider}` and model `{model}` saved; Hermes restarted."
        except (OSError, ValueError, RuntimeError, requests.RequestException) as error:
            return f"Provider save failed: {error}"

    if command in ("/redeploy", "/tools", "/skills", "/approve", "/deny", "/allowlist", "/bg", "/compress", "/sethome", "/stop"):
        return "This control is reserved for the Hermes adapter phase and is not enabled yet. No action was taken."

    return None


def handle_update(update):
    message = update.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    text = message.get("text", "").strip()
    if not chat_id or chat_id != ALLOWED_CHAT_ID or not text:
        return

    contains_credential = text.lower().startswith("/provider set ") or (
        text.lower().startswith("/models ") and len(text.split()) > 2
    )

    command_response = handle_command(chat_id, text) if text.startswith("/") else None
    if command_response is not None:
        send_message(chat_id, command_response)
        if contains_credential:
            delete_message(chat_id, message.get("message_id"))
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
    if contains_credential:
        delete_message(chat_id, message.get("message_id"))


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