#!/bin/bash
# ============================================================
# IBC Gateway Starter Script
# ============================================================
# Custom wrapper to start IB Gateway via IBC with virtual display
# ============================================================

# Set display for headless operation
export DISPLAY=:1

# Start Xvfb if not running
if ! pgrep -x "Xvfb" > /dev/null; then
    echo "Starting Xvfb virtual display..."
    Xvfb :1 -screen 0 1024x768x24 &
    sleep 2
fi

# Path configurations
IBC_PATH="/root/ibc"
TWS_PATH="/root/Jts"
IBC_INI="/root/ibc/config.ini"

# Determine Gateway version (finds the installed version)
GATEWAY_VERSION=$(ls -1 ${TWS_PATH} | grep -E '^[0-9]+$' | sort -n | tail -1)

if [ -z "$GATEWAY_VERSION" ]; then
    echo "ERROR: Could not find IB Gateway installation in ${TWS_PATH}"
    exit 1
fi

echo "Starting IB Gateway version ${GATEWAY_VERSION}..."

# Start IB Gateway via IBC
${IBC_PATH}/scripts/ibcstart.sh ${GATEWAY_VERSION} \
    --gateway \
    --mode=paper \
    --user=${IB_USER:-""} \
    --pw=${IB_PASS:-""} \
    --ibc-path=${IBC_PATH} \
    --ibc-ini=${IBC_INI} \
    --tws-path=${TWS_PATH}
