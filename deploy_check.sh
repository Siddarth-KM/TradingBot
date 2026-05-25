#!/bin/bash
# Deployment verification for tradingbot (signal-gen only)
set -e

echo "=========================================="
echo "STEP 1: Verify Python imports"
echo "=========================================="
cd /opt/tradingbot

echo "Testing trading_bot import..."
/opt/tradingbot/.venv/bin/python -c "import trading_bot; print('trading_bot OK')" 2>&1 || echo "FAILED: trading_bot"

echo "Testing massive_api import..."
/opt/tradingbot/.venv/bin/python -c "import massive_api; print('massive_api OK')" 2>&1 || echo "FAILED: massive_api"

echo "Confirming ibapi is NOT installed (it should not be)..."
/opt/tradingbot/.venv/bin/python -c "import ibapi" 2>&1 && echo "WARN: ibapi is still installed" || echo "ibapi correctly absent"

echo ""
echo "=========================================="
echo "STEP 2: Check signal-gen systemd unit + timer"
echo "=========================================="
cat /etc/systemd/system/tradingbot-signals.service 2>/dev/null || echo "No tradingbot-signals.service found"
echo "---"
cat /etc/systemd/system/tradingbot-signals.timer 2>/dev/null || echo "No tradingbot-signals.timer found"

echo ""
echo "=========================================="
echo "STEP 3: Check timer status and next firing"
echo "=========================================="
systemctl list-timers tradingbot-signals.timer --no-pager 2>/dev/null || echo "Timer not active"

echo ""
echo "=========================================="
echo "STEP 4: Check current timezone"
echo "=========================================="
timedatectl | grep "Time zone"

echo ""
echo "=========================================="
echo "STEP 5: Confirm legacy IB services are gone"
echo "=========================================="
systemctl status ibgateway --no-pager 2>/dev/null | head -3 || echo "ibgateway not present (expected)"
systemctl status tradingbot.service --no-pager 2>/dev/null | head -3 || echo "old tradingbot.service not present (expected)"

echo ""
echo "=========================================="
echo "DONE - All checks complete"
echo "=========================================="
