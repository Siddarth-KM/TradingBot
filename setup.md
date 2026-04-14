# INDEX-LAB Autonomous Agents — Setup Guide

## Folder Structure
Put these files in your project root:
```
TradingBot/
├── CLAUDE.md                          ← already built
├── agents/
│   ├── monitor_and_email.md           ← Agent 1 prompt
│   ├── excel_writer.md                ← Agent 2 prompt
│   ├── weekly_results.json            ← you fill this each Friday
│   ├── last_sent_week.txt             ← auto-maintained by Agent 1
│   └── unsent/                        ← Agent 1 saves here if Gmail fails
├── logs/
│   ├── monitor.log
│   └── excel.log
└── InvestmentTracker.xlsx
```

## Before First Run — Fill In Your Details

In monitor_and_email.md, replace:
- `[INSERT VM IP]` → your Oracle VM IP
- `[INSERT SSH KEY PATH]` → e.g. C:/Users/Siddarth/.ssh/oracle_key
- `[INSERT YOUR EMAIL]` → your email address

In excel_writer.md, replace the same three fields, plus:
- `[INSERT FULL PATH TO InvestmentTracker.xlsx]` → full local path

## Cron Setup (Windows — use Task Scheduler instead of cron)

Since you're on Windows, use Windows Task Scheduler:

**Agent 1 — Monitor + Email (Monday 10pm CT):**
- Program: `claude`
- Arguments: `-p "Read agents/monitor_and_email.md and execute it" --allowedTools "Bash,Read,mcp__gmail__send_email"`
- Start in: `C:\path\to\TradingBot`
- Schedule: Weekly, Monday, 10:00 PM

**Agent 2 MODE A — Write Open Positions (Monday 11pm CT):**
- Arguments: `-p "Read agents/excel_writer.md and execute it in MODE A (write open positions)"`
- Schedule: Weekly, Monday, 11:00 PM

**Agent 2 MODE B — Close Week (Friday 8pm CT):**
- Arguments: `-p "Read agents/excel_writer.md and execute it in MODE B (close completed week)"`
- Schedule: Weekly, Friday, 8:00 PM

## Your Weekly Workflow

**Monday (automated):**
- 10pm: Agent 1 SSHes into VM, reads signals.json, emails you the trades
- 11pm: Agent 2 writes the new week's open positions into Excel with buy prices and share counts

**Monday–Friday (manual, ~2 min):**
- Execute the trades from your email
- As positions close during the week, note the final sell prices

**Friday evening (manual, ~5 min):**
- Open `agents/weekly_results.json`
- Fill in `spy_return` and each ticker's `final_sell`
- Save the file
- Agent 2 MODE B runs at 8pm, closes out the week in Excel, computes all metrics

**That's it.** Excel stays current automatically. You never open the file to enter data.

## Testing Before Automating

Run each agent manually first in Claude Code:

Test Agent 1:
```
Read agents/monitor_and_email.md and execute it. Gmail MCP is connected.
```

Test Agent 2 MODE A:
```
Read agents/excel_writer.md and execute it in MODE A. 
Excel is at [path]. SSH key is at [path]. VM IP is [IP].
```

Test Agent 2 MODE B (use a past week's fake results first):
```
Read agents/excel_writer.md and execute it in MODE B.
weekly_results.json is ready with test data.
```

## Gmail MCP in Claude Code
In Claude Code Desktop, click the + button → Connectors → add Gmail.
It's the same Gmail account you already have connected here in claude.ai.

---

# Agent 1: VM Monitor + Email Sender
# Runs Monday nights via cron. SSHes into Oracle VM, reads signals.json,
# sends a formatted HTML email of the week's trades via Gmail MCP.

## Trigger
Run this agent every Monday at 10pm CT (after signal generation window):
```
0 22 * * 1 cd /path/to/project && claude -p "$(cat agents/monitor_and_email.md)" --allowedTools "Bash,Read,mcp__gmail__send_email" >> logs/monitor.log 2>&1
```

## Environment
- VM IP: [INSERT VM IP]
- SSH key path: [INSERT SSH KEY PATH e.g. C:/Users/Siddarth/.ssh/oracle_key]
- My email: [INSERT YOUR EMAIL]
- signals.json remote path: /home/ubuntu/TradingBot/signals.json
- Last-sent tracker: agents/last_sent_week.txt (stores last week number emailed)

## Step 1 — Check for New Signals
SSH into the VM and read signals.json:
```bash
ssh -i [KEY_PATH] ubuntu@[VM_IP] "cat /home/ubuntu/TradingBot/signals.json"
```

If SSH fails: log "VM unreachable at [timestamp]" and exit. Do not send email.

Parse the JSON. Extract:
- `week_number` field
- `date_range` field  
- `generated_at` timestamp
- All entries in `signals` array: ticker, pred, probability, buy_price, sell_limit, percent_allocation, signal

## Step 2 — Check if Already Sent
Read agents/last_sent_week.txt. If the week_number from signals.json matches the number in that file, log "Already sent week [N], skipping" and exit.

## Step 3 — Validate Before Sending
Run basic sanity checks. If any fail, log the error and exit without sending:
- signals array is not empty
- all entries have ticker, buy_price, percent_allocation
- percent_allocation values sum to between 95 and 105
- generated_at timestamp is within last 48 hours (signals are fresh)

## Step 4 — Build HTML Email

Generate this email (dark theme, tight trading dashboard style):

```html
Subject: INDEX-LAB | Week [N] Signals | [date_range]

Color scheme:
- Background: #0d1117
- Card background: #161b22
- Text: #e6edf3
- Green (positive/buy): #3fb950
- Red (negative/sell): #f85149
- Orange accent: #d29922
- Monospace font for all numbers: 'Courier New', monospace
```

Email sections:
1. **Header**: "INDEX-LAB Weekly Signals — Week [N] | [date_range]"
   - Subtext: "Generated [generated_at] | [N] positions"

2. **Positions Table**:
   | # | Ticker | ML Pred | Probability | Buy Price | Sell Limit | Allocation |
   - Sort by percent_allocation descending (highest conviction first)
   - Highlight top 3 by conviction with orange left border
   - Format Pred as percentage (e.g. 0.1525 → 15.25%)
   - Format Probability as percentage
   - Format Allocation as percentage
   - Format Buy/Sell prices with $ and 2 decimal places

3. **Sizing Summary**:
   - Total capital deployed: [sum of allocations]%
   - Number of positions: [N]
   - Avg conviction (avg pred): [X]%
   - Avg probability: [X]%

4. **Footer**: "Validate before trading. This is model output, not financial advice."

## Step 5 — Send via Gmail MCP
Use the Gmail MCP tool to send:
- To: [YOUR EMAIL]
- Subject: `INDEX-LAB | Week [N] Signals | [date_range]`
- Body: the HTML from Step 4

## Step 6 — Update Tracker
If email sent successfully, write the week_number to agents/last_sent_week.txt.
Log: "Week [N] email sent successfully at [timestamp]"

If Gmail MCP fails: save HTML to agents/unsent/week_[N]_report.html and log the failure. Retry next cron run.

---

# Agent 2: Signals → Excel Writer
# Reads signals.json from VM, writes a new week block into InvestmentTracker.xlsx.
# Run after Week N closes (Friday evening) to log completed trades.

## Trigger
Two modes:

MODE A — Write new week's open positions (Monday night, after signal email):
```
0 23 * * 1 cd /path/to/project && claude -p "$(cat agents/excel_writer.md) MODE=open" --allowedTools "Bash,Read" >> logs/excel.log 2>&1
```

MODE B — Close completed week (Friday evening, after you've filled in results):
```
0 20 * * 5 cd /path/to/project && claude -p "$(cat agents/excel_writer.md) MODE=close" --allowedTools "Bash,Read" >> logs/excel.log 2>&1
```

## Environment
- VM IP: [INSERT VM IP]
- SSH key path: [INSERT SSH KEY PATH]
- Excel path (local): [INSERT FULL PATH TO InvestmentTracker.xlsx]
- signals.json remote path: /home/ubuntu/TradingBot/signals.json
- Results file (you maintain): agents/weekly_results.json

## Excel Structure (DO NOT DEVIATE)
Each week block has this exact layout, starting at the first empty row:

```
Row 0:  [Week N]  [date_range]                    ← cols A, B
Row 1:  Ticker | Timeframe | Pred | Probability | Buy Price | Sell Limit | Final Sell | Percent Return | Shares Purchased | Start Amount | Final Amount  ← headers
Row 2+: [one row per ticker]
...
Row N:  Total | [timeframe] | [weekly_return] | [blank] | [blank] | [blank] | [blank] | [blank] | [blank] | [start_total] | [final_total]
Row N+1: S&P 500(Adjusted) | [timeframe] | [spy_return] | [blank] | [blank] | [blank] | [blank] | [blank] | [blank] | [start_total] | [spy_final]
Row N+2: S&P 500 | [timeframe] | [spy_return] | [blank] | [blank] | [blank] | [blank] | [blank] | [blank] | [spy_start_raw] | [spy_final_raw]
Row N+3: Cum. Return | [value] | YTD Cum. Return | [value]
Row N+4: Sharpe Ratio (Cum.) | [value] | YTD Sharpe Ratio | [value]
Row N+5: Max Drawdown (Cum.) | [value] | YTD Max Drawdown | [value]
Row N+6: Calmar Ratio (Cum.) | [value] | YTD Calmar Ratio | [value]
Row N+7: Sortino Ratio (Cum.) | [value] | YTD Sortino Ratio | [value]
Row N+8: Info. Ratio (Cum.) | [value] | YTD Info. Ratio | [value]
Row N+9: Win Rate | [value] | YTD Win Rate | [value]
Row N+10: Alpha vs SPY (Cum.) | [value] | YTD Alpha vs SPY | [value]
Row N+11: Payoff Ratio (Cum.) | [value] | Payoff Ratio (YTD) | [value]
Row N+12: Profit Factor (Cum.) | [value] | Profit Factor (YTD) | [value]
[blank row]
```

## MODE A — Write Open Positions (Monday night)
[See excel_writer.md for full detail]

## MODE B — Close Completed Week (Friday evening)
[See excel_writer.md for full detail]

---

# weekly_results.json — Template

```json
{
  "week_number": 36,
  "date_range": "4/7 - 4/13",
  "spy_return": 0.0,
  "trades": [
    {"ticker": "AMKR", "final_sell": 0.00, "notes": ""},
    {"ticker": "TER",  "final_sell": 0.00, "notes": ""},
    {"ticker": "MRNA", "final_sell": 0.00, "notes": ""},
    {"ticker": "AA",   "final_sell": 0.00, "notes": ""},
    {"ticker": "ASO",  "final_sell": 0.00, "notes": ""},
    {"ticker": "UCTT", "final_sell": 0.00, "notes": ""},
    {"ticker": "ASML", "final_sell": 0.00, "notes": ""},
    {"ticker": "ON",   "final_sell": 0.00, "notes": ""}
  ]
}
```

HOW TO USE:
1. Each Friday, open this file and fill in:
   - spy_return: SPY's % return for the week as a decimal (e.g. -0.035 for -3.5%)
   - final_sell: the price you actually sold each position at
   - notes: "Missed Entry" if you never got filled, "Stop Loss" etc
2. Save the file
3. Agent 2 (MODE B) will read this and write everything into Excel automatically
4. After Agent 2 confirms success, reset this file for next week
