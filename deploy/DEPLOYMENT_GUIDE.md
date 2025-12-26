# ============================================================
# DigitalOcean Deployment Guide for Trading Bot
# ============================================================

## Quick Start

### 1. Create DigitalOcean Droplet
- Go to DigitalOcean → Create → Droplets
- Choose: **Ubuntu 22.04 LTS**
- Plan: **Basic $6/mo** (1GB RAM, 1 vCPU) - sufficient for trading bot
- Datacenter: Choose closest to you (or New York for US markets)
- Authentication: SSH Key (recommended) or Password

### 2. Connect to Your Droplet
```bash
ssh root@YOUR_DROPLET_IP
```

### 3. Upload Trading Bot Files
From your local machine (PowerShell):
```powershell
# Using scp to upload entire folder
scp -r C:\Users\sidda\Downloads\TradingBot root@YOUR_DROPLET_IP:~/tradingbot
```

Or use FileZilla/WinSCP for GUI file transfer.

### 4. Run Setup Script
```bash
cd ~/tradingbot/deploy
chmod +x setup_droplet.sh
./setup_droplet.sh
```

### 5. Configure IB Credentials
```bash
nano ~/ibc/config.ini
```
Update these lines with your IB paper trading credentials:
```
IbLoginId=YOUR_IB_USERNAME
IbPassword=YOUR_IB_PASSWORD
TradingMode=paper
```

### 6. Install Python Requirements
```bash
cd ~/tradingbot
source .venv/bin/activate
pip install -r requirements.txt
pip install ibapi
```

### 7. Test IB Gateway Manually First
```bash
# Start virtual display
Xvfb :1 -screen 0 1024x768x24 &
export DISPLAY=:1

# Start gateway via IBC
~/ibc/gatewaystart.sh -inline
```
Wait for "IB Gateway is ready" message. Press Ctrl+C to stop.

### 8. Set Up Auto-Start Services
```bash
# Copy service files
sudo cp ~/tradingbot/deploy/ibgateway.service /etc/systemd/system/
sudo cp ~/tradingbot/deploy/tradingbot.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable services to start on boot
sudo systemctl enable ibgateway
sudo systemctl enable tradingbot

# Start IB Gateway first
sudo systemctl start ibgateway

# Wait 60 seconds for Gateway to fully initialize
sleep 60

# Start trading bot
sudo systemctl start tradingbot
```

### 9. Check Status
```bash
# Check if services are running
sudo systemctl status ibgateway
sudo systemctl status tradingbot

# View trading bot logs
tail -f /var/log/tradingbot.log

# View all recent logs
journalctl -u tradingbot -f
journalctl -u ibgateway -f
```

---

## Maintenance Commands

### Restart Services
```bash
sudo systemctl restart ibgateway
sudo systemctl restart tradingbot
```

### Stop Everything
```bash
sudo systemctl stop tradingbot
sudo systemctl stop ibgateway
```

### Update Trading Bot Code
```bash
# Stop bot
sudo systemctl stop tradingbot

# Upload new files from local machine
scp -r C:\Users\sidda\Downloads\TradingBot\*.py root@YOUR_DROPLET_IP:~/tradingbot/

# Restart
sudo systemctl start tradingbot
```

### View Logs
```bash
# Real-time trading bot log
tail -f /var/log/tradingbot.log

# Last 100 lines
tail -100 /var/log/tradingbot.log

# Search for errors
grep -i error /var/log/tradingbot.log
```

---

## Troubleshooting

### IB Gateway Won't Start
```bash
# Check if Xvfb is running
pgrep Xvfb

# Start it manually if not
Xvfb :1 -screen 0 1024x768x24 &
export DISPLAY=:1

# Check IBC config
cat ~/ibc/config.ini
```

### Connection Refused (Port 7497)
- IB Gateway not running or not fully started
- Wait longer after starting Gateway (can take 30-60 seconds)
- Check Gateway logs: `journalctl -u ibgateway`

### Auto-Login Failing
- Verify credentials in `~/ibc/config.ini`
- Check if IB account has API access enabled
- Paper trading must be enabled on your IB account

### Bot Not Trading
```bash
# Check if bot is running
sudo systemctl status tradingbot

# Check logs for errors
tail -50 /var/log/tradingbot.log

# Manually test
cd ~/tradingbot
source .venv/bin/activate
python trade_executor.py schedule
```

---

## Security Notes

1. **Keep IB credentials secure** - config.ini contains your password
   ```bash
   chmod 600 ~/ibc/config.ini
   ```

2. **Use paper trading first** - test thoroughly before switching to live

3. **Set up firewall** (optional but recommended):
   ```bash
   sudo ufw allow ssh
   sudo ufw enable
   ```
   No need to open port 7497 - it's localhost only

4. **Enable DigitalOcean backups** - $1.20/mo for weekly snapshots

---

## Switching to Live Trading

⚠️ **Only after extensive paper trading testing!**

1. Edit IBC config:
   ```bash
   nano ~/ibc/config.ini
   # Change: TradingMode=live
   ```

2. Update trade_executor.py to use live port:
   ```python
   # Change PAPER_TRADING_PORT to LIVE_TRADING_PORT in the code
   ```

3. Restart services:
   ```bash
   sudo systemctl restart ibgateway
   sleep 60
   sudo systemctl restart tradingbot
   ```
