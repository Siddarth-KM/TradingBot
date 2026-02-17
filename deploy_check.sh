#!/bin/bash
# Deployment verification and setup script for tradingbot
set -e

echo "=========================================="
echo "STEP 1: Verify Python imports"
echo "=========================================="
cd /opt/tradingbot

echo "Testing massive_api import..."
/opt/tradingbot/.venv/bin/python -c "import massive_api; print('massive_api OK')" 2>&1 || echo "FAILED: massive_api"

echo ""
echo "Testing trade_executor import..."
/opt/tradingbot/.venv/bin/python -c "import trade_executor; print('trade_executor OK')" 2>&1 || echo "FAILED: trade_executor"

echo ""
echo "=========================================="
echo "STEP 2: Check trade_executor schedule constants"
echo "=========================================="
/opt/tradingbot/.venv/bin/python -c "
import trade_executor as te
print(f'LIMIT_ORDER_HOUR = {te.LIMIT_ORDER_HOUR}')
print(f'LIMIT_ORDER_DAY = {te.LIMIT_ORDER_DAY}')
print(f'MARKET_ORDER_HOUR = {te.MARKET_ORDER_HOUR}')
print(f'MARKET_ORDER_DAY = {te.MARKET_ORDER_DAY}')
" 2>&1 || echo "FAILED: trade_executor constants"

echo ""
echo "=========================================="
echo "STEP 3: Check existing systemd service"
echo "=========================================="
cat /etc/systemd/system/tradingbot.service 2>/dev/null || echo "No tradingbot.service found"

echo ""
echo "=========================================="
echo "STEP 4: Check existing crontab"
echo "=========================================="
crontab -l 2>/dev/null || echo "No crontab found"

echo ""
echo "=========================================="
echo "STEP 5: Check ibgateway service"  
echo "=========================================="
sudo systemctl status ibgateway --no-pager 2>/dev/null | head -5 || echo "ibgateway not found"

echo ""
echo "=========================================="
echo "STEP 6: Check tradingbot service status"
echo "=========================================="
sudo systemctl status tradingbot --no-pager 2>/dev/null | head -5 || echo "tradingbot service inactive/not found"

echo ""
echo "=========================================="
echo "STEP 7: Check current timezone"
echo "=========================================="
timedatectl | grep "Time zone"

echo ""
echo "=========================================="
echo "DONE - All checks complete"
echo "=========================================="
