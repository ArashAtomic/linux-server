import os
import time
import signal
import threading
import subprocess
import json
import urllib.request
import urllib.parse
from flask import Flask, jsonify, render_template, request
import psutil

app = Flask(__name__)

BASE_DIR = os.path.expanduser("~/bot-server")
START_TIME = time.time()

BOTS = {
    "love-whispers": {
        "name": "Love Whispers",
        "icon": "❤️",
        "dir": os.path.join(BASE_DIR, "bots/love-whispers-bot"),
        "entry": "bot.py",
        "pid_path": "/tmp/love-whispers.pid",
        "log_path": "/tmp/love-whispers.log"
    },
    "packtogether": {
        "name": "PackTogether",
        "icon": "🎒",
        "dir": os.path.join(BASE_DIR, "bots/PackTogether"),
        "entry": "packtogether/bot.py",
        "pid_path": "/tmp/packtogether.pid",
        "log_path": "/tmp/packtogether.log"
    }
}

RESTART_COUNTS = {
    "love-whispers": 0,
    "packtogether": 0
}

def get_bot_proc(bot_key):
    pid_path = BOTS[bot_key]["pid_path"]
    if os.path.exists(pid_path):
        try:
            with open(pid_path, "r") as f:
                pid = int(f.read().strip())
                if psutil.pid_exists(pid):
                    proc = psutil.Process(pid)
                    if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                        return proc
        except Exception:
            pass
    return None

def get_tailscale_ip():
    try:
        out = subprocess.check_output(["tailscale", "ip", "-4"], text=True, timeout=3).strip()
        return out if out else "127.0.0.1"
    except Exception:
        return "127.0.0.1"

def format_uptime(seconds):
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"

def send_telegram_msg(message):
    token = os.environ.get("STATUS_BOT_TOKEN")
    chat_id = os.environ.get("STATUS_CHAT_ID")
    if not token or not chat_id:
        return
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }).encode("utf-8")
    
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")

def trigger_github_redeploy(source="User"):
    pat = os.environ.get("GH_PAT")
    repo = os.environ.get("GITHUB_REPO", "ArashAtomic/linux-server")
    ref = os.environ.get("GITHUB_REF_NAME", "main")

    # Mark trigger file so current workflow exits keep-alive cleanly
    with open("/tmp/redeploy.trigger", "w") as f:
        f.write(f"triggered by {source} at {time.time()}\n")

    # Dispatch new workflow run
    dispatched = False
    if pat and repo:
        url = f"https://api.github.com/repos/{repo}/actions/workflows/server.yml/dispatches"
        payload = json.dumps({"ref": ref}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {pat}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "Bot-Server-Management-Panel"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as res:
                if res.status in [200, 204]:
                    dispatched = True
        except Exception as e:
            print(f"GitHub API dispatch error: {e}")

    if not dispatched:
        # Fallback to local gh CLI if available
        try:
            subprocess.run(["gh", "workflow", "run", "server.yml", "--ref", ref], check=True, timeout=10)
            dispatched = True
        except Exception as e:
            print(f"gh CLI fallback error: {e}")

    msg = f"🔄 <b>Server Redeploy Triggered ({source})</b>\n\nA new GitHub Actions workflow run has been started with the latest code. The current server will shut down shortly."
    send_telegram_msg(msg)
    return dispatched

# Background Telegram Bot Command Listener
def telegram_poll_worker():
    token = os.environ.get("STATUS_BOT_TOKEN")
    allowed_chat_id = str(os.environ.get("STATUS_CHAT_ID", ""))
    if not token or not allowed_chat_id:
        print("Telegram bot listener skipped: STATUS_BOT_TOKEN or STATUS_CHAT_ID missing.")
        return

    offset = 0
    print("Starting Telegram command listener...")
    while True:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=20"
            req = urllib.request.Request(url, headers={"User-Agent": "Bot-Server-Listener"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if data.get("ok"):
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    chat = msg.get("chat", {})
                    chat_id = str(chat.get("id", ""))
                    text = msg.get("text", "").strip()

                    # Only respond to authorized chat ID
                    if chat_id != allowed_chat_id:
                        continue

                    cmd = text.split()[0].lower() if text else ""

                    if cmd in ["/redeploy", "/restart"]:
                        send_telegram_msg("⏳ <b>Triggering Server Redeploy...</b>\nStarting fresh GitHub runner instance with latest repository code.")
                        trigger_github_redeploy(source="Telegram /redeploy")

                    elif cmd in ["/status", "/ping"]:
                        cpu = psutil.cpu_percent(interval=0.2)
                        ram = psutil.virtual_memory()
                        ts_ip = get_tailscale_ip()
                        
                        b1 = "🟢 RUNNING" if get_bot_proc("love-whispers") else "🔴 STOPPED"
                        b2 = "🟢 RUNNING" if get_bot_proc("packtogether") else "🔴 STOPPED"
                        
                        status_msg = (
                            f"<b>🖥️ Server Status</b>\n\n"
                            f"⏱ Uptime: {format_uptime(time.time() - START_TIME)}\n"
                            f"🌐 IP: <code>{ts_ip}</code>\n"
                            f"💻 CPU: {cpu}%\n"
                            f"🧠 RAM: {round(ram.used/(1024**3), 2)} / {round(ram.total/(1024**3), 2)} GB\n\n"
                            f"<b>Bots:</b>\n"
                            f"❤️ Love Whispers: {b1}\n"
                            f"🎒 PackTogether: {b2}"
                        )
                        send_telegram_msg(status_msg)

                    elif cmd == "/panel":
                        ts_ip = get_tailscale_ip()
                        send_telegram_msg(f"🌐 <b>Web Control Panel:</b>\n<a href=\"http://{ts_ip}:8080\">http://{ts_ip}:8080</a>")

                    elif cmd == "/ssh":
                        ts_ip = get_tailscale_ip()
                        user = os.environ.get("SSH_USER", "admin")
                        send_telegram_msg(f"💻 <b>SSH Connection:</b>\n<code>ssh {user}@{ts_ip}</code>")

                    elif cmd in ["/help", "/start"]:
                        help_msg = (
                            "<b>🤖 Server Control Commands:</b>\n\n"
                            "🔄 <code>/redeploy</code> - Trigger fresh workflow run & update server\n"
                            "📊 <code>/status</code> - Current CPU, RAM, and bot health\n"
                            "🌐 <code>/panel</code> - Open Web Management Panel\n"
                            "💻 <code>/ssh</code> - View SSH terminal access command"
                        )
                        send_telegram_msg(help_msg)

        except Exception as e:
            time.sleep(5)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def status():
    cpu = psutil.cpu_percent(interval=0.2)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    
    ts_ip = get_tailscale_ip()
    ssh_user = os.environ.get("SSH_USER", "admin")
    
    bot_status = {}
    online_count = 0
    for key, info in BOTS.items():
        proc = get_bot_proc(key)
        running = proc is not None
        if running:
            online_count += 1
            proc_uptime = format_uptime(time.time() - proc.create_time())
            pid = proc.pid
        else:
            proc_uptime = "—"
            pid = "—"

        bot_status[key] = {
            "name": info["name"],
            "icon": info["icon"],
            "running": running,
            "pid": pid,
            "uptime": proc_uptime,
            "restarts": RESTART_COUNTS[key]
        }

    return jsonify({
        "system": {
            "cpu_percent": cpu,
            "ram_used_gb": round(ram.used / (1024**3), 2),
            "ram_total_gb": round(ram.total / (1024**3), 2),
            "ram_percent": ram.percent,
            "disk_percent": disk.percent,
            "uptime": format_uptime(time.time() - START_TIME),
            "tailscale_ip": ts_ip,
            "ssh_command": f"ssh {ssh_user}@{ts_ip}",
            "panel_url": f"http://{ts_ip}:8080"
        },
        "fleet": {
            "online": online_count,
            "total": len(BOTS)
        },
        "bots": bot_status
    })

@app.route("/api/server/redeploy", methods=["POST"])
def redeploy_server():
    success = trigger_github_redeploy(source="Web Panel")
    return jsonify({
        "success": True,
        "message": "Server redeploy initiated. Fresh GitHub runner is launching with the latest code."
    })

@app.route("/api/logs/<bot_key>")
def get_logs(bot_key):
    if bot_key not in BOTS and bot_key != "panel":
        return jsonify({"error": "Unknown log target"}), 404
    
    log_path = "/tmp/panel.log" if bot_key == "panel" else BOTS[bot_key]["log_path"]
    lines = int(request.args.get("lines", 200))
    
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", errors="replace") as f:
                content = f.readlines()
                return jsonify({"logs": "".join(content[-lines:]), "total_lines": len(content)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"logs": "Log file not found or empty.", "total_lines": 0})

@app.route("/api/env/<bot_key>")
def get_env(bot_key):
    if bot_key not in BOTS:
        return jsonify({"error": "Unknown bot"}), 404
    
    env_path = os.path.join(BOTS[bot_key]["dir"], ".env")
    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip()
    return jsonify({"env": env_vars, "count": len(env_vars)})

@app.route("/api/bot/<bot_key>/<action>", methods=["POST"])
def control_bot(bot_key, action):
    if bot_key not in BOTS:
        return jsonify({"error": "Unknown bot"}), 404
    
    info = BOTS[bot_key]
    proc = get_bot_proc(bot_key)

    if action in ["stop", "restart"]:
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if os.path.exists(info["pid_path"]):
            try:
                os.remove(info["pid_path"])
            except Exception:
                pass
        
        if action == "stop":
            return jsonify({"success": True, "message": f"{info['name']} stopped"})

    if action in ["start", "restart"]:
        if action == "restart":
            RESTART_COUNTS[bot_key] += 1

        venv_python = os.path.join(info["dir"], ".venv/bin/python")
        cmd = f"cd {info['dir']} && nohup {venv_python} -u {info['entry']} > {info['log_path']} 2>&1 & echo $! > {info['pid_path']}"
        subprocess.Popen(cmd, shell=True)
        return jsonify({"success": True, "message": f"{info['name']} started"})

    return jsonify({"error": "Invalid action"}), 400

@app.route("/api/files")
def list_files():
    req_path = request.args.get("path")
    if not req_path:
        target_path = BASE_DIR
    else:
        target_path = os.path.abspath(req_path)
    
    allowed_roots = [os.path.expanduser("~"), "/tmp"]
    if not any(target_path.startswith(os.path.abspath(r)) for r in allowed_roots):
        target_path = BASE_DIR

    if not os.path.exists(target_path):
        return jsonify({"error": "Path does not exist"}), 404

    if os.path.isfile(target_path):
        return jsonify({"is_file": True, "path": target_path})

    try:
        entries = []
        raw_items = os.listdir(target_path)
        valid_items = [item for item in raw_items if not item.startswith(".venv") and item != "__pycache__"]
        
        for item in valid_items:
            full_path = os.path.join(target_path, item)
            is_dir = os.path.isdir(full_path)
            size = os.path.getsize(full_path) if not is_dir else 0
            entries.append({
                "name": item,
                "path": full_path,
                "is_dir": is_dir,
                "size_bytes": size
            })
        
        entries.sort(key=lambda x: (0 if x["is_dir"] else 1, x["name"].lower()))
        
        parent = os.path.dirname(target_path)
        return jsonify({
            "is_file": False,
            "current_path": target_path,
            "parent_path": parent if parent != target_path else None,
            "entries": entries
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/file/content")
def file_content():
    req_path = request.args.get("path", "")
    target_path = os.path.abspath(req_path)

    allowed_roots = [os.path.expanduser("~"), "/tmp"]
    if not any(target_path.startswith(os.path.abspath(r)) for r in allowed_roots):
        return jsonify({"error": "Access denied"}), 403

    if not os.path.isfile(target_path):
        return jsonify({"error": "File not found"}), 404

    try:
        if os.path.getsize(target_path) > 2 * 1024 * 1024:
            return jsonify({"content": "File too large to preview (>2MB)."}), 400

        with open(target_path, "r", errors="replace") as f:
            content = f.read()
            return jsonify({"content": content, "path": target_path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    t = threading.Thread(target=telegram_poll_worker, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=8080)
