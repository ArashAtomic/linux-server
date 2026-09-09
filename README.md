# Bot Server & Control Center

Disposable GitHub Actions environment for running Telegram bots with a web-based GUI management panel, Cloudflare-hosted panel access, direct public SSH access via **Tailscale Funnel**, interactive Telegram bot commands, and 24/7 auto-renewing runner architecture.

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
  - **Server Assistant** powered by Hermes Agent, with streamed chat, compact tool activity, Stop control, and curated command autocomplete.
  - Provider setup from the authenticated panel; provider credentials are stored in Hermes state and restored on redeploy.
- **Public SSH Access (Tailscale Funnel)**:
  - Exposes the VM's OpenSSH server (port 22) to the public internet via Tailscale Funnel.
  - Port priority: **443** → fallback **8443** → fallback **10000**.
  - Works from any machine with a standard SSH client — **no Tailscale install needed on the client**:
    ```bash
    ssh -p 443 <SERVER_USERNAME>@<machine>.<tailnet>.ts.net
    ```
  - Prompts for your `SERVER_PASSWORD` in the terminal before granting shell access.
  - Funnel config persisted via a `tailscale-funnel.service` systemd unit.
- **Interactive Telegram Bot Commands**:
  - On boot, sends the Web Panel URL and the exact SSH command to your Telegram status chat.
  - `🔄 /redeploy` or `/restart` - Trigger a fresh GitHub Actions workflow run and update the server instantly.
  - `📊 /status` - Real-time CPU/RAM stats and bot health.
  - `🌐 /panel` - Direct link to the Web Management Panel.
  - `💻 /ssh` - The exact SSH command with the current Funnel port.
- **Continuous 24/7 Uptime**:
  - 5-hour and 45-minute runner cycle with automated handoff triggering the next GitHub Actions workflow.

## Remote Access

When the workflow boots:
1. **Cloudflare Tunnel** publishes the Web Control Panel at a temporary `https://<random>.trycloudflare.com` URL.
2. **Tailscale** connects the runner to your tailnet using `TAILSCALE_AUTHKEY` (hostname `bot-server`).
3. **Tailscale Funnel** publishes `tcp://localhost:22` publicly on port 443 (or 8443/10000 if 443 is taken).
4. The exact SSH command and Cloudflare Panel URL are sent to Telegram and printed in the workflow log.

> **Note**: Funnel must be allowed for the node. In the Tailscale admin console, ensure your tailnet policy contains a `nodeAttrs` block granting funnel, or enable HTTPS/MagicDNS for the tailnet when prompted.

## GitHub Configuration

### Variables (**Settings → Secrets and variables → Actions → Variables tab**)

| Variable | Description | Default (if unset) |
|---|---|---|
| `SERVER_USERNAME` | Web Panel & SSH login username | `admin` |

### Secrets (**Settings → Secrets and variables → Actions → Secrets tab**)

| Secret | Description | Default (if unset) |
|---|---|---|
| `SERVER_PASSWORD` | Web Panel & SSH login password | `admin` |
| `TAILSCALE_AUTHKEY` | Tailscale auth key (reusable/Ephemeral off, with Funnel-capable device approval) | None |
| `GH_PAT` | Personal Access Token (`ArashAtomic`) to trigger workflow redeploys | None |
| `CLONE_PAT` | Personal Access Token (`ArashMaghsoodi`) to clone private `love-whispers-bot` | None |
| `LOVE_WHISPERS_ENV` | Complete `.env` content for Love Whispers | None |
| `PACKTOGETHER_ENV` | Complete `.env` content for PackTogether | None |
| `STATUS_BOT_TOKEN` | Telegram bot token for status notifications & commands | None |
| `STATUS_CHAT_ID` | Telegram chat ID for notifications & commands | None |
| `HERMES_API_SERVER_KEY` | Strong bearer key used internally between the panel and Hermes | Required |

### Hermes Provider Setup

Provider API keys are intentionally **not required during workflow setup**. After the server is online:

1. Open the Web Control Panel and select **Server Assistant**.
2. Open **Providers**.
3. Choose a provider, enter its API key or token, and select **Save & Restart Hermes**.

The value is written to the runner's `~/.hermes/.env` with restricted permissions. Hermes state, including configured providers, is saved to and restored from the `hermes-state-*` Actions cache during runner replacement. The API key used by the panel itself remains the separate `HERMES_API_SERVER_KEY` GitHub secret.

Supported provider entries currently include OpenRouter, OpenAI, Anthropic, Google Gemini, xAI, DeepSeek, Groq, GitHub Copilot, and the Hermes Telegram bot token.

## Starting

Go to: **GitHub → Actions → Bot Server → Run workflow**
