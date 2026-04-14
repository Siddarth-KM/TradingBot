# Agent 2: Signals → Excel Writer
# Reads signals.json from VM, appends a new week block into InvestmentTracker.xlsx.
# Runs Monday night after Agent 1. Writes open positions with placeholder buy prices
# and Excel formulas so Percent Return and Final Amount auto-calculate when you fill
# in sell prices.

## Credentials
- VM IP: 137.131.26.201
- SSH key path: /c/Users/sidda/Downloads/oracle-tradingbot.key
- Excel path: C:/Users/sidda/OneDrive/Documents/InvestmentTracker.xlsx
- signals.json remote path: /opt/tradingbot/signals/current_signals.json

## Field Mapping (CRITICAL — apply before using any signal values)
The VM's signals.json uses different field names than this prompt's column names.

| VM field name        | Use as          | Notes                                      |
|----------------------|-----------------|---------------------------------------------|
| predicted_return     | pred            | Decimal, e.g. 0.044                        |
| direction_probability| probability     | Already a % value (0–100)                  |
| last_close           | buy_price       | Use as placeholder buy price               |
| limit_sell           | sell_limit      | Pre-calculated target                      |
| timestamp (top-level)| generated_at    | ISO 8601 string                            |
| (hardcode)           | timeframe       | Always 5                                   |

Fields NOT in signals.json (derived by this agent):
- week_number: read from agents/last_sent_week.txt (same tracker as Agent 1)
- date_range: Tuesday (entry day) through following Monday (exit day)
- percent_allocation: leave blank — user fills after placing trades

## Excel Column Layout (DO NOT DEVIATE)
```
A: Ticker
B: Timeframe
C: Pred
D: Probability
E: Buy Price
F: Sell Limit
G: Final Sell        ← user fills this
H: Percent Return    ← formula auto-calculates from G and E
I: Shares Purchased  ← user fills this
J: Start Amount      ← user fills this
K: Final Amount      ← formula auto-calculates from I and G
```

## Excel Week Block Layout
Each week block has this exact layout, starting at the first empty row:

```
Row 0:  [Week N]  [date_range]                             ← cols A, B
Row 1:  Ticker | Timeframe | Pred | Probability | Buy Price | Sell Limit | Final Sell | Percent Return | Shares Purchased | Start Amount | Final Amount
Row 2+: [one row per ticker — formulas in H and K]
Row N:  Total | 5 | [weekly_return] | | | | | | | [start_total] | [final_total]
Row N+1: S&P 500(Adjusted) | 5 | [spy_return] | | | | | | | [start_total] | [spy_final]
Row N+2: S&P 500 | 5 | [spy_return] | | | | | | | [spy_start_raw] | [spy_final_raw]
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

---

## Step 1: SSH and read signals.json
```bash
ssh -i /c/Users/sidda/Downloads/oracle-tradingbot.key ubuntu@137.131.26.201 "cat /opt/tradingbot/signals/current_signals.json"
```
If SSH fails: log error to logs/excel.log and exit.

Extract from JSON (applying field mapping above):
- timestamp → generated_at
- all_signals array: for each entry extract ticker, pred (predicted_return),
  probability (direction_probability), buy_price (last_close), sell_limit (limit_sell)

## Step 2: Derive week_number and date_range
Read `agents/last_sent_week.txt` to get current week number (same file Agent 1 writes).

Derive date_range:
- monday = signal_ts - timedelta(days=signal_ts.weekday())
- tuesday = monday + timedelta(days=1)         ← entry day
- following_monday = monday + timedelta(days=7) ← exit day
- date_range = f"{tuesday.month}/{tuesday.day} - {following_monday.month}/{following_monday.day}"

Example: signal timestamp 2026-03-31 → "3/31 - 4/6"

## Step 3: Determine starting capital
Load InvestmentTracker.xlsx with openpyxl (load_workbook).
Find the last row where column A = "Total" with a non-blank Final Amount (col K).
That value is the starting capital for the new week.

If no prior Total row exists: log an error and exit — do not write a week block without a starting figure.

## Step 4: Make backup
Before writing anything:
```python
import shutil
from datetime import datetime
backup_path = excel_path.replace('.xlsx', f'_backup_{datetime.today().strftime("%Y%m%d")}.xlsx')
shutil.copy2(excel_path, backup_path)
```

## Step 5: Append new week block to Excel
Use openpyxl to find the first empty row after the last content row.

Determine the absolute row number for the first ticker data row (call it `data_start`):
- data_start = first_empty_row + 2  (row 0 = week header, row 1 = column headers)

Write the week block in this exact order:

**Week header row** (first_empty_row):
- col A = f"Week {week_number}"
- col B = date_range

**Column headers row** (first_empty_row + 1):
- A=Ticker, B=Timeframe, C=Pred, D=Probability, E=Buy Price, F=Sell Limit,
  G=Final Sell, H=Percent Return, I=Shares Purchased, J=Start Amount, K=Final Amount

**One data row per ticker** (rows data_start through data_start + len(signals) - 1):
For each signal at absolute row number `r`:
- col A = ticker
- col B = 5
- col C = pred (decimal, e.g. 0.04392)
- col D = probability (e.g. 65.0)
- col E = buy_price (last_close — placeholder; user corrects after trading)
- col F = sell_limit
- col G = [blank]  ← user fills final sell price here
- col H = formula string: `=IF(G{r}="","",(G{r}-E{r})/E{r})`
- col I = [blank]  ← user fills shares purchased
- col J = [blank]  ← user fills start amount
- col K = formula string: `=IF(OR(I{r}="",G{r}=""),"",I{r}*G{r})`

**Total row**:
- col A = "Total", col B = 5, all other columns blank

**S&P 500(Adjusted) row**:
- col A = "S&P 500(Adjusted)", col B = 5, all other columns blank

**S&P 500 row**:
- col A = "S&P 500", col B = 5, all other columns blank

**Metric label rows** (Cum. Return through Profit Factor):
- Write label in col A, blank in col B, label in col C, blank in col D for each metric

**Blank separator row** after all metric rows.

Save workbook.

## Step 5b: Apply formatting

After writing all cell values (still using the same `wb` and `ws` objects, before final save), apply
formatting to match the template used in all prior weeks.

```python
from openpyxl.styles import Font, Border, Side, PatternFill

thin      = Side(border_style='thin')
medium    = Side(border_style='medium')
BLACK     = 'FF000000'
blue_fill = PatternFill(patternType='solid', fgColor='DEEAF1')
n_tickers = len(signals)
r_total   = data_start + n_tickers   # data_start = first_empty_row + 2

# --- Week header row (first_empty_row) ---
for col in range(1, 12):
    ws.cell(first_empty_row, col).font = Font(bold=True, color=BLACK)

# --- Column headers row (first_empty_row + 1) ---
r1 = first_empty_row + 1
ws.cell(r1, 1).font   = Font(bold=True, color=BLACK)
ws.cell(r1, 1).border = Border(top=medium, left=medium)
for col in range(2, 10):   # B–I: medium top, thin left+right
    ws.cell(r1, col).border = Border(top=medium, left=thin, right=thin)
for col in range(10, 12):  # J–K: medium top, thin left, no right
    ws.cell(r1, col).border = Border(top=medium, left=thin)

# --- Data rows ---
for i in range(n_tickers):
    r = data_start + i
    ws.cell(r, 1).font   = Font(bold=True, color=BLACK)
    ws.cell(r, 1).border = Border(top=thin, left=medium)
    for col in range(2, 10):   # B–I: thin top+left+right
        ws.cell(r, col).font   = Font(color=BLACK)
        ws.cell(r, col).border = Border(top=thin, left=thin, right=thin)
    for col in range(10, 12):  # J–K: thin top+left, no right
        ws.cell(r, col).font   = Font(color=BLACK)
        ws.cell(r, col).border = Border(top=thin, left=thin)

# --- Total row ---
ws.cell(r_total, 1).font   = Font(bold=True, color=BLACK)
ws.cell(r_total, 1).border = Border(top=medium, left=medium)
for col in range(2, 12):
    ws.cell(r_total, col).font   = Font(color=BLACK)
    ws.cell(r_total, col).border = Border(top=medium)

# --- S&P 500 rows ---
for r_spy in [r_total + 1, r_total + 2]:
    ws.cell(r_spy, 1).font = Font(bold=True, color=BLACK)

# --- Metric label rows (10 rows: Cum. Return … Profit Factor) ---
r_metric0 = r_total + 3
for i in range(10):
    r = r_metric0 + i
    c = ws.cell(r, 1)
    c.fill = blue_fill
    c.font = Font(bold=True, color=BLACK)
    if i == 0:
        c.border = Border(top=medium)
```

Save workbook (this replaces the earlier save call — call it once here):

```python
wb.save(excel_path)
```

## Step 6: Confirm
Print summary and append to logs/excel.log:
```
Week [N] open positions written to Excel:
  [N] tickers | Date range: [date_range]
  Buy prices from last_close (placeholders — correct after trading)
  Formulas written: H (Percent Return) and K (Final Amount) auto-calculate on sell price entry
  Formatting applied: borders, font colors, light-blue metric labels (matches Week 36 template)
  File: C:/Users/sidda/OneDrive/Documents/InvestmentTracker.xlsx
```

## Error Handling
- If SSH fails: log to excel.log, exit without touching Excel
- If no prior Total row found: log error, exit — never write without starting capital
- Never overwrite a row that already has a ticker name in col A in an existing week block
- Always backup before writing
