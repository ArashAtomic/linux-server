import os
import time
import signal
import subprocess
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
    
    # Restrict file browser to home or /tmp for security
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
        
        # Sort folders first, then by name alphabetically
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
    app.run(host="0.0.0.0", port=8080)
