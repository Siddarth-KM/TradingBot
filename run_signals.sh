#!/bin/bash
# Signal generation cron job for tradingbot
# Runs Monday 4 PM CST (22:00 UTC) to generate signals before 11 PM limit orders
# Expected duration: ~5.5 hours for ~1600 tickers at 5 API calls/min
#
# Cron entry (UTC): 0 22 * * 1 /opt/tradingbot/run_signals.sh

BOTDIR="/opt/tradingbot"
VENV="$BOTDIR/.venv/bin"
LOGFILE="$BOTDIR/logs/signal_generation.log"
LOCKFILE="/tmp/tradingbot_signals.lock"

# Ensure log directory exists
mkdir -p "$BOTDIR/logs"

# Prevent overlapping runs
if [ -f "$LOCKFILE" ]; then
    PID=$(cat "$LOCKFILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') Signal generation already running (PID $PID)" >> "$LOGFILE"
        exit 0
    fi
    rm -f "$LOCKFILE"
fi

echo $$ > "$LOCKFILE"
trap "rm -f $LOCKFILE" EXIT

echo "=========================================" >> "$LOGFILE"
echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') Starting signal generation" >> "$LOGFILE"
echo "=========================================" >> "$LOGFILE"

cd "$BOTDIR"

# Run trading_bot.py with all 4 indexes
"$VENV/python" "$BOTDIR/trading_bot.py" --indexes SPY NASDAQ SP400 SPSM >> "$LOGFILE" 2>&1
EXIT_CODE=$?

echo "" >> "$LOGFILE"
if [ $EXIT_CODE -eq 0 ]; then
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') ✅ Signal generation completed successfully (exit code 0)" >> "$LOGFILE"
    # Verify signals file exists and has content
    if [ -f "$BOTDIR/signals/current_signals.json" ]; then
        SIGNALS_SIZE=$(stat -c%s "$BOTDIR/signals/current_signals.json")
        SIGNAL_COUNT=$(python3 -c "import json; print(len(json.load(open('$BOTDIR/signals/current_signals.json'))))" 2>/dev/null || echo "?")
        echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') Signals file: $SIGNALS_SIZE bytes, $SIGNAL_COUNT signals" >> "$LOGFILE"
    else
        echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') ⚠️ WARNING: signals/current_signals.json not found after successful run" >> "$LOGFILE"
    fi
else
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') ❌ Signal generation FAILED (exit code $EXIT_CODE)" >> "$LOGFILE"
fi

echo "=========================================" >> "$LOGFILE"
echo "" >> "$LOGFILE"
