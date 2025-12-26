#!/bin/bash
# ============================================================
# Azure VM Setup Script for Trading Bot
# ============================================================
# Run this script on a fresh Ubuntu 22.04 VM
# Usage: chmod +x setup_azure.sh && ./setup_azure.sh
# ============================================================

set -e  # Exit on error

echo "=========================================="
echo "Trading Bot - Azure VM Setup"
echo "=========================================="

# Update system
echo "[1/8] Updating system..."
sudo apt update && sudo apt upgrade -y

# Install dependencies
echo "[2/8] Installing dependencies..."
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    unzip \
    wget \
    curl \
    openjdk-11-jre \
    xvfb \
    libxslt-dev \
    libxrender1 \
    libxtst6 \
    libxi6 \
    libgtk-3-0 \
    socat

# Create trading bot directory
echo "[3/8] Setting up directories..."
mkdir -p ~/tradingbot
mkdir -p ~/ibc
mkdir -p ~/Jts

# Set up Python virtual environment
echo "[4/8] Setting up Python environment..."
cd ~/tradingbot
python3 -m venv .venv
source .venv/bin/activate

# Install Python packages
pip install --upgrade pip
pip install ibapi

# Download and install IB Gateway
echo "[5/8] Downloading IB Gateway..."
cd ~
# IB Gateway stable version URL
wget -q https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh -O ibgateway-install.sh
chmod +x ibgateway-install.sh

echo "[6/8] Installing IB Gateway..."
echo "When prompted, press ENTER to accept defaults."
echo "Install location should be: /root/Jts"
./ibgateway-install.sh -q -dir ~/Jts

# Download and install IBC
echo "[7/8] Installing IBC (IB Controller)..."
cd ~/ibc
IBC_VERSION="3.18.0"
wget -q "https://github.com/IbcAlpha/IBC/releases/download/${IBC_VERSION}/IBCLinux-${IBC_VERSION}.zip" -O ibc.zip
unzip -o ibc.zip
chmod +x *.sh
rm ibc.zip

# Create IBC config file
echo "[8/8] Creating configuration files..."
cat > ~/ibc/config.ini << 'EOF'
# IBC Configuration
# IMPORTANT: Update these with your IB credentials

# IB Account Credentials
IbLoginId=YOUR_IB_USERNAME
IbPassword=YOUR_IB_PASSWORD

# Trading Mode: paper or live
TradingMode=paper

# Accept incoming API connections
AcceptIncomingConnectionAction=accept

# Accept non-brokerage account warning
AcceptNonBrokerageAccountWarning=yes

# Existing session handling
ExistingSessionDetectedAction=primary

# API Settings
ReadOnlyLogin=no
ReadOnlyApi=no

# Auto-restart settings
ClosedownAt=Saturday 02:00
AllowBlindTrading=yes
StoreSettingsOnServer=no
MinimizeMainWindow=yes
EOF

# Set proper permissions
chmod 600 ~/ibc/config.ini

echo ""
echo "=========================================="
echo "SETUP COMPLETE!"
echo "=========================================="
echo ""
echo "NEXT STEPS:"
echo ""
echo "1. Edit IBC config with your IB credentials:"
echo "   nano ~/ibc/config.ini"
echo ""
echo "2. Upload your trading bot files to ~/tradingbot/"
echo "   (if not already done)"
echo ""
echo "3. Install your Python requirements:"
echo "   cd ~/tradingbot && source .venv/bin/activate"
echo "   pip install -r requirenments.txt"
echo ""
echo "4. Test IB Gateway manually first:"
echo "   export DISPLAY=:1"
echo "   Xvfb :1 -screen 0 1024x768x24 &"
echo "   ~/ibc/scripts/ibcstart.sh -g"
echo ""
echo "5. Set up systemd services:"
echo "   sudo cp ~/tradingbot/deploy/ibgateway.service /etc/systemd/system/"
echo "   sudo cp ~/tradingbot/deploy/tradingbot.service /etc/systemd/system/"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable ibgateway tradingbot"
echo "   sudo systemctl start ibgateway"
echo "   sleep 60"
echo "   sudo systemctl start tradingbot"
echo ""
echo "=========================================="
