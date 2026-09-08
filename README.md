# Bot Server & Control Center

Disposable GitHub Actions environment for running Telegram bots with a web-based GUI management panel, SSH access, and 24/7 auto-renewing runner architecture.

## Structure

```text
repository/
├── .github/
│   └── workflows/
│       └── server.yml
│
├── panel/
│   ├── app.py
│   └── templates/
│       └── index.html
│
├── scripts/
│   ├── setup.sh
│   ├── start-bots.sh
│   ├── heartbeat.sh
│   └── stop-bots.sh
│
└── README.md
```

## Features

- **Web Control Panel (Port 8080)**:
  - Real-time CPU & RAM metrics.
  - Bot lifecycle management (Start, Restart, Stop).
  - Live log streaming with search filter and pause/resume.
  - Environment variables viewer with secret masking toggle.
  - File Explorer for browsing directory contents and viewing source files.
  - Copyable SSH connection command.
- **Direct Telegram Startup Notification**:
  - Automatically notifies your Telegram status chat when a new runner boots up.
  - Includes direct links to the Web Panel (`http://<TAILSCALE_IP>:8080`) and SSH access command.
- **Continuous 24/7 Uptime**:
  - 5-hour runner cycle with automated handoff triggering the next GitHub Actions workflow.

## SSH & Web Panel Access

When `TAILSCALE_AUTHKEY` is provided, the runner connects to your Tailscale VPN network:

1. **Web Control Panel**:
   Open in your browser:
   ```text
   http://<TAILSCALE_IP>:8080
   ```
2. **SSH Terminal**:
   ```bash
   ssh <SERVER_USERNAME>@<TAILSCALE_IP>
   ```

## GitHub Configuration

### Variables (**Settings → Secrets and variables → Actions → Variables tab**)

| Variable | Description | Default (if unset) |
|---|---|---|
| `SERVER_USERNAME` | SSH username (unmasked in logs) | `admin` |

### Secrets (**Settings → Secrets and variables → Actions → Secrets tab**)

| Secret | Description | Default (if unset) |
|---|---|---|
| `SERVER_PASSWORD` | SSH password (masked in logs) | `admin` |
| `TAILSCALE_AUTHKEY` | Tailscale auth key for SSH and Web Panel access | None |
| `GH_PAT` | Personal Access Token to clone private `love-whispers-bot` & dispatch next workflow | None |
| `LOVE_WHISPERS_ENV` | Complete `.env` content for Love Whispers | None |
| `PACKTOGETHER_ENV` | Complete `.env` content for PackTogether | None |
| `STATUS_BOT_TOKEN` | Telegram bot token for startup notifications | None |
| `STATUS_CHAT_ID` | Telegram chat ID for startup notifications | None |

## Starting

Go to: **GitHub → Actions → Bot Server → Run workflow**
