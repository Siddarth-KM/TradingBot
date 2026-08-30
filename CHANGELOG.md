# CHANGELOG — INDEX-LAB TradingBot

All notable changes to signal-generation logic are recorded here. Per
`CLAUDE.md`, any change that materially affects the interview-critical metrics
must be noted with the date and reason.

---

## 2026-06-20 — Fix: signals used prior-trading-day prices (stale data)

**Type:** `[FIX]` signal-generation correctness + scheduling.

### Problem
Every weekly `signals.json` used the **prior trading day's** close as
`last_close` (and therefore as the basis for `limit_sell` and all as-of ML
features), not the most recent completed session. Confirmed with live VM
evidence on the 2026-06-16 run:

| Ticker | Signal `last_close` | Fri 6/12 close | Mon 6/15 close |
|--------|--------------------:|---------------:|---------------:|
| DELL   | 395.57              | **395.57**     | 409.07         |
| DAL    | 83.06               | **83.06**      | 84.07          |

### Root cause
`tradingbot-signals.timer` fired `OnCalendar=Mon 16:00 America/Chicago`
(= 21:00 UTC), only ~1h after the 20:00 UTC US close. Massive.com (Polygon)
publishes a session's EOD daily bar a few hours after close, so at run time
Monday's bar did not exist yet. `download_index_data` calls
`yf.download(..., end=datetime.today())` with an **inclusive** `to=` date
(`massive_api`), which then returned data only through the latest *available*
bar = the prior Friday.

### Changes
1. **Reschedule (VM).** `tradingbot-signals.timer` `OnCalendar` moved
   `Mon 16:00` → `Mon 20:00 America/Chicago` (= Tue 01:00 UTC CDT, ~5h after
   close). Cron safety-net moved `30 23 * * 1` (Mon 23:30 UTC) →
   `0 5 * * 2` (Tue 05:00 UTC). Run finishes (~2h) well before Tuesday
   pre-market, when trades are placed.
2. **Pre-flight freshness guard (code).** New `expected_last_trading_day()` and
   `verify_data_freshness()` in `bot_utils.py`; NYSE `US_MARKET_HOLIDAYS`
   (2026–2027) + `MARKET_CLOSE_UTC_HOUR` in `bot_config.py`.
   `generate_trading_signals()` now verifies the provider has the latest
   expected session **before** the ML pipeline; on stale data it logs, does
   **not** overwrite `current_signals.json`, and `sys.exit(2)`.
3. **Provenance stamp.** `format_trading_signals()` adds a top-level
   `data_as_of` (ISO date of the session the prices came from).
4. **Defense in depth.** `ops/health_report.py` asserts `data_as_of ==
   expected_last_trading_day(...)` and alerts on mismatch.

### Impact on interview-critical metrics
- **Historical figures are unaffected.** The fix changes only the entry-price
  basis for *future* signals; it does not touch any historical
  `InvestmentTracker.xlsx` rows or the recorded 26-week live results
  (+36.8% vs SPY +11.1%, Sharpe 3.48, IR 2.85, max DD −11.35%, WF-OOS 55.2%).
- Going forward, signals reflect the **actual most-recent session close**
  rather than the prior session, so future weekly entries (and any metrics
  derived from them) will differ from what the buggy path would have produced.
  This is a correctness improvement, not a parameter tune.

### Verification
- Unit: `expected_last_trading_day()` — normal Tue→Mon, Memorial-Day-week
  Tue→prior Fri, Sat-after-Juneteenth→Thu 6/18 (holiday handling). All pass.
- Unit: `verify_data_freshness()` fresh / stale (retries) / empty paths. Pass.
- VM: byte-for-byte md5 match after `scp`; venv `py_compile` clean.
- Schedule: `systemctl list-timers` NEXT = `Tue 2026-06-23 01:00:00 UTC`.
- Pending: end-to-end confirmation at the next live run (Mon 2026-06-22 20:00
  CT) that each `last_close` matches that session and `data_as_of` is stamped.

### Files
`bot_config.py`, `bot_utils.py`, `trading_bot.py`, `ops/health_report.py`;
VM units `tradingbot-signals.timer` + ubuntu crontab. Pre-change VM copies
backed up to `/opt/tradingbot/backup_predeploy_20260621_031458/` and
`tradingbot-signals.timer.bak_20260621`.
