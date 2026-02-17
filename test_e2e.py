#!/usr/bin/env python3
"""End-to-end dry run: generate signals for 1 small index, verify output"""
import subprocess
import sys
import os
import json
import time

BOTDIR = "/opt/tradingbot"
PYTHON = os.path.join(BOTDIR, ".venv", "bin", "python")
TRADING_BOT = os.path.join(BOTDIR, "trading_bot.py")
SIGNALS_FILE = os.path.join(BOTDIR, "current_signals.json")

print("=" * 60)
print("END-TO-END DRY RUN")
print("=" * 60)

# Remove old signals if they exist
if os.path.exists(SIGNALS_FILE):
    os.remove(SIGNALS_FILE)
    print(f"Removed old {SIGNALS_FILE}")

# Run trading_bot.py with NASDAQ (100 tickers)
print(f"\nRunning: {PYTHON} {TRADING_BOT} --indexes NASDAQ")
print("This should take ~15-25 minutes for ~100 tickers...")
sys.stdout.flush()

start = time.time()
result = subprocess.run(
    [PYTHON, TRADING_BOT, "--indexes", "NASDAQ"],
    cwd=BOTDIR,
    capture_output=True,
    text=True,
    timeout=1800  # 30 min timeout for NASDAQ
)
elapsed = time.time() - start

print(f"\nCompleted in {elapsed:.1f}s (exit code: {result.returncode})")

if result.returncode != 0:
    print(f"STDERR (last 1000 chars):")
    print(result.stderr[-1000:] if result.stderr else "None")
    print(f"\nSTDOUT (last 1000 chars):")
    print(result.stdout[-1000:] if result.stdout else "None")
    sys.exit(1)

# Check if signals file was generated
if not os.path.exists(SIGNALS_FILE):
    print(f"\n❌ ERROR: {SIGNALS_FILE} not found!")
    print(f"STDOUT (last 2000 chars):")
    print(result.stdout[-2000:] if result.stdout else "None")
    sys.exit(1)

# Load and inspect signals
with open(SIGNALS_FILE) as f:
    signals = json.load(f)

print(f"\n✅ Signals file exists: {os.path.getsize(SIGNALS_FILE)} bytes")
print(f"✅ Number of signals: {len(signals)}")

if signals:
    print(f"\nTop signals:")
    for i, sig in enumerate(signals[:5]):
        ticker = sig.get("ticker", "?")
        action = sig.get("action", "?")
        score = sig.get("confidence", sig.get("score", "?"))
        print(f"  {i+1}. {ticker}: {action} (score: {score})")

print(f"\n{'='*60}")
print("END-TO-END DRY RUN COMPLETE")
print(f"{'='*60}")
