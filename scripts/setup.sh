#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="$HOME/bot-server"
BOT_DIR="$BASE_DIR/bots"
PANEL_DIR="$BASE_DIR/panel"
HERMES_HOME_DIR="$HOME/.hermes"

LOVE_WHISPERS_DIR="$BOT_DIR/love-whispers-bot"
PACKTOGETHER_DIR="$BOT_DIR/PackTogether"

echo "======================================"
echo "Server setup"
echo "======================================"

mkdir -p "$BOT_DIR"
mkdir -p "$PANEL_DIR"

echo
echo "==> Updating system"
sudo apt-get update
sudo apt-get install -y \
    software-properties-common \
    build-essential \
    curl \
    git \
    wget \
    jq \
    htop \
    neofetch \
    tmux \
    nano \
    vim \
    tree \
    ncdu \
    net-tools \
    iputils-ping \
    dnsutils \
    unzip \
    openssh-server

echo
echo "==> Installing cloudflared"
curl -fsSL --output /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i /tmp/cloudflared.deb || sudo apt-get install -f -y
rm -f /tmp/cloudflared.deb

echo
echo "==> Installing Tailscale"
curl -fsSL https://tailscale.com/install.sh | sh
tailscale version || true

echo
echo "==> Installing Hermes Agent"
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
export PATH="$HOME/.local/bin:$HOME/.hermes/bin:$PATH"
if ! command -v hermes >/dev/null 2>&1; then
    echo "ERROR: Hermes Agent installation did not provide the hermes command."
    exit 1
fi
hermes --version
mkdir -p "$HERMES_HOME_DIR"
chmod 700 "$HERMES_HOME_DIR"

echo
echo "==> Installing Python 3.12"

sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update

sudo apt-get install -y \
    python3.12 \
    python3.12-venv \
    python3.12-dev

python3.12 --version

echo
echo "==> Cloning Love Whispers"

if [ -d "$LOVE_WHISPERS_DIR/.git" ]; then
    echo "Repository already exists."
else
    CLONE_TOKEN="${CLONE_PAT:-${GH_PAT:-}}"
    if [ -z "$CLONE_TOKEN" ]; then
        echo "ERROR: CLONE_PAT is not configured."
        exit 1
    fi

    git clone \
        "https://x-access-token:${CLONE_TOKEN}@github.com/ArashMaghsoodi/love-whispers-bot.git" \
        "$LOVE_WHISPERS_DIR"
fi

echo
echo "==> Cloning PackTogether"

if [ -d "$PACKTOGETHER_DIR/.git" ]; then
    echo "Repository already exists."
else
    git clone \
        https://github.com/ArashMaghsoodi/PackTogether.git \
        "$PACKTOGETHER_DIR"
fi

echo
echo "==> Setting up Panel environment"

cp -r ./panel/* "$PANEL_DIR/" 2>/dev/null || true
cd "$PANEL_DIR"

python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install flask psutil requests
deactivate

echo
echo "==> Creating Love Whispers Python 3.12 environment"

cd "$LOVE_WHISPERS_DIR"

python3.12 -m venv .venv

source .venv/bin/activate

python --version
python -m pip install --upgrade pip

if [ -f requirements.txt ]; then
    pip install -r requirements.txt
else
    echo "WARNING: Love Whispers has no requirements.txt"
fi

deactivate

echo
echo "==> Creating PackTogether Python 3.12 environment"

cd "$PACKTOGETHER_DIR"

python3.12 -m venv .venv

source .venv/bin/activate

python --version
python -m pip install --upgrade pip

if [ -f requirements.txt ]; then
    pip install -r requirements.txt
else
    echo "WARNING: PackTogether has no requirements.txt"
fi

deactivate

echo
echo "==> Creating environment files"

if [ -z "${LOVE_WHISPERS_ENV:-}" ]; then
    echo "ERROR: LOVE_WHISPERS_ENV secret is empty."
    exit 1
fi

if [ -z "${PACKTOGETHER_ENV:-}" ]; then
    echo "ERROR: PACKTOGETHER_ENV secret is empty."
    exit 1
fi

printf '%s\n' "$LOVE_WHISPERS_ENV" > "$LOVE_WHISPERS_DIR/.env"
printf '%s\n' "$PACKTOGETHER_ENV" > "$PACKTOGETHER_DIR/.env"

chmod 600 "$LOVE_WHISPERS_DIR/.env"
chmod 600 "$PACKTOGETHER_DIR/.env"

echo
echo "======================================"
echo "Setup complete"
echo "======================================"

echo
echo "Love Whispers: $LOVE_WHISPERS_DIR"
echo "PackTogether:  $PACKTOGETHER_DIR"
echo "Panel:         $PANEL_DIR"
echo "Hermes home:   $HERMES_HOME_DIR"
