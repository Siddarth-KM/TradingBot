# Agent 3: Weekly Metrics Writer

Runs every **Tuesday 5pm CST** via Windows Task Scheduler. Locates the
most-recently-completed week in `InvestmentTracker.xlsx` and writes 20
formulas across the 10-row metric subblock — Cum metrics in column B, YTD
metrics in column D — matching the formula structure used in prior weeks.

## Inputs (user-supplied before agent runs)

For the just-completed week's block, the user must have entered:
- `G{row}` Final Sell, `I{row}` Shares Purchased, `J{row}` Start Amount,
  `K{row}` Final Amount for each ticker
- `K{Total}` Final Amount on the Total row (the agent reads this to detect "completed")
- `H{N+1}` SPY (Adjusted) weekly return on the S&P 500(Adjusted) row
- `H{N+2}` SPY weekly return on the S&P 500 row

If `K{Total}` is blank or `H{N+1}` is blank, the agent exits cleanly without
writing — the next scheduled run will pick it up.

## Excel layout

Each week block ends with these fixed offsets from the Total row `N`:
```
Row N    : Total              (col H = =IF(K{N}="","",K{N}/J{N}-1))
Row N+1  : S&P 500(Adjusted)  (col H = SPY weekly return — user-entered)
Row N+2  : S&P 500            (col H = SPY weekly return — user-entered)
Row N+3  : Cum. Return        (B = cumulative since Week 1, D = YTD)
Row N+4  : Sharpe Ratio
Row N+5  : Max Drawdown       (RECURSIVE — references prior week's B/D{N+3} and B/D{N+5})
Row N+6  : Calmar Ratio
Row N+7  : Sortino Ratio
Row N+8  : Info. Ratio
Row N+9  : Win Rate
Row N+10 : Alpha vs SPY
Row N+11 : Payoff Ratio
Row N+12 : Profit Factor
```

Number formats: rows 0/2/6/7 (Cum Return, Max DD, Win Rate, Alpha) use `0.00%`;
the other six metrics use `0.00`.

## Formula patterns

Constants used (extracted from existing Week 37 cells):
- Weekly risk-free rate: `0.0009615385` (≈ 5% annual / 52)
- Annualization factor: `SQRT(52)`

| Metric | Formula |
|---|---|
| Cum. Return | `=(1+H1)*(1+H2)*…*(1+Hn)-1` |
| Sharpe | `=IF(STDEV(H1,…,Hn)=0,"N/A",(AVERAGE(H1,…,Hn)-RFR)/STDEV(H1,…,Hn)*SQRT(52))` |
| Max DD | `=MIN({prior_maxdd},(1+{prior_cum})*(1+{this_H})/MAX(1+{prior_cum},(1+{prior_cum})*(1+{this_H}))-1)` |
| Calmar | `=IF({maxdd}=0,"N/A",((1+{cum})^(52/N)-1)/ABS({maxdd}))` |
| Sortino | `=IF(SQRT((MIN(H1-RFR,0)^2+…+MIN(Hn-RFR,0)^2)/N)=0,"N/A",(AVERAGE(H1,…,Hn)-RFR)/SQRT(…/N)*SQRT(52))` |
| Info Ratio | `=IF(SQRT(((Σ(Hi-Si)^2-(Σ(Hi-Si))^2/N)/(N-1))=0,"N/A",Σ(Hi-Si)/N/SQRT(…)*SQRT(52))` |
| Win Rate | `=(IF(H1>0,1,0)+…+IF(Hn>0,1,0))/N` |
| Alpha vs SPY | `={cum}-((1+S1)*…*(1+Sn)-1)` |
| Payoff Ratio | `=IF(OR(neg_count=0,pos_count=0),"N/A",(pos_sum/pos_count)/ABS(neg_sum/neg_count))` |
| Profit Factor | `=IF(neg_sum=0,"N/A",pos_sum/ABS(neg_sum))` |

For column D (YTD), the same formulas are generated using only the subset of
weeks whose tuesday-date falls in the current calendar year.

## YTD anchor detection

The first YTD week of the current year is detected dynamically:
1. Read `date_range` from each week's header row (col B, e.g. `"4/21 - 4/27"`).
2. Walk backwards from the most recent week, decrementing the year whenever a
   week's tuesday-month is greater than the next week's (i.e., crosses Jan→Dec).
3. YTD anchor = smallest week whose computed year == today's year.

For 2026, this resolves to Week 23. When January 2027 arrives, it auto-rolls to
the first week whose tuesday-date is in 2027. No manual config update needed.

## Variable stock counts

Each week's `Total` row position is discovered fresh on every run by scanning
column A for `"Total"` after each `"Week N"` header. Adding/removing tickers in
any week shifts row offsets; since the agent re-derives all references from the
current sheet state, formulas are correct by construction.

## Idempotency

For each week, the agent checks `B{N+3}` (Cum. Return cell). If non-blank,
the week is skipped. So running the agent multiple times in a row is safe;
it only writes the most recent week that's missing metrics.

Tracker file `agents/last_metrics_week.txt` records the last-written week +
timestamp for diagnostics.

## Operations

```
# Dry run (no writes — print all 20 formulas to stdout):
python3 agents/metrics_agent.py --dry-run

# Real run:
python3 agents/metrics_agent.py
```

If the workbook is open in Excel, the agent retries `wb.save()` every 10 min
(matches `excel_agent.py`'s pattern). Close the workbook to let the save go
through.
