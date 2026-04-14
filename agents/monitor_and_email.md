# Agent 1: VM Monitor + Email Sender
# Runs Monday nights via Windows Task Scheduler. SSHes into Oracle VM,
# reads signals.json, sends a formatted HTML email of the week's trades via Gmail SMTP.

## Credentials
- VM IP: 137.131.26.201
- SSH key path: /c/Users/sidda/Downloads/oracle-tradingbot.key
- My email: siddarth.manoj27@gmail.com
- signals.json remote path: /opt/tradingbot/signals/current_signals.json
- Last-sent tracker: agents/last_sent_week.txt
- Gmail SMTP server: smtp.gmail.com:587 (STARTTLS)
- Auth: GMAIL_FROM + GMAIL_APP_PASSWORD from .env (python-dotenv to load)

## Field Mapping (CRITICAL — read before parsing signals)
The VM's signals.json uses different field names than this prompt's column names.
Apply this mapping when reading each signal object:

| VM field name        | Use as          | Notes                          |
|----------------------|-----------------|--------------------------------|
| predicted_return     | pred            | Decimal, e.g. 0.044 = 4.4%   |
| direction_probability| probability     | Already a % value (0–100)     |
| last_close           | buy_price       | Current market close price    |
| limit_sell           | sell_limit      | Pre-calculated target price   |
| timestamp (top-level)| generated_at    | ISO 8601 string               |
| (hardcode)           | timeframe       | Always 5 (days)               |

Fields NOT in signals.json (derived by this agent):
- week_number: read from agents/last_sent_week.txt, increment by 1 each new run
- date_range: compute Monday–Friday of the signals timestamp week, format "M/D - M/D"
- percent_allocation: NOT in signals.json — omit from email or show "—"

## Step 1 — Read signals.json from VM
SSH into the VM and read signals.json:
```bash
ssh -i /c/Users/sidda/Downloads/oracle-tradingbot.key ubuntu@137.131.26.201 "cat /opt/tradingbot/signals/current_signals.json"
```

If SSH fails: log "VM unreachable at [timestamp]" to agents/../logs/monitor.log and exit. Do not send email.

Parse the JSON. The top-level structure has:
- `timestamp`: ISO 8601 string (use as generated_at)
- `all_signals`: array of signal objects (use this array)
- `summary`: summary stats

From `all_signals`, extract each entry and apply the field mapping above:
- ticker
- pred (from predicted_return)
- probability (from direction_probability)
- buy_price (from last_close)
- sell_limit (from limit_sell)

## Step 2 — Derive week_number and date_range

Read `agents/last_sent_week.txt`. It contains JSON like:
```json
{"week": 35, "timestamp": "2000-01-01T00:00:00"}
```

Parse the stored timestamp. Compare it to the signals `timestamp`.
- If signals timestamp > stored timestamp → this is a new week. Use week = stored_week + 1.
- If signals timestamp <= stored timestamp → already sent this week. Log "Already sent week [N], skipping" and exit.

Derive date_range from the signals timestamp:
- Find Monday of that week: monday = signal_ts - timedelta(days=signal_ts.weekday())
- tuesday = monday + timedelta(days=1)   ← entry day (positions entered Tuesday)
- following_monday = monday + timedelta(days=7)   ← exit day
- Format as "M/D - M/D" (e.g. "3/31 - 4/6")
- Positions are held Tuesday through the following Monday (5 trading days)

## Step 3 — Validate Before Sending
Run basic sanity checks. If any fail, log the error to logs/monitor.log and exit without sending:
- all_signals array is not empty
- all entries have ticker, predicted_return (buy_price), last_close
- generated_at timestamp is within the last 7 days (signals are not stale)

## Step 4 — Build HTML Email

Generate this email (dark theme, trading dashboard style):

Color scheme:
- Background: #0d1117
- Card background: #161b22
- Text: #e6edf3
- Green (positive): #3fb950
- Orange accent: #d29922
- Monospace font for all numbers: 'Courier New', monospace

Email sections:

1. **Header**: "INDEX-LAB Weekly Signals — Week [N] | [date_range]"
   Subtext: "Generated [generated_at] | [N] positions"

2. **Positions Table**:
   Columns: # | Ticker | ML Pred | Probability | Buy Price | Sell Limit | Timeframe
   - Sort by pred descending (highest conviction first)
   - Highlight top 3 rows with an orange left border
   - Format pred as percentage (e.g. 0.044 → 4.40%)
   - Format probability as percentage (e.g. 65.0 → 65.0%)
   - Format buy_price and sell_limit with $ prefix, 2 decimal places
   - Timeframe column: always show "5 days"

3. **Sizing Summary**:
   - Number of positions: [N]
   - Avg ML Pred (avg predicted_return): [X]%
   - Avg Probability: [X]%

4. **Footer**: "Validate before trading. This is model output, not financial advice."

## Step 5 — Send via Gmail SMTP
Load credentials from `.env` (python-dotenv) and send via smtplib:

```python
import os, smtplib, ssl
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv()
sender = os.environ['GMAIL_FROM']          # siddarth.manoj27@gmail.com
password = os.environ['GMAIL_APP_PASSWORD']  # 16-char app password

msg = EmailMessage()
msg['Subject'] = f"INDEX-LAB | Week {week_number} Signals | {date_range}"
msg['From'] = sender
msg['To'] = sender  # send to self
msg.set_content("HTML email — view in an HTML-capable mail client.")  # fallback
msg.add_alternative(html_body, subtype='html')

context = ssl.create_default_context()
with smtplib.SMTP('smtp.gmail.com', 587) as server:
    server.starttls(context=context)
    server.login(sender, password)
    server.send_message(msg)
```

## Step 6 — Update Tracker + Log
If email sent successfully:
- Write to agents/last_sent_week.txt:
  ```json
  {"week": [N], "timestamp": "[signals_timestamp]"}
  ```
- Append to logs/monitor.log: "Week [N] email sent successfully at [current_datetime]"

If SMTP fails (auth error, network error, etc.):
- Save the HTML to agents/unsent/week_[N]_report.html
- Append to logs/monitor.log: "Week [N] SMTP failed at [current_datetime]: [error] — saved to unsent/"
- Do NOT update last_sent_week.txt (so next run retries)
