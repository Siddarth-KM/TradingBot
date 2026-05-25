# Oracle Cloud Deployment — INDEX-LAB Signal Generation

This guide deploys the INDEX-LAB **signal generator** to an Oracle Cloud Ubuntu VM. It is signal-gen only — there is no IB Gateway, no broker connection, and no automated order placement. Execution is performed manually from the generated `signals.json` (see [archive/README.md](../archive/README.md) for the rationale).

## Prerequisites
- Oracle Cloud account (Always Free Tier is sufficient)
- Ubuntu 22.04 VM (1 GB RAM is enough for signal-gen alone)
- SSH key configured

## Part 1 — Initial Setup

SSH to the VM:
```bash
ssh -i ~/.ssh/oracle-tradingbot.key ubuntu@YOUR_VM_IP
```

Run the bootstrap script:
```bash
git clone <this repo> /tmp/tb && sudo cp -r /tmp/tb/* /opt/tradingbot/ 2>/dev/null || true
# Or scp the repo from your local box per CLAUDE.md (use scp, never rsync)
cd /opt/tradingbot/deploy
chmod +x setup_oracle.sh
./setup_oracle.sh
```

This installs Python 3, sets up a venv at `/opt/tradingbot/.venv`, and prepares `/var/log/tradingbot.log`.

## Part 2 — Install Python Requirements

```bash
cd /opt/tradingbot
source .venv/bin/activate
pip install -r requirements.txt
```

`ibapi` is intentionally NOT a requirement.

## Part 3 — Validate Before Enabling the Timer

```bash
cd /opt/tradingbot
source .venv/bin/activate
python validate_deploy.py
```

Must exit 0 with `VALIDATION PASSED`.

Optional one-shot signal-gen test:
```bash
bash /opt/tradingbot/run_signals.sh
ls -lh /opt/tradingbot/signals/current_signals.json
```

## Part 4 — Install Systemd Unit + Timer

```bash
sudo cp /opt/tradingbot/deploy/tradingbot-signals.service /etc/systemd/system/
sudo cp /opt/tradingbot/deploy/tradingbot-signals.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tradingbot-signals.timer
systemctl list-timers tradingbot-signals.timer --no-pager
```

The last command must show the next firing on a Monday at 16:00 America/Chicago.

## Part 5 — Smoke Test the Service

Force an immediate run (independent of the timer):
```bash
sudo systemctl start tradingbot-signals.service
sudo journalctl -u tradingbot-signals.service -n 200 --no-pager
tail -100 /opt/tradingbot/logs/signal_generation.log
ls -lh /opt/tradingbot/signals/current_signals.json
```

Confirm `current_signals.json` mtime is within the last 5 minutes and the file is non-empty.

## Useful Commands

| Action | Command |
|--------|---------|
| SSH into VM | `ssh -i ~/.ssh/oracle-tradingbot.key ubuntu@YOUR_IP` |
| Service status | `systemctl status tradingbot-signals.timer tradingbot-signals.service` |
| Force a run now | `sudo systemctl start tradingbot-signals.service` |
| View timer schedule | `systemctl list-timers tradingbot-signals.timer --no-pager` |
| Live log tail | `tail -f /opt/tradingbot/logs/signal_generation.log` |
| Systemd journal | `journalctl -u tradingbot-signals.service -n 200 --no-pager` |
| Disable scheduled runs | `sudo systemctl disable --now tradingbot-signals.timer` |

## Troubleshooting

### Signal-gen didn't fire on Monday
```bash
systemctl list-timers tradingbot-signals.timer --no-pager
journalctl -u tradingbot-signals.service --since "last monday" --no-pager
```
The shell script also writes detailed output to `/opt/tradingbot/logs/signal_generation.log`.

### Memory pressure (VM has only 1 GB)
Per CLAUDE.md:
```bash
free -m
sudo sync && sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'
```
`run_signals.sh` already includes a 150 MB pre-flight check and a one-shot drop_caches retry.

### Rollback to the prior IB-execution build
The archived executor lives at `/opt/tradingbot/archive/trade_executor.py`. To restore it as the active path:
```bash
cp /opt/tradingbot/archive/trade_executor.py /opt/tradingbot/
git -C /opt/tradingbot show <prior-commit>:deploy/oracle-tradingbot.service | sudo tee /etc/systemd/system/tradingbot.service
sudo systemctl daemon-reload && sudo systemctl enable --now tradingbot.service
pip install ibapi
```
Then re-enable IB Gateway separately. The kill decision is documented in the council session of 2026-05-25.
