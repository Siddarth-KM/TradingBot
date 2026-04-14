# CLAUDE.md — INDEX-LAB TradingBot

## Project Overview
Live swing trading system (INDEX-LAB) running on an Oracle Cloud VM.
- **Backend**: Python (signal generation, Kelly Criterion sizing, scheduler)
- **Deployment target**: Oracle Cloud VM (Linux), ~1GB RAM
- **Local environment**: Windows
- **Version control**: GitHub
- **Output**: `signals.json` (weekly, consumed by downstream processes)

---

## Environment Rules

### File Transfers
- **Always use `scp`**, never `rsync` — rsync is not available on Windows
- Syntax: `scp -i <key> <local_file> ubuntu@<vm_ip>:<remote_path>`
- After every transfer, SSH in and verify files arrived with `ls -lh <remote_path>`

### SSH Access
- Always confirm SSH connection before issuing remote commands
- If connection times out, check Oracle Cloud security list rules before debugging elsewhere

---

## VM Process Management (Most Common Failure Source)

### Before Every Deployment or Signal Run
Always kill stale scheduler processes first:
```bash
ps aux | grep scheduler | grep -v grep
kill -9 <pid>  # kill all matches, not just one
```
Then verify clean state:
```bash
ps aux | grep scheduler | grep -v grep  # should return nothing
free -m  # check available memory — need >200MB free to run safely
```

### Memory Constraints
- VM has ~1GB RAM total; signal generation needs headroom
- If `free -m` shows <200MB available: `sudo sync && sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'`
- If still constrained, identify and kill the heaviest non-essential process before proceeding

### Process Management (Permanent Fix)
Run the scheduler as a systemd service, not a raw process, to prevent duplicate spawning:
```bash
# Check service status
sudo systemctl status tradingbot-scheduler

# Restart cleanly
sudo systemctl restart tradingbot-scheduler

# View logs
sudo journalctl -u tradingbot-scheduler -n 50
```
If systemd service is not yet configured, ask Claude to set one up — this eliminates the duplicate process problem permanently.

---

## signals.json — Field Mapping (VM → Agents)

The VM's `signals.json` uses different field names than the agent prompts. Apply this mapping whenever agents or scripts read signals:

| VM field name          | Agent/prompt name | Notes                              |
|------------------------|-------------------|------------------------------------|
| `predicted_return`     | `pred`            | Decimal (e.g. 0.044 = 4.4%)      |
| `direction_probability`| `probability`     | Already a % value (0–100)         |
| `last_close`           | `buy_price`       | Placeholder in MODE A              |
| `limit_sell`           | `sell_limit`      | Pre-calculated target price        |
| `timestamp`            | `generated_at`    | ISO 8601 string                    |
| *(hardcode)*           | `timeframe`       | Always 5 (days)                    |
| *(derived)*            | `week_number`     | From `agents/last_sent_week.txt`   |
| *(derived)*            | `date_range`      | Monday–Friday of signal week       |
| *(user fills)*         | `percent_allocation` | Left blank by agents; user enters after trading |

Signal data lives in `all_signals` array at the top level of signals.json.

---

## signals.json — Required Schema and Validation

Every entry in `signals.json` must have:
```json
{
  "ticker": "string",
  "date": "YYYY-MM-DD (current trading week)",
  "price": "float (non-zero, within 5% of market close)",
  "signal": "string (BUY | SELL | HOLD)",
  "percent_allocation": "float (all entries must sum to 100.0)"
}
```

### Mandatory Post-Generation Validation
Run this after every signal generation before committing or deploying:
```python
import json, sys
from datetime import datetime, timedelta

with open('signals.json') as f:
    data = json.load(f)

signals = data.get('signals', [])
errors = []

# Check percent_allocation exists and sums to 100
allocations = []
for s in signals:
    if 'percent_allocation' not in s:
        errors.append(f"Missing percent_allocation: {s.get('ticker')}")
    else:
        allocations.append(s['percent_allocation'])

alloc_sum = sum(allocations)
if not (99.0 <= alloc_sum <= 101.0):
    errors.append(f"percent_allocation sums to {alloc_sum}, expected ~100")

# Check dates are current week
today = datetime.today()
week_start = today - timedelta(days=today.weekday() + 4)  # last Friday
for s in signals:
    sig_date = datetime.strptime(s['date'], '%Y-%m-%d')
    if sig_date < week_start:
        errors.append(f"Stale date for {s['ticker']}: {s['date']}")

# Check prices are non-zero
for s in signals:
    if s.get('price', 0) == 0:
        errors.append(f"Zero price for {s['ticker']}")

if errors:
    print("VALIDATION FAILED:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print(f"signals.json OK — {len(signals)} signals, allocation sum: {alloc_sum:.1f}%")
```

Never push to GitHub or deploy to VM if this script exits with code 1.

---

## Deployment Workflow (Full Sequence)

Follow this exact order every time:

```
1. Kill stale scheduler processes on VM (ps aux | grep scheduler)
2. Check VM memory (free -m)
3. scp updated files to VM
4. SSH in, verify files arrived
5. Start scheduler (systemd service preferred)
6. Wait 60 seconds
7. Run signals.json validation script
8. If validation passes → push to GitHub
9. If validation fails → diagnose, fix, re-run from step 5
```

Do not skip step 7. Do not push to GitHub before validating.

---

## Kelly Criterion Implementation Notes

Three portfolio-level Kelly frameworks are implemented (not position-level):
- **Opportunity-Adjusted Kelly**: scales weekly deployment by signal count, avg ML confidence, and market regime
- **Edge-Based Deployment**: derives Kelly from aggregate portfolio Sharpe across signals
- **Regime-Conditional Kelly**: detects market regime (4 categories) and applies regime-specific bounds

When modifying Kelly code, always test with historical signal data before deploying live. Never adjust Kelly parameters mid-week during an active deployment.

---

## Known Issues & Root Causes

| Issue | Root Cause | Fix |
|-------|-----------|-----|
| Duplicate scheduler processes | Scheduler not managed by systemd; manual restarts stack | Convert to systemd service |
| Stale dates in signals.json | Generation using cached data or wrong datetime reference | Always pass `datetime.today()` explicitly, never rely on module-level constants |
| Missing `percent_allocation` field | Field added after base schema, not in all code paths | Run validation script post-generation |
| rsync fails on Windows | rsync not natively available on Windows | Always use scp |
| Signal generation timeout | Memory exhaustion from duplicate processes | Kill all scheduler processes before starting; check free -m |

---

## GitHub Workflow

- Commit message format: `[DEPLOY] <description>` for production pushes, `[FIX] <description>` for patches
- Never force-push to main
- Always pull before pushing: `git pull origin main` first
- Only push after signals.json validation passes

---

## Interview-Critical Metrics (Do Not Modify Without Noting Changes)

These figures are on the resume and must be reproducible:
- **26-week live period**: cumulative return +36.8% vs SPY +11.1%
- **Annualized Sharpe**: 3.48
- **Information Ratio vs SPY**: 2.85
- **Max drawdown**: -11.35%, 5-week recovery
- **Walk-forward OOS accuracy**: 55.2% directional, 12-month window, 7-day horizon

Any change to signal generation logic that materially affects these figures must be noted with the date and reason in a `CHANGELOG.md`.

---

## What Not To Do

- Do not run signal generation without first killing stale processes
- Do not declare a deployment successful without running the validation script
- Do not use rsync from Windows
- Do not modify Kelly parameters during live trading week
- Do not push to GitHub with validation errors outstanding
