#!/bin/bash
# ============================================================
# Raspberry Pi 5 — Prediction Sandbox Setup
# Run once on a fresh Raspberry Pi OS Lite 64-bit install.
# ============================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="prediction-pipeline"

echo "=== Prediction Sandbox Pi Setup ==="
echo "Repo dir: $REPO_DIR"

# ── System dependencies ──────────────────────────────────────
echo "[1/5] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y python3.11 python3.11-venv python3-pip git ufw

# ── Python venv ──────────────────────────────────────────────
echo "[2/5] Setting up Python virtual environment..."
python3.11 -m venv "$REPO_DIR/.venv"
"$REPO_DIR/.venv/bin/pip" install --upgrade pip
"$REPO_DIR/.venv/bin/pip" install -e "$REPO_DIR[dev]"

# ── Environment file ─────────────────────────────────────────
echo "[3/5] Checking .env..."
if [ ! -f "$REPO_DIR/.env" ]; then
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    echo "  Created .env from .env.example — edit it to add API keys!"
else
    echo "  .env already exists, skipping"
fi

# ── Firewall ─────────────────────────────────────────────────
echo "[4/5] Configuring UFW firewall..."
sudo ufw --force enable
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
# Dashboard (Phase 2) — only allow from local network
# sudo ufw allow from 192.168.0.0/16 to any port 8000

# ── Systemd service ──────────────────────────────────────────
echo "[5/5] Installing systemd timer..."
PYTHON="$REPO_DIR/.venv/bin/python"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
TIMER_FILE="/etc/systemd/system/${SERVICE_NAME}.timer"

sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=Prediction Market Pipeline — single cycle
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$REPO_DIR
ExecStart=$PYTHON scripts/run_pipeline.py
StandardOutput=journal
StandardError=journal
EOF

sudo tee "$TIMER_FILE" > /dev/null << EOF
[Unit]
Description=Run prediction pipeline every 30 minutes

[Timer]
OnBootSec=60
OnUnitActiveSec=30min
Unit=${SERVICE_NAME}.service

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}.timer"
sudo systemctl start "${SERVICE_NAME}.timer"

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "  1. Edit $REPO_DIR/.env and add your API keys"
echo "  2. Run: $PYTHON scripts/check_health.py"
echo "  3. Check logs: journalctl -u $SERVICE_NAME -f"
echo "  4. Check timer: systemctl list-timers $SERVICE_NAME"
