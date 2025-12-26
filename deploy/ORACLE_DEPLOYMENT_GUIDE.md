# Oracle Cloud Deployment Guide for Trading Bot

## Cost: FREE FOREVER

Oracle Cloud Free Tier includes:
- **2 AMD VMs** (1GB RAM each) - **Always Free**
- **200GB block storage** - **Always Free**
- **No credit card charges** - Just verification

---

## Part 1: Create Oracle Cloud Account

### Step 1.1: Sign Up
1. Go to **https://www.oracle.com/cloud/free/**
2. Click **"Start for free"**
3. Fill in your details:
   - Email address
   - Country (United States)
   - First/Last name
4. Click **"Verify my email"**
5. Check your email and click the verification link

### Step 1.2: Complete Registration
1. Set your password
2. Enter your address
3. **Add a payment method** (credit/debit card)
   - This is for **verification only**
   - You will **NOT be charged** for Always Free resources
4. Select your **Home Region** (choose closest to you):
   - US East (Ashburn) - Recommended for US
   - US West (Phoenix)
   - US West (San Jose)
5. Click **"Start my free trial"**

### Step 1.3: Wait for Account
- Account provisioning takes **5-30 minutes**
- You'll get an email when ready
- Sign in at **https://cloud.oracle.com**

---

## Part 2: Create a Virtual Machine

### Step 2.1: Access Compute
1. Log into **https://cloud.oracle.com**
2. Click the **hamburger menu** (☰) top left
3. Click **Compute** → **Instances**
4. Click **"Create instance"**

### Step 2.2: Configure Instance

#### Name and Compartment
| Setting | Value |
|---------|-------|
| Name | `tradingbot` |
| Compartment | (leave default) |

#### Placement (leave defaults)

#### Image and Shape - IMPORTANT!
1. Click **"Edit"** in the Image and shape section
2. Click **"Change image"**
3. Select **"Canonical Ubuntu"** → **"22.04"**
4. Click **"Select image"**

5. Click **"Change shape"**
6. Select **"AMD"** (not Ampere/ARM!)
7. Select **"VM.Standard.E2.1.Micro"** (Always Free)
   - 1 OCPU, 1 GB RAM
8. Click **"Select shape"**

#### Networking
1. Click **"Edit"** in Networking section
2. Select **"Create new virtual cloud network"** (or use existing)
3. Select **"Create new public subnet"**
4. Check **"Assign a public IPv4 address"** ✅

#### Add SSH Keys
1. Select **"Generate a key pair for me"**
2. Click **"Save private key"** - SAVE THIS FILE!
   - Save as `oracle-tradingbot.key`
   - Save to `C:\Users\sidda\.ssh\`
3. Also click **"Save public key"** (backup)

### Step 2.3: Create the Instance
1. Click **"Create"**
2. Wait 2-5 minutes for it to provision
3. Status will change to **"Running"**
4. Note the **Public IP address** (e.g., `129.146.xxx.xxx`)

---

## Part 3: Configure Firewall (Oracle Security List)

Oracle blocks all ports by default. We don't need to open any for our use case since everything runs locally, but let's make sure SSH works.

### Step 3.1: Verify SSH Access
1. On your instance page, click **"Virtual cloud network"** link
2. Click **"Security Lists"** in the left menu
3. Click the **default security list**
4. Verify there's an **Ingress Rule** for:
   - Source: `0.0.0.0/0`
   - Protocol: TCP
   - Destination Port: `22`
   
(This should exist by default)

---

## Part 4: Connect to Your VM

### Step 4.1: Set Key Permissions (Windows PowerShell)
```powershell
$keyPath = "C:\Users\sidda\Downloads\oracle-tradingbot.key"
icacls $keyPath /inheritance:r
icacls $keyPath /grant:r "$($env:USERNAME):(R)"
```

### Step 4.2: Connect via SSH
```powershell
ssh -i "C:\Users\sidda\Downloads\oracle-tradingbot.key" ubuntu@YOUR_VM_IP
```

Replace `YOUR_VM_IP` with your actual IP address.

### Step 4.3: First Connection
- Type `yes` when asked about fingerprint
- You should see Ubuntu welcome message

---

## Part 5: Upload Your Code

### Step 5.1: From Your Local Machine (New PowerShell Window)
```powershell
scp -i "C:\Users\sidda\Downloads\oracle-tradingbot.key" -r C:\Users\sidda\Downloads\TradingBot ubuntu@YOUR_VM_IP:~/tradingbot
```

### Step 5.2: Verify Upload (In SSH Session)
```bash
ls ~/tradingbot
# Should show: trading_bot.py, trade_executor.py, main.py, deploy/, etc.
```

---

## Part 6: Run Setup Script

### Step 6.1: Copy to /opt (Standard Location)
```bash
sudo cp -r ~/tradingbot /opt/tradingbot
sudo chown -R ubuntu:ubuntu /opt/tradingbot
```

### Step 6.2: Run Setup Script
```bash
cd /opt/tradingbot/deploy
chmod +x setup_oracle.sh
./setup_oracle.sh
```

**This takes 5-10 minutes.** It will:
- Install Python, Java, Xvfb
- Download IB Gateway
- Download IBC (auto-login tool)
- Create configuration files

---

## Part 7: Configure IB Credentials

### Step 7.1: Edit Config File
```bash
nano /opt/ibc/config.ini
```

### Step 7.2: Update These Lines
```ini
IbLoginId=DU1234567      # Your paper trading username
IbPassword=YourPassword   # Your IB password
TradingMode=paper         # Keep as paper for testing
```

### Step 7.3: Save and Exit
- Press `Ctrl+O` then `Enter` to save
- Press `Ctrl+X` to exit

---

## Part 8: Install Python Requirements

```bash
cd /opt/tradingbot
source .venv/bin/activate
pip install -r requirenments.txt
pip install ibapi
```

---

## Part 9: Test IB Gateway Manually (Optional but Recommended)

### Step 9.1: Start Virtual Display
```bash
export DISPLAY=:1
Xvfb :1 -screen 0 1024x768x24 &
```

### Step 9.2: Start IB Gateway
```bash
/opt/ibc/scripts/ibcstart.sh -g --tws-path=/opt/Jts --ibc-path=/opt/ibc --ibc-ini=/opt/ibc/config.ini
```

### Step 9.3: Watch Output
- Wait for "IB Gateway is ready" message
- If login errors, check credentials in config.ini

### Step 9.4: Stop Test
- Press `Ctrl+C`
- Kill Xvfb: `pkill Xvfb`

---

## Part 10: Set Up Automatic Services

### Step 10.1: Copy Service Files
```bash
sudo cp /opt/tradingbot/deploy/oracle-ibgateway.service /etc/systemd/system/ibgateway.service
sudo cp /opt/tradingbot/deploy/oracle-tradingbot.service /etc/systemd/system/tradingbot.service
```

### Step 10.2: Reload Systemd
```bash
sudo systemctl daemon-reload
```

### Step 10.3: Enable Auto-Start on Boot
```bash
sudo systemctl enable ibgateway
sudo systemctl enable tradingbot
```

### Step 10.4: Start IB Gateway
```bash
sudo systemctl start ibgateway
```

### Step 10.5: Wait and Check Logs
```bash
# Watch gateway startup (wait 60-90 seconds)
journalctl -u ibgateway -f
```
Wait until you see it's connected. Press `Ctrl+C` to exit.

### Step 10.6: Start Trading Bot
```bash
sudo systemctl start tradingbot
```

### Step 10.7: Verify Both Running
```bash
sudo systemctl status ibgateway
sudo systemctl status tradingbot
```

Both should show **"active (running)"**.

---

## Part 11: Monitor Your Bot

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

### Quick Status Check
```bash
sudo systemctl status tradingbot ibgateway
```

---

## Useful Commands Reference

| Action | Command |
|--------|---------|
| SSH into VM | `ssh -i "C:\Users\sidda\Downloads\oracle-tradingbot.key" ubuntu@YOUR_IP` |
| SSH into VM | `ssh -i ~/.ssh/oracle-tradingbot.key ubuntu@YOUR_IP` |
| View bot logs | `journalctl -u tradingbot -f` |
| View gateway logs | `journalctl -u ibgateway -f` |
| Restart bot | `sudo systemctl restart tradingbot` |
| Restart gateway | `sudo systemctl restart ibgateway` |
| Stop everything | `sudo systemctl stop tradingbot ibgateway` |
| Start everything | `sudo systemctl start ibgateway && sleep 60 && sudo systemctl start tradingbot` |
| Check status | `sudo systemctl status tradingbot ibgateway` |
| Manual sell all | `cd /opt/tradingbot && source .venv/bin/activate && python trade_executor.py sell` |
| View all logs | `tail -100 /var/log/tradingbot.log` |

---

## Troubleshooting

### "Connection refused" from trading bot
```bash
# Check if gateway is running
sudo systemctl status ibgateway

# Restart gateway and wait
sudo systemctl restart ibgateway
sleep 90
sudo systemctl restart tradingbot
```

### IB Gateway won't start
```bash
# Check credentials
cat /opt/ibc/config.ini

# Check if Xvfb is running
pgrep Xvfb

# Check gateway logs
journalctl -u ibgateway -n 50
```

### Out of memory
Oracle free tier has only 1GB RAM. If issues:
```bash
# Check memory
free -h

# Create swap file (adds virtual memory)
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### VM stops responding
1. Go to Oracle Cloud Console
2. Click your instance
3. Click **"Reboot"** or **"Stop"** then **"Start"**

---

## Weekly Schedule (Automated)

Once running, your bot operates automatically:

| Day/Time | Action |
|----------|--------|
| Mon-Sun | Sleeps, waiting for Monday |
| Monday 4:00 PM CST | Wakes up |
| Monday 4:00 PM | Sells all existing positions |
| Monday 4:01 PM | Runs trading_bot.py for signals |
| Monday 4:05 PM | Places 8 new bracket orders |
| Monday 4:06 PM | Goes back to sleep |

**Fully automated - no intervention needed!**

---

## Cost Summary

| Item | Cost |
|------|------|
| VM.Standard.E2.1.Micro | **$0 forever** |
| 50GB Boot Volume | **$0 forever** |
| Bandwidth (10TB/month) | **$0 forever** |
| **Total** | **$0 forever** |

This is genuinely free, not a trial. Oracle's "Always Free" tier never expires.

---

## Security Notes

1. **SSH key only** - Password auth is disabled by default
2. **No ports exposed** - IB Gateway only listens on localhost:7497
3. **Credentials secured** - config.ini is chmod 600
4. **Paper trading first** - Test thoroughly before switching to live
