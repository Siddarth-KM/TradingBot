#!/bin/bash
# Signal generation runner for tradingbot.
# Primary invocation: trade_executor.py phase 0 calls trading_bot.py directly.
# Safety-net invocation: cron runs this with --if-stale Mon 23:30 UTC to retry
# if phase 0 silently missed.
#
# Usage:
#   run_signals.sh            # always run
#   run_signals.sh --if-stale # skip if current_signals.json is from today

BOTDIR="/opt/tradingbot"
VENV="$BOTDIR/.venv/bin"
LOGFILE="$BOTDIR/logs/signal_generation.log"
LOCKFILE="/tmp/tradingbot_signals.lock"
SIGNALS_FILE="$BOTDIR/signals/current_signals.json"
HEALTH_REPORT="$BOTDIR/ops/health_report.py"
MIN_FREE_MB=150

IF_STALE=0
for arg in "$@"; do
    case "$arg" in
        --if-stale) IF_STALE=1 ;;
    esac
done

mkdir -p "$BOTDIR/logs"

log() {
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $*" >> "$LOGFILE"
}

alert() {
    # Fire an SMTP alert via health_report.py using the canned subject/body hook.
    # We just invoke validate mode which will alert if file is missing/stale/bad.
    [ -x "$VENV/python" ] && "$VENV/python" "$HEALTH_REPORT" >> "$LOGFILE" 2>&1 || true
}

# --if-stale: skip entirely if today's signals already exist
if [ "$IF_STALE" -eq 1 ] && [ -f "$SIGNALS_FILE" ]; then
    FILE_DATE=$(date -u -r "$SIGNALS_FILE" '+%Y-%m-%d')
    TODAY=$(date -u '+%Y-%m-%d')
    if [ "$FILE_DATE" = "$TODAY" ]; then
        log "--if-stale: signals already fresh ($FILE_DATE), skipping"
        exit 0
    fi
    log "--if-stale: signals are stale ($FILE_DATE vs $TODAY), proceeding"
fi

# Prevent overlapping runs
if [ -f "$LOCKFILE" ]; then
    PID=$(cat "$LOCKFILE")
    if kill -0 "$PID" 2>/dev/null; then
        log "Signal generation already running (PID $PID), exiting"
        exit 0
    fi
    rm -f "$LOCKFILE"
fi

echo $$ > "$LOCKFILE"
trap "rm -f $LOCKFILE" EXIT

# Pre-flight memory check
FREE_MB=$(free -m | awk '/^Mem:/ {print $7}')
log "Pre-flight: ${FREE_MB}MB available"
if [ -n "$FREE_MB" ] && [ "$FREE_MB" -lt "$MIN_FREE_MB" ]; then
    log "ABORT: free memory ${FREE_MB}MB < ${MIN_FREE_MB}MB threshold. Dropping caches and retrying once."
    sudo sync 2>/dev/null || true
    sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null || true
    sleep 2
    FREE_MB=$(free -m | awk '/^Mem:/ {print $7}')
    log "After drop_caches: ${FREE_MB}MB available"
    if [ -n "$FREE_MB" ] && [ "$FREE_MB" -lt "$MIN_FREE_MB" ]; then
        log "ABORT: still under threshold, not starting signal gen"
        alert
        exit 2
    fi
fi

echo "=========================================" >> "$LOGFILE"
log "Starting signal generation"
echo "=========================================" >> "$LOGFILE"

cd "$BOTDIR"

# Run trading_bot.py with all 4 indexes
"$VENV/python" "$BOTDIR/trading_bot.py" --indexes SPY NASDAQ SP400 SPSM >> "$LOGFILE" 2>&1
EXIT_CODE=$?

echo "" >> "$LOGFILE"
if [ $EXIT_CODE -eq 0 ]; then
    log "Signal generation completed (exit 0)"
    if [ -f "$SIGNALS_FILE" ]; then
        SIGNALS_SIZE=$(stat -c%s "$SIGNALS_FILE")
        log "Signals file: $SIGNALS_SIZE bytes"
    else
        log "WARNING: $SIGNALS_FILE not found after successful run"
    fi
else
    log "Signal generation FAILED (exit $EXIT_CODE)"
fi

# Post-run validation (writes run_status.json, alerts on failure)
if [ -f "$HEALTH_REPORT" ]; then
    log "Running health_report.py"
    "$VENV/python" "$HEALTH_REPORT" >> "$LOGFILE" 2>&1
    HEALTH_EXIT=$?
    log "health_report exited $HEALTH_EXIT"
    if [ $HEALTH_EXIT -ne 0 ] && [ $EXIT_CODE -eq 0 ]; then
        EXIT_CODE=$HEALTH_EXIT
    fi
else
    log "WARNING: $HEALTH_REPORT missing, skipping validation"
fi

echo "=========================================" >> "$LOGFILE"
echo "" >> "$LOGFILE"

exit $EXIT_CODE
