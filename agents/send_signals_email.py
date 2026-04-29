import os, smtplib, ssl, json, sys
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sender   = os.environ['GMAIL_FROM']
password = os.environ['GMAIL_APP_PASSWORD']

# ── Signals (from VM, already fetched) ───────────────────────────────────────
signals_raw = [
    {"ticker":"RMBS","predicted_return":0.12657,"direction_probability":61.5,"last_close":126.87,"limit_sell":136.75},
    {"ticker":"EGBN","predicted_return":0.11344,"direction_probability":59.5,"last_close":28.18, "limit_sell":30.08},
    {"ticker":"HL",  "predicted_return":0.1008, "direction_probability":61.5,"last_close":19.33, "limit_sell":20.53},
    {"ticker":"AGX", "predicted_return":0.10001,"direction_probability":59.5,"last_close":611.21,"limit_sell":647.58},
    {"ticker":"INTC","predicted_return":0.06059,"direction_probability":58.5,"last_close":65.7,  "limit_sell":68.03},
    {"ticker":"CF",  "predicted_return":0.05219,"direction_probability":66.0,"last_close":115.94,"limit_sell":119.93},
    {"ticker":"GFS", "predicted_return":0.03534,"direction_probability":59.5,"last_close":58.76, "limit_sell":60.0},
]
GENERATED_AT = "2026-04-21T04:03:40.764256"

# Deduplicate by ticker (INTC appeared in SPY + NASDAQ)
seen = set()
signals = []
for s in signals_raw:
    if s["ticker"] not in seen:
        seen.add(s["ticker"])
        signals.append({
            "ticker":      s["ticker"],
            "pred":        s["predicted_return"],
            "probability": s["direction_probability"],
            "buy_price":   s["last_close"],
            "sell_limit":  s["limit_sell"],
        })
# already sorted by pred desc

# ── Week / date ───────────────────────────────────────────────────────────────
tracker = json.loads(Path("agents/last_sent_week.txt").read_text())
stored_ts = datetime.fromisoformat(tracker["timestamp"])
signal_ts = datetime.fromisoformat(GENERATED_AT)

if signal_ts <= stored_ts:
    print("Already sent this week — skipping.")
    sys.exit(0)

week_number = tracker["week"] + 1
monday           = signal_ts - timedelta(days=signal_ts.weekday())
tuesday          = monday + timedelta(days=1)
following_monday = monday + timedelta(days=7)
date_range = f"{tuesday.month}/{tuesday.day} - {following_monday.month}/{following_monday.day}"

# ── Build HTML ────────────────────────────────────────────────────────────────
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

rows_html = "".join(row_html(s) for s in signals)

html_body = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:24px;background:#0d1117;font-family:system-ui,sans-serif;">
  <div style="max-width:560px;margin:0 auto;background:#161b22;border-radius:10px;
              padding:28px 32px;border:1px solid #30363d;">

    <h2 style="margin:0 0 4px;color:#e6edf3;font-size:18px;letter-spacing:.5px;">
      INDEX-LAB — Week {week_number} <span style="color:#888;">|</span> {date_range}
    </h2>
    <p style="margin:0 0 24px;color:#8b949e;font-size:13px;">
      {len(signals)} positions &middot; Generated {GENERATED_AT[:16].replace('T', ' ')} UTC
    </p>

    <table style="width:100%;border-collapse:collapse;">
      <thead>
        <tr style="border-bottom:1px solid #30363d;">
          <th style="padding:8px 14px;text-align:left;color:#8b949e;font-size:12px;
                     text-transform:uppercase;letter-spacing:.8px;">Ticker</th>
          <th style="padding:8px 14px;text-align:left;color:#8b949e;font-size:12px;
                     text-transform:uppercase;letter-spacing:.8px;">Buy Price</th>
          <th style="padding:8px 14px;text-align:left;color:#8b949e;font-size:12px;
                     text-transform:uppercase;letter-spacing:.8px;">Sell Limit</th>
          <th style="padding:8px 14px;text-align:left;color:#8b949e;font-size:12px;
                     text-transform:uppercase;letter-spacing:.8px;">ML Pred</th>
          <th style="padding:8px 14px;text-align:left;color:#8b949e;font-size:12px;
                     text-transform:uppercase;letter-spacing:.8px;">Prob</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>

    <p style="margin:24px 0 0;color:#484f58;font-size:11px;border-top:1px solid #21262d;
              padding-top:16px;">
      Validate before trading. Model output, not financial advice.
    </p>
  </div>
</body>
</html>"""

# ── Send ──────────────────────────────────────────────────────────────────────
from email.message import EmailMessage
msg = EmailMessage()
msg['Subject'] = f"INDEX-LAB | Week {week_number} | {date_range}"
msg['From']    = sender
msg['To']      = sender
msg.set_content("HTML email — view in an HTML-capable mail client.")
msg.add_alternative(html_body, subtype='html')

context = ssl.create_default_context()
with smtplib.SMTP('smtp.gmail.com', 587) as server:
    server.starttls(context=context)
    server.login(sender, password)
    server.send_message(msg)

print(f"Sent: Week {week_number} | {date_range} | {len(signals)} tickers")

# ── Update tracker ────────────────────────────────────────────────────────────
Path("agents/last_sent_week.txt").write_text(
    json.dumps({"week": week_number, "timestamp": GENERATED_AT})
)

log_line = f"{datetime.now().isoformat()} Week {week_number} email sent OK\n"
Path("logs/monitor.log").open('a').write(log_line)
print("Tracker updated → Week", week_number)
