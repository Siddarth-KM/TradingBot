# CHANGELOG — INDEX-LAB TradingBot

All notable changes to signal-generation logic are recorded here. Per
`CLAUDE.md`, any change that materially affects the interview-critical metrics
must be noted with the date and reason.

---

## 2026-08-29 - Fix: predictions used ~3-month-old features; corrupted training labels

**Type:** `[FIX]` signal-generation correctness.

### Problems

Three defects, all confirmed against the committed offline cache
(`cache/market_data_20240929_today.pkl`, 370 bars, latest session 2026-03-23).

**1. Regressors forecast from a stale feature row.** All three return branches of
`train_and_predict_model` predicted on `X_train_scaled[-1:]` - the last row of the
80% training window - and shipped that as the ticker's 5-day forecast. The row
actually used was **2025-12-15** while the latest bar was **2026-03-23**: 67
trading rows / **98 calendar days** stale. Same fitted model, stale vs latest row:

| Ticker | Stale (shipped) | Latest (correct) | Delta | Sign flip |
|--------|----------------:|-----------------:|------:|:---------:|
| SPY    | -0.228%         | +1.650%          | 1.878pp | **yes** |
| QQQ    | -0.633%         | +1.183%          | 1.816pp | **yes** |
| TLT    | +0.081%         | +0.561%          | 0.481pp | no      |

`filter_positive_predictions` hard-gates on `pred > 0` and `select_top_2_per_index`
ranks by `pred`, so this changed *which tickers became signals*, not only their
magnitudes. `last_close` and the direction probability both came from the true
latest row, so `limit_sell` multiplied a 98-day-old forecast by today's price.

**2. Fabricated bearish training labels.** `add_binary_direction_target` used
`(forward_return_N > 0).astype(int)`. The final N rows have no forward return yet
and `NaN > 0` is False, so the newest bars were labelled `down`. Measured: 5 NaN
rows all labelled 0, **4 inside the `[:-1]` slice** fed to CatBoost. True up-rate
0.5863 vs 0.5799 as trained - fabricated rows sitting exactly at the recency edge.

**3. The training target was forward-filled.** The `fillna(ffill/bfill, limit=5)`
loop in `train_model_for_stock` ran over `feature_columns`, which includes
`forward_return_N`. The target's trailing NaNs were filled with the last real
value, fabricating labels that survived the NaN mask and landed in `y_test`,
contaminating the `prediction_bias` estimate applied to every prediction.

### Changes

1. **Target excluded from the fill** (`main.py`). Consequence: `X_clean` now
   legitimately stops N bars short, so the live prediction row must come from the
   full-length matrix.
2. **Live prediction row** (`main.py`). `train_model_for_stock` selects the most
   recent fully-populated row of the full regime-weighted `X` and passes it as
   `x_latest`. `train_and_predict_model` refits the estimator on all clean rows -
   after the CV gate and bias estimate are taken off the 80/20 split - then
   predicts that row. Existing bias correction, sign-flip guard and per-model caps
   are unchanged. `x_latest=None` preserves the old behaviour.
3. **Label integrity** (`main.py`). Direction labels keep NaN; training masks to
   labelled rows only.
4. **Provenance** (`trading_bot.py`). Every signal carries `feature_row_date` and
   `feature_row_lag`; `lag == 0` means the latest bar was used.
5. **Timer artifact** (`deploy/tradingbot-signals.timer`) corrected from
   `Mon 16:00` to `Mon 20:00 America/Chicago`, matching what the VM has actually
   run since the 2026-06-20 fix.

**Caveat:** `prediction_bias` is estimated from the 80%-fit model and applied to
the refit model. It is capped at +/-2% and preserves continuity with prior
behaviour, but the two models are not identical.

### Impact on interview-critical metrics

- **Historical figures are unaffected.** The 26-week live results (+36.8% vs SPY
  +11.1%, Sharpe 3.48, IR 2.85, max DD -11.35%, 5-week recovery) are realized
  trades recorded in `InvestmentTracker.xlsx`. Nothing here recomputes them.
- Future signals **will differ materially** from what the defective path produced,
  since two of three probe symbols flipped sign. This is a correctness fix, not a
  parameter tune.
- **Open question:** the walk-forward OOS accuracy figure (55.2% directional,
  12-month, 7-day horizon) was measured before these fixes. If it was produced by
  this prediction path it reflects the stale-row behaviour and needs re-derivation
  before being quoted. Tracked separately against `backtest_results_*.json`.

### Verification

- `test_prediction_freshness.py` - 6/6 passing, offline, no API key. Covers all
  three defects plus a regression guard that fails if `X_train[-1]` returns, a
  `fit()` spy proving the refit sees more than the 80% split, and the
  `x_latest=None` fallback.
- Local/VM reconciliation: `bot_utils.py`, `bot_config.py`, `trading_bot.py`
  confirmed content-identical to the running VM (CRLF-only delta) before deploy.
- VM: `py_compile` clean, `md5sum` verified after `scp`, single-index SPY run
  validated by `ops/health_report.py`.

### Known issue - NOT fixed here

**`direction` and `direction_probability` are ticker-hash output, not model
output.** `predict_direction_confidence` trains CatBoost successfully and then
throws one line later in its categorical handling (`Cannot setitem on a
Categorical with a new category`), returning its `except`-branch fallback:
`direction = 'up' if sum(ord(c) for c in ticker) % 3 != 0 else 'down'`,
`probability = 50 + (15 + hash % 20)/2`.

Verified pre-existing on unmodified `HEAD`, and verified against the live
2026-06-16 VM signals: **all eight** `direction`/`direction_probability` pairs
reproduce exactly from the ticker string. So `limit_sell` is derived from a
synthetic probability and the `direction == 'up'` gate is a hash filter.

Change 3 above therefore has no live effect until this is fixed. A prototype fix
(cast to `str` before the categorical conversion, as the training path already
does) works, but the real model returned `down` for all three probe symbols -
where the hash passes ~2/3 of tickers - so it could sharply reduce or empty the
weekly signal set. Deliberately deferred to its own change with a universe-wide
validation run rather than bundled here days before a live run.

### Files

`main.py`, `trading_bot.py`, `test_prediction_freshness.py`,
`deploy/tradingbot-signals.timer`.

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
