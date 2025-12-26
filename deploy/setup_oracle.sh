#!/bin/bash
# ============================================================
# Oracle Cloud Setup Script for Trading Bot
# ============================================================
# Run this script on a fresh Ubuntu 22.04 VM (Always Free Tier)
# Usage: chmod +x setup_oracle.sh && ./setup_oracle.sh
# ============================================================

set -e  # Exit on error

echo "=========================================="
echo "Trading Bot - Oracle Cloud Setup"
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
    socat \
    libxxf86vm1 \
    libgl1-mesa-glx

# Create directories
echo "[3/8] Setting up directories..."
sudo mkdir -p /opt/tradingbot
sudo mkdir -p /opt/ibc
sudo mkdir -p /opt/Jts
sudo chown -R $USER:$USER /opt/tradingbot /opt/ibc /opt/Jts

# Set up Python virtual environment
echo "[4/8] Setting up Python environment..."
cd /opt/tradingbot
python3 -m venv .venv
source .venv/bin/activate

# Install Python packages
pip install --upgrade pip
pip install ibapi

# Download and install IB Gateway
echo "[5/8] Downloading IB Gateway..."
cd /opt
wget -q https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh -O ibgateway-install.sh
chmod +x ibgateway-install.sh

echo "[6/8] Installing IB Gateway (silent mode)..."
# Silent install to /opt/Jts
./ibgateway-install.sh -q -dir /opt/Jts
rm ibgateway-install.sh

# Download and install IBC
echo "[7/8] Installing IBC (IB Controller)..."
cd /opt/ibc
IBC_VERSION="3.18.0"
wget -q "https://github.com/IbcAlpha/IBC/releases/download/${IBC_VERSION}/IBCLinux-${IBC_VERSION}.zip" -O ibc.zip
unzip -o ibc.zip
chmod +x *.sh
chmod +x scripts/*.sh
rm ibc.zip

# Create IBC config file
echo "[8/8] Creating configuration files..."
cat > /opt/ibc/config.ini << 'EOF'
# IBC Configuration for Oracle Cloud
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

# Override TWS API port (paper trading)
OverrideTwsApiPort=7497
EOF

# Set proper permissions
chmod 600 /opt/ibc/config.ini

# Create logs directory
sudo mkdir -p /var/log
sudo touch /var/log/tradingbot.log
sudo chmod 666 /var/log/tradingbot.log

echo ""
echo "=========================================="
echo "SETUP COMPLETE!"
echo "=========================================="
echo ""
echo "NEXT STEPS:"
echo ""
echo "1. Edit IBC config with your IB credentials:"
echo "   nano /opt/ibc/config.ini"
echo ""
echo "2. Copy your trading bot files to /opt/tradingbot/"
echo "   (if not already done)"
echo ""
echo "3. Install your Python requirements:"
echo "   cd /opt/tradingbot && source .venv/bin/activate"
echo "   pip install -r requirenments.txt"
echo ""
echo "4. Copy and enable systemd services:"
echo "   sudo cp /opt/tradingbot/deploy/oracle-ibgateway.service /etc/systemd/system/ibgateway.service"
echo "   sudo cp /opt/tradingbot/deploy/oracle-tradingbot.service /etc/systemd/system/tradingbot.service"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable ibgateway tradingbot"
echo ""
echo "5. Start services:"
echo "   sudo systemctl start ibgateway"
echo "   sleep 60"
echo "   sudo systemctl start tradingbot"
echo ""
echo "=========================================="
