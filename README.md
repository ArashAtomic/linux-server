# Bot Server & Control Center

Disposable GitHub Actions environment for running Telegram bots with a web-based GUI management panel, direct SSH access (`tmate`), interactive Telegram bot commands, and 24/7 auto-renewing runner architecture.

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
│       ├── index.html
│       └── login.html
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

- **Web Control Panel (Cloudflare Tunnel)**:
  - Real-time CPU & RAM metrics.
  - Bot lifecycle management (Start, Restart, Stop).
  - Password-protected with `SERVER_USERNAME` and `SERVER_PASSWORD`.
  - One-click **🔄 Redeploy Server** button to trigger a fresh GitHub runner with the latest code.
  - Live log streaming with search filter and pause/resume.
  - Environment variables viewer with secret masking toggle.
  - File Explorer for browsing directory contents and viewing source files.
- **Direct SSH Access (`tmate`)**:
  - Direct native SSH terminal access from any computer or terminal worldwide:
    ```bash
    ssh <SESSION_ID>@<REGION>.tmate.io
    ```
  - Zero accounts, zero tokens, zero credit card requirements.
- **Interactive Telegram Bot Commands**:
  - Automatically notifies your Telegram status chat when a new runner boots up with Web Panel URL & direct SSH command.
  - `🔄 /redeploy` or `/restart` - Trigger a fresh GitHub Actions workflow run and update the server instantly.
  - `📊 /status` - Real-time CPU/RAM stats and bot health.
  - `🌐 /panel` - Direct link to the Web Management Panel.
  - `💻 /ssh` - Direct `ssh` terminal command.
- **Continuous 24/7 Uptime**:
  - 5-hour and 45-minute runner cycle with automated handoff triggering the next GitHub Actions workflow.

## Remote Access

When the workflow boots:
1. **Cloudflare Tunnel** creates a secure HTTPS URL for the Web Control Panel (`https://<random>.trycloudflare.com`).
2. **`tmate`** establishes an SSH session relay (`ssh <id>@<region>.tmate.io`).
3. Both endpoints are delivered to your Telegram status chat and logged in the workflow execution step.

## GitHub Configuration

### Variables (**Settings → Secrets and variables → Actions → Variables tab**)

| Variable | Description | Default (if unset) |
|---|---|---|
| `SERVER_USERNAME` | Web Panel login username | `admin` |

### Secrets (**Settings → Secrets and variables → Actions → Secrets tab**)

| Secret | Description | Default (if unset) |
|---|---|---|
| `SERVER_PASSWORD` | Web Panel login password | `admin` |
| `GH_PAT` | Personal Access Token (`ArashAtomic`) to trigger workflow redeploys | None |
| `CLONE_PAT` | Personal Access Token (`ArashMaghsoodi`) to clone private `love-whispers-bot` | None |
| `LOVE_WHISPERS_ENV` | Complete `.env` content for Love Whispers | None |
| `PACKTOGETHER_ENV` | Complete `.env` content for PackTogether | None |
| `STATUS_BOT_TOKEN` | Telegram bot token for status notifications & commands | None |
| `STATUS_CHAT_ID` | Telegram chat ID for notifications & commands | None |

## Starting

Go to: **GitHub → Actions → Bot Server → Run workflow**
