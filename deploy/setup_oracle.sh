#!/bin/bash
# ============================================================
# Oracle Cloud Setup Script for INDEX-LAB signal generation
# ============================================================
# Run this on a fresh Ubuntu 22.04 VM (Always Free Tier).
# Signal-gen only: no IB Gateway, no broker connection.
# Execution is manual; see archive/README.md for rationale.
# Usage: chmod +x setup_oracle.sh && ./setup_oracle.sh
# ============================================================

set -e

echo "=========================================="
echo "INDEX-LAB - Oracle Cloud Setup (signal-gen)"
echo "=========================================="

echo "[1/4] Updating system..."
sudo apt update && sudo apt upgrade -y

echo "[2/4] Installing dependencies..."
sudo apt install -y python3 python3-pip python3-venv unzip wget curl

echo "[3/4] Setting up directories..."
sudo mkdir -p /opt/tradingbot
sudo chown -R "$USER":"$USER" /opt/tradingbot

echo "[4/4] Setting up Python environment..."
cd /opt/tradingbot
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

sudo mkdir -p /var/log
sudo touch /var/log/tradingbot.log
sudo chmod 666 /var/log/tradingbot.log

echo ""
echo "=========================================="
echo "SETUP COMPLETE"
echo "=========================================="
echo ""
echo "NEXT STEPS:"
echo ""
echo "1. scp the repo into /opt/tradingbot/ from your local box."
echo ""
echo "2. Install Python requirements:"
echo "   cd /opt/tradingbot && source .venv/bin/activate"
echo "   pip install -r requirements.txt"
echo ""
echo "3. Install the systemd unit + timer:"
echo "   sudo cp /opt/tradingbot/deploy/tradingbot-signals.service /etc/systemd/system/"
echo "   sudo cp /opt/tradingbot/deploy/tradingbot-signals.timer /etc/systemd/system/"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable --now tradingbot-signals.timer"
echo ""
echo "4. Verify next scheduled run:"
echo "   systemctl list-timers tradingbot-signals.timer --no-pager"
echo ""
echo "5. Optional smoke-test (forces one immediate run):"
echo "   sudo systemctl start tradingbot-signals.service"
echo "   tail -100 /opt/tradingbot/logs/signal_generation.log"
echo ""
echo "=========================================="
