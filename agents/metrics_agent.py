#!/usr/bin/env python3
"""Agent 3: Weekly Metrics Writer.

Runs every Tuesday 5pm CST. Locates the most-recently-completed week in
InvestmentTracker.xlsx (the week whose Total row K is filled in but whose
metric block is still blank) and writes 20 formulas across the 10-row metric
subblock — Cum metrics in col B, YTD metrics in col D — matching the exact
formula structure used in prior weeks (extracted from Week 37).
"""

import json, sys, time, logging, argparse, re
from datetime import datetime, timedelta
from pathlib import Path
import openpyxl

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False

EXCEL_PATH    = r"C:\Users\sidda\OneDrive\Documents\InvestmentTracker.xlsx"
LOG           = Path("logs/metrics.log")
TRACKER       = Path("agents/last_metrics_week.txt")
RETRY_SECS    = 600                # PermissionError retry interval
RFR_WEEKLY    = "0.0009615385"     # ~5% annual / 52, as Excel-formula string

LOG.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=str(LOG), level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger()
log.addHandler(logging.StreamHandler(sys.stdout))


# ── Formula builders (verbatim patterns from Week 37 B1087–B1096) ────────────

def f_cum_return(h_cells):
    return "=" + "*".join(f"(1+{h})" for h in h_cells) + "-1"

def f_sharpe(h_cells):
    cells = ",".join(h_cells)
    return (f'=IF(STDEV({cells})=0,"N/A",'
            f'(AVERAGE({cells})-{RFR_WEEKLY})/STDEV({cells})*SQRT(52))')

def f_max_dd(prior_maxdd_cell, prior_cum_cell, this_h):
    return (f'=MIN({prior_maxdd_cell},'
            f'(1+{prior_cum_cell})*(1+{this_h})/'
            f'MAX(1+{prior_cum_cell},(1+{prior_cum_cell})*(1+{this_h}))-1)')

def f_max_dd_first(this_h):
    return f"=MIN(0,{this_h})"

def f_calmar(maxdd_cell, cum_cell, n):
    return f'=IF({maxdd_cell}=0,"N/A",((1+{cum_cell})^(52/{n})-1)/ABS({maxdd_cell}))'

def f_sortino(h_cells):
    n = len(h_cells)
    cells    = ",".join(h_cells)
    downside = "+".join(f"MIN({h}-{RFR_WEEKLY},0)^2" for h in h_cells)
    return (f'=IF(SQRT(({downside})/{n})=0,"N/A",'
            f'(AVERAGE({cells})-{RFR_WEEKLY})/SQRT(({downside})/{n})*SQRT(52))')

def f_info_ratio(h_cells, spy_cells):
    n = len(h_cells)
    if n < 2:
        return '="N/A"'
    diffs       = [f"({h}-{s})" for h, s in zip(h_cells, spy_cells)]
    sum_squared = "+".join(f"{d}^2" for d in diffs)
    sum_diffs   = "+".join(diffs)
    variance    = f"(({sum_squared}-({sum_diffs})^2/{n})/{n-1})"
    return (f'=IF(SQRT({variance})=0,"N/A",'
            f'({sum_diffs})/{n}/SQRT({variance})*SQRT(52))')

def f_win_rate(h_cells):
    n = len(h_cells)
    counts = "+".join(f"IF({h}>0,1,0)" for h in h_cells)
    return f"=({counts})/{n}"

def f_alpha(cum_cell, spy_cells):
    chain = "*".join(f"(1+{s})" for s in spy_cells)
    return f"={cum_cell}-({chain}-1)"

def f_payoff(h_cells):
    pos_cnt = "+".join(f"IF({h}>0,1,0)" for h in h_cells)
    neg_cnt = "+".join(f"IF({h}<0,1,0)" for h in h_cells)
    pos_sum = "+".join(f"IF({h}>0,{h},0)" for h in h_cells)
    neg_sum = "+".join(f"IF({h}<0,{h},0)" for h in h_cells)
    return (f'=IF(OR({neg_cnt}=0,{pos_cnt}=0),"N/A",'
            f'(({pos_sum})/({pos_cnt}))/ABS(({neg_sum})/({neg_cnt})))')

def f_profit_factor(h_cells):
    pos_sum = "+".join(f"IF({h}>0,{h},0)" for h in h_cells)
    neg_sum = "+".join(f"IF({h}<0,{h},0)" for h in h_cells)
    return f'=IF(({neg_sum})=0,"N/A",({pos_sum})/ABS({neg_sum}))'


# ── SPY auto-fill ────────────────────────────────────────────────────────────

def fetch_spy_weekly_return(date_range_str, today_year):
    """Return SPY weekly return (float) for the week described by date_range_str,
    e.g. '5/12 - 5/18' (Mon–Sun).  Returns None on any failure.
    """
    if not _YF_AVAILABLE:
        log.warning("yfinance not installed — cannot auto-fill SPY return.")
        return None
    try:
        end_str = date_range_str.split("-")[1].strip()   # "5/18"
        m, d    = int(end_str.split("/")[0]), int(end_str.split("/")[1])
        # Infer year: if the date is more than 7 days in the future, use prior year
        week_end = datetime(today_year, m, d)
        if week_end > datetime.today() + timedelta(days=7):
            week_end = datetime(today_year - 1, m, d)
        week_mon = week_end - timedelta(days=6)   # Monday of that week

        fetch_start = (week_mon - timedelta(days=14)).strftime("%Y-%m-%d")
        fetch_end   = (week_end + timedelta(days=2)).strftime("%Y-%m-%d")

        data = yf.download("SPY", start=fetch_start, end=fetch_end,
                           auto_adjust=True, progress=False)
        if data.empty or len(data) < 2:
            log.warning("SPY download returned insufficient data.")
            return None

        import pandas as pd
        idx = data.index.normalize()
        ts_end = pd.Timestamp(week_end.date())
        ts_mon = pd.Timestamp(week_mon.date())

        # Last close on or before week_end (i.e., Friday of the target week)
        this_week = data[idx <= ts_end]
        if this_week.empty:
            return None
        close_this = float(this_week["Close"].values[-1])

        # Last close strictly before Monday (i.e., Friday of prior week)
        prior_week = data[idx < ts_mon]
        if prior_week.empty:
            return None
        close_prior = float(prior_week["Close"].values[-1])

        return close_this / close_prior - 1
    except Exception as e:
        log.warning(f"SPY fetch failed: {e}")
        return None


# ── Sheet scan helpers ───────────────────────────────────────────────────────

WEEK_RE = re.compile(r"^Week\s+(\d+)$")

def scan_weeks(ws):
    """Returns dict: week_number -> {header, total, date_range}."""
    weeks = {}
    current_wk = None
    for r in range(1, ws.max_row + 1):
        a = ws.cell(r, 1).value
        if isinstance(a, str):
            m = WEEK_RE.match(a.strip())
            if m:
                wk = int(m.group(1))
                weeks[wk] = {"header": r, "date_range": ws.cell(r, 2).value, "total": None}
                current_wk = wk
            elif a.strip() == "Total" and current_wk is not None and weeks[current_wk]["total"] is None:
                weeks[current_wk]["total"] = r
    return weeks


def determine_ytd_anchor(weeks, today_year):
    """Returns the smallest week number whose tuesday-date is in today_year."""
    # Walk backward: assume most-recent week is current year, decrement when M jumps Jan→Dec
    sorted_wks = sorted(weeks.keys(), reverse=True)
    if not sorted_wks:
        return None
    year = today_year
    last_month = None
    week_year = {}
    for wk in sorted_wks:
        dr = weeks[wk]["date_range"] or ""
        try:
            tuesday_str = dr.split("-")[0].strip()  # "4/21"
            month = int(tuesday_str.split("/")[0])
        except Exception:
            month = last_month
        if last_month is not None and month is not None and month > last_month:
            year -= 1
        week_year[wk] = year
        last_month = month
    ytd_weeks = [wk for wk, y in week_year.items() if y == today_year]
    return min(ytd_weeks) if ytd_weeks else min(weeks.keys())


# ── Main ─────────────────────────────────────────────────────────────────────

def build_formulas(weeks, target_week, ytd_anchor):
    """Returns (b_formulas[10], d_formulas[10]) for the target week's metric block."""
    target_total = weeks[target_week]["total"]
    this_h       = f"H{target_total}"

    prior_weeks_all = sorted(w for w in weeks if w <= target_week)
    ytd_weeks       = [w for w in prior_weeks_all if w >= ytd_anchor]

    cum_H   = [f"H{weeks[w]['total']}"     for w in prior_weeks_all]
    cum_SPY = [f"H{weeks[w]['total'] + 1}" for w in prior_weeks_all]
    ytd_H   = [f"H{weeks[w]['total']}"     for w in ytd_weeks]
    ytd_SPY = [f"H{weeks[w]['total'] + 1}" for w in ytd_weeks]

    N = len(cum_H)
    Y = len(ytd_H)

    # Metric block row offsets (relative to target_total)
    cum_return_row = target_total + 3
    max_dd_row     = target_total + 5

    # Recursive Max DD: needs prior week's metric block. Find the immediately
    # previous existing week (numerically), not necessarily target_week-1.
    prior_wk_b = max((w for w in weeks if w < target_week), default=None)
    if prior_wk_b is not None:
        pt_b = weeks[prior_wk_b]["total"]
        prior_cum_B   = f"B{pt_b + 3}"
        prior_maxdd_B = f"B{pt_b + 5}"
    else:
        prior_cum_B = prior_maxdd_B = None

    prior_wk_d = max((w for w in weeks if w < target_week and w >= ytd_anchor), default=None)
    if prior_wk_d is not None and target_week > ytd_anchor:
        pt_d = weeks[prior_wk_d]["total"]
        prior_cum_D   = f"D{pt_d + 3}"
        prior_maxdd_D = f"D{pt_d + 5}"
    else:
        prior_cum_D = prior_maxdd_D = None

    # Self-references for Calmar
    cum_B_cell    = f"B{cum_return_row}"
    cum_D_cell    = f"D{cum_return_row}"
    maxdd_B_cell  = f"B{max_dd_row}"
    maxdd_D_cell  = f"D{max_dd_row}"

    # Build column-B (cumulative since Week 1)
    b = [
        f_cum_return(cum_H),
        f_sharpe(cum_H),
        (f_max_dd(prior_maxdd_B, prior_cum_B, this_h)
         if prior_maxdd_B else f_max_dd_first(this_h)),
        f_calmar(maxdd_B_cell, cum_B_cell, N),
        f_sortino(cum_H),
        f_info_ratio(cum_H, cum_SPY),
        f_win_rate(cum_H),
        f_alpha(cum_B_cell, cum_SPY),
        f_payoff(cum_H),
        f_profit_factor(cum_H),
    ]

    # Build column-D (YTD)
    d = [
        f_cum_return(ytd_H),
        f_sharpe(ytd_H),
        (f_max_dd(prior_maxdd_D, prior_cum_D, this_h)
         if prior_maxdd_D else f_max_dd_first(this_h)),
        f_calmar(maxdd_D_cell, cum_D_cell, Y),
        f_sortino(ytd_H),
        f_info_ratio(ytd_H, ytd_SPY),
        f_win_rate(ytd_H),
        f_alpha(cum_D_cell, ytd_SPY),
        f_payoff(ytd_H),
        f_profit_factor(ytd_H),
    ]
    return b, d, N, Y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print formulas without saving")
    args = ap.parse_args()

    # Step 1: open workbook in formula mode (no data_only — avoids stale cache issues
    # with cells openpyxl has written but Excel hasn't recalculated yet).
    def _load():
        attempt = 1
        while True:
            try:
                return openpyxl.load_workbook(EXCEL_PATH)
            except PermissionError:
                if args.dry_run:
                    log.error("Excel file is locked. Exiting.")
                    sys.exit(1)
                log.warning(f"Attempt {attempt}: locked. Retry in {RETRY_SECS//60} min.")
                attempt += 1
                time.sleep(RETRY_SECS)

    wb_scan = _load()
    ws_scan = wb_scan.active
    weeks = scan_weeks(ws_scan)
    if not weeks:
        log.error("No Week N headers found in workbook.")
        sys.exit(1)

    # Filter out weeks where Total row wasn't found
    weeks = {w: m for w, m in weeks.items() if m["total"] is not None}

    today_year = datetime.today().year
    ytd_anchor = determine_ytd_anchor(weeks, today_year)
    log.info(f"YTD anchor for {today_year}: Week {ytd_anchor}")

    # Step 2: identify target week — most recent week with K filled but B{cum} blank.
    # K may be a raw number the user typed OR a formula (e.g. =SUM(...)) they entered in Excel.
    # Both indicate trade results are present. B_cum is non-empty once the agent has written it
    # (formula string), so the string-non-empty check is reliable here.
    def _k_filled(v):
        return isinstance(v, (int, float)) or (isinstance(v, str) and v.startswith("="))

    # Collect weeks that have trade data (K filled) but no metrics yet (B_cum blank).
    # Only consider a week if its immediately prior week already has metrics — this
    # prevents scanning back to ancient weeks that were never part of automated tracking.
    # Process oldest qualifying week first so the recursive Max DD chain stays valid.
    def _has_metrics(wk):
        tot = weeks[wk]["total"]
        v = ws_scan.cell(tot + 3, 2).value
        return v not in (None, "")

    candidates = []
    for wk in sorted(weeks.keys()):
        tot = weeks[wk]["total"]
        k_val = ws_scan.cell(tot, 11).value
        b_cum = ws_scan.cell(tot + 3, 2).value
        if not _k_filled(k_val) or b_cum not in (None, ""):
            continue
        prior_wk = max((w for w in weeks if w < wk), default=None)
        if prior_wk is not None and not _has_metrics(prior_wk):
            log.info(f"Week {wk}: K filled, metrics blank, but prior week {prior_wk} has no metrics — skipping")
            continue
        candidates.append(wk)
        log.info(f"Week {wk} needs metrics (K filled, B{tot+3} blank)")

    target_week = min(candidates) if candidates else None

    if target_week is None:
        log.info("No week needs metrics — nothing to do.")
        sys.exit(0)

    target_total = weeks[target_week]["total"]
    log.info(f"Target week: {target_week} (Total at row {target_total})")

    # Auto-fill SPY weekly return if not already present
    spy_adj_h = ws_scan.cell(target_total + 1, 8).value
    auto_spy  = None
    if spy_adj_h in (None, "") or (isinstance(spy_adj_h, str) and spy_adj_h.startswith("=")):
        log.info(f"SPY H{target_total+1} blank — fetching from yfinance...")
        auto_spy = fetch_spy_weekly_return(weeks[target_week]["date_range"], today_year)
        if auto_spy is None:
            log.warning("Could not fetch SPY return — exiting; will retry next run.")
            sys.exit(0)
        log.info(f"SPY weekly return fetched: {auto_spy:.4%}")

    # Step 3: build formulas
    b_formulas, d_formulas, N, Y = build_formulas(weeks, target_week, ytd_anchor)
    log.info(f"Built formulas: cumulative N={N} weeks, YTD Y={Y} weeks")

    if args.dry_run:
        cum_return_row = target_total + 3
        labels = ["Cum. Return", "Sharpe", "Max DD", "Calmar", "Sortino",
                  "Info Ratio", "Win Rate", "Alpha", "Payoff", "Profit Factor"]
        for i, lbl in enumerate(labels):
            r = cum_return_row + i
            print(f"\n--- {lbl} (row {r}) ---")
            print(f"B{r} = {b_formulas[i]}")
            print(f"D{r} = {d_formulas[i]}")
        log.info("Dry-run complete; no file changes.")
        return

    # Step 4: load fresh workbook for writing, then write formulas + number formats
    wb = _load()
    ws = wb.active

    if auto_spy is not None:
        ws.cell(target_total + 1, 8).value = auto_spy
        ws.cell(target_total + 2, 8).value = auto_spy
        ws.cell(target_total + 1, 8).number_format = "0.00%"
        ws.cell(target_total + 2, 8).number_format = "0.00%"
        log.info(f"Wrote SPY return {auto_spy:.4%} to H{target_total+1} and H{target_total+2}")

    PCT, DEC = "0.00%", "0.00"
    fmts = [PCT, DEC, PCT, DEC, DEC, DEC, PCT, PCT, DEC, DEC]

    cum_return_row = target_total + 3
    for i, (bf, df, fmt) in enumerate(zip(b_formulas, d_formulas, fmts)):
        r = cum_return_row + i
        b_cell = ws.cell(r, 2); b_cell.value = bf; b_cell.number_format = fmt
        d_cell = ws.cell(r, 4); d_cell.value = df; d_cell.number_format = fmt

    # Step 5: save with retry on PermissionError
    attempt = 1
    while True:
        try:
            wb.save(EXCEL_PATH)
            break
        except PermissionError:
            log.warning(f"Save attempt {attempt} failed — file locked. Retry in {RETRY_SECS//60} min.")
            attempt += 1
            time.sleep(RETRY_SECS)

    log.info(f"Week {target_week} metrics written OK | "
             f"Cum N={N}, YTD Y={Y} | block rows {cum_return_row}-{cum_return_row+9}")

    # Step 6: update tracker
    TRACKER.write_text(json.dumps({
        "week": target_week,
        "timestamp": datetime.now().isoformat(),
    }))


if __name__ == "__main__":
    main()
