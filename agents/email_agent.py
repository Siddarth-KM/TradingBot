#!/usr/bin/env python3
"""Agent 1: VM Monitor + Email Sender. Reads signals from VM via SSH, sends weekly email."""

import os, json, sys, smtplib, ssl, subprocess, logging, time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

LOG     = Path("logs/monitor.log")
TRACKER = Path("agents/last_sent_week.txt")
SSH_KEY = r"C:\Users\sidda\Downloads\oracle-tradingbot.key"
VM      = "ubuntu@137.131.26.201"
REMOTE  = "/opt/tradingbot/signals/current_signals.json"

WAIT_POLL_SEC = 300    # re-check every 5 min
WAIT_MAX_MIN  = 120    # give up after 2 hours

logging.basicConfig(filename=str(LOG), level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger()
log.addHandler(logging.StreamHandler(sys.stdout))

def fetch_signals():
    result = subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no",
         "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
         VM, f"cat {REMOTE}"],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)

# ── Step 1: SSH read signals — wait for new data if not ready yet ─────────────
try:
    data = fetch_signals()
except Exception as e:
    log.error(f"SSH/parse failed: {e}")
    sys.exit(1)

generated_at = data["timestamp"]
signal_ts    = datetime.fromisoformat(generated_at)

if TRACKER.exists():
    tracker   = json.loads(TRACKER.read_text())
    stored_ts = datetime.fromisoformat(tracker["timestamp"])
    wait_start = datetime.now()
    while signal_ts <= stored_ts:
        elapsed = (datetime.now() - wait_start).total_seconds() / 60
        if elapsed >= WAIT_MAX_MIN:
            log.info(f"No new signals after {WAIT_MAX_MIN}min — exiting.")
            sys.exit(0)
        log.info(f"Signals not yet updated (still week {tracker['week']}). Waiting 5 min...")
        time.sleep(WAIT_POLL_SEC)
        try:
            data = fetch_signals()
            generated_at = data["timestamp"]
            signal_ts    = datetime.fromisoformat(generated_at)
        except Exception as e:
            log.warning(f"Refetch failed: {e}")
else:
    tracker = {"week": 0, "timestamp": "2000-01-01T00:00:00"}

# Deduplicate by ticker, apply field mapping, sort by pred desc
seen, signals = set(), []
for s in data["all_signals"]:
    t = s["ticker"]
    if t not in seen:
        seen.add(t)
        signals.append({
            "ticker":      t,
            "pred":        s["predicted_return"],
            "probability": s["direction_probability"],
            "buy_price":   s["last_close"],
            "sell_limit":  s["limit_sell"],
        })
signals.sort(key=lambda x: x["pred"], reverse=True)
log.info(f"Loaded {len(signals)} signals (generated_at={generated_at})")

week_number      = tracker["week"] + 1
monday           = signal_ts - timedelta(days=signal_ts.weekday())
tuesday          = monday + timedelta(days=1)
following_monday = monday + timedelta(days=7)
date_range = f"{tuesday.month}/{tuesday.day} - {following_monday.month}/{following_monday.day}"
log.info(f"New week {week_number} | {date_range}")

# ── Step 3: Validate ──────────────────────────────────────────────────────────
if not signals:
    log.error("Empty signals array — aborting.")
    sys.exit(1)
if (datetime.now(timezone.utc) - signal_ts.replace(tzinfo=timezone.utc)).days > 7:
    log.error(f"Signals are stale ({generated_at}) — aborting.")
    sys.exit(1)

# ── Step 4: Build HTML ────────────────────────────────────────────────────────
def row_html(s):
    return f"""
      <tr>
        <td style="padding:10px 14px;font-family:'Courier New',monospace;font-size:15px;
                   color:#e6edf3;font-weight:600;">{s['ticker']}</td>
        <td style="padding:10px 14px;font-family:'Courier New',monospace;font-size:14px;
                   color:#e6edf3;">${s['buy_price']:.2f}</td>
        <td style="padding:10px 14px;font-family:'Courier New',monospace;font-size:14px;
                   color:#3fb950;font-weight:600;">${s['sell_limit']:.2f}</td>
        <td style="padding:10px 14px;font-family:'Courier New',monospace;font-size:14px;
                   color:#d29922;">{s['pred']*100:.2f}%</td>
        <td style="padding:10px 14px;font-family:'Courier New',monospace;font-size:14px;
                   color:#e6edf3;">{s['probability']:.1f}%</td>
      </tr>"""

html_body = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:24px;background:#0d1117;font-family:system-ui,sans-serif;">
  <div style="max-width:560px;margin:0 auto;background:#161b22;border-radius:10px;
              padding:28px 32px;border:1px solid #30363d;">
    <h2 style="margin:0 0 4px;color:#e6edf3;font-size:18px;letter-spacing:.5px;">
      INDEX-LAB &mdash; Week {week_number} <span style="color:#888;">|</span> {date_range}
    </h2>
    <p style="margin:0 0 24px;color:#8b949e;font-size:13px;">
      {len(signals)} positions &middot; Generated {generated_at[:16].replace('T',' ')} UTC
    </p>
    <table style="width:100%;border-collapse:collapse;">
      <thead>
        <tr style="border-bottom:1px solid #30363d;">
          <th style="padding:8px 14px;text-align:left;color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:.8px;">Ticker</th>
          <th style="padding:8px 14px;text-align:left;color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:.8px;">Buy Price</th>
          <th style="padding:8px 14px;text-align:left;color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:.8px;">Sell Limit</th>
          <th style="padding:8px 14px;text-align:left;color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:.8px;">ML Pred</th>
          <th style="padding:8px 14px;text-align:left;color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:.8px;">Prob</th>
        </tr>
      </thead>
      <tbody>{"".join(row_html(s) for s in signals)}</tbody>
    </table>
    <p style="margin:24px 0 0;color:#484f58;font-size:11px;border-top:1px solid #21262d;padding-top:16px;">
      Validate before trading. Model output, not financial advice.
    </p>
  </div>
</body>
</html>"""

# ── Step 5: Send ──────────────────────────────────────────────────────────────
sender   = os.environ["GMAIL_FROM"]
password = os.environ["GMAIL_APP_PASSWORD"]
msg = EmailMessage()
msg["Subject"] = f"INDEX-LAB | Week {week_number} | {date_range}"
msg["From"]    = sender
msg["To"]      = sender
msg.set_content("HTML email — view in an HTML-capable mail client.")
msg.add_alternative(html_body, subtype="html")

try:
    ctx = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls(context=ctx)
        server.login(sender, password)
        server.send_message(msg)
except Exception as e:
    unsent = Path(f"agents/unsent/week_{week_number}_report.html")
    unsent.write_text(html_body)
    log.error(f"SMTP failed: {e} — saved to {unsent}")
    sys.exit(1)

log.info(f"Week {week_number} email sent OK | {date_range} | {len(signals)} tickers")

# ── Step 6: Update tracker ────────────────────────────────────────────────────
TRACKER.write_text(json.dumps({"week": week_number, "timestamp": generated_at}))
