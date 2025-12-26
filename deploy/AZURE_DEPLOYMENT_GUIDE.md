# Azure VM Deployment Guide for Trading Bot

## Cost with GitHub Student Pack
- **$100 Azure credit** via GitHub Student Developer Pack
- **B1s VM**: ~$7.59/month
- **Free tier**: 750 hours/month of B1s for 12 months (effectively free!)
- **Your cost: $0** for the first year

---

## Part 1: Claim Azure Student Credits

### Step 1.1: Go to GitHub Student Pack
1. Go to **https://education.github.com/pack**
2. Log in with your verified student GitHub account
3. Find **"Microsoft Azure"** in the partner list
4. Click **"Get access"**

### Step 1.2: Activate Azure for Students
1. Or go directly to: **https://azure.microsoft.com/en-us/free/students/**
2. Click **"Start free"**
3. Sign in with your school email (.edu)
4. Verify your student status
5. **$100 credit** + free services activated

---

## Part 2: Create Azure Virtual Machine

### Step 2.1: Go to Azure Portal
1. Go to **https://portal.azure.com**
2. Sign in with your student account

### Step 2.2: Create a Virtual Machine
1. Click **"Create a resource"** (top left, + icon)
2. Click **"Virtual machine"** → **"Create"**

### Step 2.3: Configure Basics Tab

| Setting | Value |
|---------|-------|
| Subscription | Azure for Students |
| Resource group | Click "Create new" → Name it `tradingbot-rg` |
| Virtual machine name | `tradingbot-vm` |
| Region | `(US) East US` or closest to you |
| Availability options | No infrastructure redundancy required |
| Security type | Standard |
| Image | **Ubuntu Server 22.04 LTS - x64 Gen2** |
| VM architecture | x64 |
| Size | Click "See all sizes" → Select **B1s** ($7.59/mo) |

### Step 2.4: Configure Administrator Account

| Setting | Value |
|---------|-------|
| Authentication type | **SSH public key** (recommended) |
| Username | `azureuser` |
| SSH public key source | Generate new key pair |
| Key pair name | `tradingbot-key` |

### Step 2.5: Configure Inbound Ports

| Setting | Value |
|---------|-------|
| Public inbound ports | Allow selected ports |
| Select inbound ports | **SSH (22)** only |

### Step 2.6: Review and Create
1. Click **"Review + create"**
2. Review the summary (should show ~$7.59/month)
3. Click **"Create"**
4. **IMPORTANT**: Download the private key (.pem file) when prompted!
5. Save it somewhere safe (e.g., `C:\Users\YourName\.ssh\tradingbot-key.pem`)

### Step 2.7: Wait for Deployment
- Takes 1-3 minutes
- Click **"Go to resource"** when done
- Note the **Public IP address** (e.g., `20.123.45.67`)

---

## Part 3: Connect to Your VM

### Step 3.1: Open PowerShell
```powershell
cd C:\Users\sidda\Downloads\TradingBot
```

### Step 3.2: Set Key Permissions (Windows)
The .pem key needs restricted permissions. Run in PowerShell as Administrator:
```powershell
$keyPath = "C:\Users\sidda\.ssh\tradingbot-key.pem"
icacls $keyPath /inheritance:r
icacls $keyPath /grant:r "$($env:USERNAME):(R)"
```

### Step 3.3: Connect via SSH
```powershell
ssh -i C:\Users\sidda\.ssh\tradingbot-key.pem azureuser@YOUR_VM_IP
```

Replace `YOUR_VM_IP` with your actual IP address.

### Step 3.4: First Time Connection
- Type `yes` when asked about fingerprint
- You should see the Ubuntu welcome message

---

## Part 4: Upload Your Code

### Step 4.1: From Your Local Machine (New PowerShell Window)
```powershell
# Upload entire TradingBot folder
scp -i C:\Users\sidda\.ssh\tradingbot-key.pem -r C:\Users\sidda\Downloads\TradingBot azureuser@YOUR_VM_IP:~/tradingbot
```

### Step 4.2: Verify Upload (In SSH Session)
```bash
ls ~/tradingbot
# Should show: trading_bot.py, trade_executor.py, main.py, deploy/, etc.
```

---

## Part 5: Run Setup Script

### Step 5.1: Switch to Root (Easier for Setup)
```bash
sudo su -
```

### Step 5.2: Copy Files to Root Home
```bash
cp -r /home/azureuser/tradingbot ~/tradingbot
cd ~/tradingbot/deploy
```

### Step 5.3: Run Setup
```bash
chmod +x setup_azure.sh
./setup_azure.sh
```

This will:
- Install Python, Java, Xvfb
- Download IB Gateway
- Download IBC (auto-login tool)
- Create config files

**Wait for it to complete** (5-10 minutes).

---

## Part 6: Configure IB Credentials

### Step 6.1: Edit Config File
```bash
nano ~/ibc/config.ini
```

### Step 6.2: Update These Lines
```ini
IbLoginId=DU1234567     # Your paper trading username
IbPassword=YourPassword  # Your IB password
TradingMode=paper        # Keep as paper for testing
```

### Step 6.3: Save and Exit
- Press `Ctrl+O` then `Enter` to save
- Press `Ctrl+X` to exit

---

## Part 7: Install Python Requirements

```bash
cd ~/tradingbot
source .venv/bin/activate
pip install -r requirenments.txt
pip install ibapi
```

---

## Part 8: Test IB Gateway (Manual)

### Step 8.1: Start Virtual Display
```bash
export DISPLAY=:1
Xvfb :1 -screen 0 1024x768x24 &
```

### Step 8.2: Start IB Gateway
```bash
cd ~/ibc
./scripts/ibcstart.sh -g --ibc-path=/root/ibc --ibc-ini=/root/ibc/config.ini
```

### Step 8.3: Watch the Output
- Should see "IB Gateway starting..."
- Wait for "IB Gateway is ready"
- If you see login errors, check your credentials in config.ini

### Step 8.4: Stop the Test
- Press `Ctrl+C` to stop
- Kill Xvfb: `pkill Xvfb`

---

## Part 9: Set Up Automatic Services

### Step 9.1: Copy Service Files
```bash
sudo cp ~/tradingbot/deploy/ibgateway.service /etc/systemd/system/
sudo cp ~/tradingbot/deploy/tradingbot.service /etc/systemd/system/
```

### Step 9.2: Reload Systemd
```bash
sudo systemctl daemon-reload
```

### Step 9.3: Enable Auto-Start on Boot
```bash
sudo systemctl enable ibgateway
sudo systemctl enable tradingbot
```

### Step 9.4: Start IB Gateway
```bash
sudo systemctl start ibgateway
```

### Step 9.5: Wait for Gateway to Connect
```bash
# Watch the logs
journalctl -u ibgateway -f
```
Wait until you see it's connected (about 30-60 seconds). Press `Ctrl+C` to exit log view.

### Step 9.6: Start Trading Bot
```bash
sudo systemctl start tradingbot
```

### Step 9.7: Verify Everything is Running
```bash
sudo systemctl status ibgateway
sudo systemctl status tradingbot
```

Both should show **"active (running)"**.

---

## Part 10: Monitor Your Bot

### View Trading Bot Logs
```bash
# Real-time logs
journalctl -u tradingbot -f

# Or view log file
tail -f /var/log/tradingbot.log
```

### View IB Gateway Logs
```bash
journalctl -u ibgateway -f
```

### Check Status
```bash
sudo systemctl status tradingbot
sudo systemctl status ibgateway
```

---

## Useful Commands Reference

| Action | Command |
|--------|---------|
| SSH into VM | `ssh -i ~/.ssh/tradingbot-key.pem azureuser@YOUR_IP` |
| Switch to root | `sudo su -` |
| View bot logs | `journalctl -u tradingbot -f` |
| View gateway logs | `journalctl -u ibgateway -f` |
| Restart bot | `sudo systemctl restart tradingbot` |
| Restart gateway | `sudo systemctl restart ibgateway` |
| Stop everything | `sudo systemctl stop tradingbot ibgateway` |
| Manual sell all | `cd ~/tradingbot && source .venv/bin/activate && python trade_executor.py sell` |
| Check services | `sudo systemctl status tradingbot ibgateway` |

---

## Troubleshooting

### "Connection refused" from trading bot
- IB Gateway not running or not ready yet
- Wait 60 seconds after starting ibgateway
- Check: `sudo systemctl status ibgateway`

### IB Gateway won't start
- Check credentials: `cat ~/ibc/config.ini`
- Check Xvfb: `pgrep Xvfb`
- Manual test: Follow Part 8 steps

### "Permission denied" SSH
- Check key permissions (Step 3.2)
- Make sure using correct username (`azureuser`)

### VM not responding
- Go to Azure Portal → Your VM → Restart

---

## Cost Summary

| Item | Monthly Cost | With Student Credits |
|------|--------------|---------------------|
| B1s VM | $7.59 | **$0** (750 hrs free) |
| Storage (30GB) | ~$1.20 | Included in credits |
| Bandwidth | ~$0.50 | Minimal |
| **Total** | ~$9/month | **$0 for 12+ months** |

Your $100 credit + free tier = **over 1 year free**!

---

## Weekly Schedule (Automated)

Once running, your bot will:

| Day/Time | Action |
|----------|--------|
| Mon-Sun | Sleep, waiting for Monday |
| Monday 4:00 PM CST | Wake up |
| Monday 4:00 PM | Sell all existing positions |
| Monday 4:01 PM | Run trading_bot.py for signals |
| Monday 4:05 PM | Place 8 new bracket orders |
| Monday 4:06 PM | Go back to sleep |

**You never need to touch it** - fully automated!
