#!/usr/bin/env python3
"""Regression tests for prediction freshness and training-label integrity.

Offline only: reads cache/market_data_20240929_today.pkl and makes no network
calls, so it needs no API key and can run anywhere the repo is checked out.
Skips cleanly if the cache is absent.

Guards the three defects fixed on 2026-08-29 (see CHANGELOG):
  1. regressors predicted on X_train[-1] (~98 calendar days stale)
  2. NaN forward returns silently labelled 'down' for the direction classifier
  3. the forward-return target was forward-filled along with the features

Run:  .venv/Scripts/python.exe test_prediction_freshness.py
"""
import os
import pickle
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

CACHE = os.path.join("cache", "market_data_20240929_today.pkl")
WINDOW = 5
SYMBOLS = ["SPY", "QQQ", "TLT"]


def load_frames():
    with open(CACHE, "rb") as fh:
        mkt = pickle.load(fh)
    import main
    frames = {s: main.add_features_to_stock(s, mkt[s].copy(), WINDOW, mkt)
              for s in SYMBOLS if s in mkt}
    return main, mkt, frames


def build_matrices(main, df):
    """Replicate the cleaning done by train_model_for_stock.

    Returns (df_clean, X_full, y_full, X_clean, y_clean) where X_full is the
    full-length feature matrix the live prediction row is taken from, and
    X_clean is the label-aligned subset used for training.
    """
    target = "forward_return_%d" % WINDOW
    feature_cols = [c for c in df.columns
                    if c not in ["Open", "High", "Low", "Close", "Volume", "Adj Close"]]
    dfc = df.copy()
    for col in feature_cols:
        dfc[col] = pd.to_numeric(dfc[col], errors="coerce")
        if col != target and dfc[col].isna().any():
            dfc[col] = dfc[col].fillna(method="ffill", limit=5)
            dfc[col] = dfc[col].fillna(method="bfill", limit=5)
    valid = [c for c in feature_cols if not dfc[c].isna().all() and dfc[c].var() != 0]
    skip = ["lead_", "future_", "forward_", "next_"]
    filt = [c for c in valid if not any(p in c.lower() for p in skip)]
    X = dfc[filt].values.astype(float)
    y = dfc[target].values
    mask = ~(np.isnan(X).any(axis=1) | np.isnan(y))
    return dfc, X, y, X[mask], y[mask]


# ---------------------------------------------------------------- defect 3 --
def test_target_not_forward_filled(main, frames):
    """The forward-return target must keep its trailing NaNs."""
    for sym, df in frames.items():
        dfc, X, y, X_clean, _ = build_matrices(main, df)
        target = "forward_return_%d" % WINDOW
        n_nan = int(dfc[target].tail(WINDOW).isna().sum())
        assert n_nan == WINDOW, (
            "%s: target was forward-filled, only %d/%d trailing NaNs survived"
            % (sym, n_nan, WINDOW))
        # The unlabelled tail must be excluded from training entirely. This is
        # what forces the live prediction row to come from the full matrix.
        mask = ~(np.isnan(X).any(axis=1) | np.isnan(y))
        leaked = int(mask[-WINDOW:].sum())
        assert leaked == 0, (
            "%s: %d of the final %d unlabelled rows leaked into training"
            % (sym, leaked, WINDOW))
        assert len(X_clean) < len(X) - WINDOW + 1, (
            "%s: clean matrix (%d) does not stop short of the frame (%d)"
            % (sym, len(X_clean), len(X)))


# ---------------------------------------------------------------- defect 2 --
def test_direction_labels_exclude_nan_forward_returns(main, frames):
    """NaN forward returns must yield NaN labels, never a fabricated 'down'."""
    for sym, df in frames.items():
        d = main.add_binary_direction_target(df.copy(), WINDOW)
        target = "forward_return_%d" % WINDOW
        label = "direction_%d" % WINDOW
        no_target = d[target].isna()
        assert int(no_target.sum()) == WINDOW, (
            "%s: expected %d unlabelled rows, got %d"
            % (sym, WINDOW, int(no_target.sum())))
        fabricated = int((d.loc[no_target, label] == 0).sum())
        assert fabricated == 0, (
            "%s: %d rows with no forward return were labelled 'down'" % (sym, fabricated))
        assert int(d[label].isna().sum()) == WINDOW, (
            "%s: labels are not NaN-preserving" % sym)
        lost = int(d.loc[~no_target, label].isna().sum())
        assert lost == 0, "%s: lost %d real labels" % (sym, lost)


# ---------------------------------------------------------------- defect 1 --
def test_prediction_uses_latest_bar(main, frames):
    """Every prediction must be anchored to the most recent bar in the frame."""
    for sym, df in frames.items():
        res = main.train_model_for_stock(sym, df, [2], regime="bull",
                                         regime_strength=0.8,
                                         prediction_window=WINDOW)
        assert res is not None, "%s: training returned None" % sym
        latest = str(df.index[-1].date())
        assert res["feature_row_date"] == latest, (
            "%s: predicted from %s but the latest bar is %s"
            % (sym, res["feature_row_date"], latest))
        assert res["feature_row_lag"] == 0, (
            "%s: feature_row_lag is %s, expected 0" % (sym, res["feature_row_lag"]))


def test_stale_row_regression_guard(main, frames):
    """Live row and the old X_train[-1] row must give different answers.

    If the stale-row behaviour is ever reinstated these converge and this fails.
    """
    differed = 0
    for sym, df in frames.items():
        _, X, _, X_clean, y_clean = build_matrices(main, df)
        fresh = main.train_and_predict_model(X_clean, y_clean, 2, WINDOW, x_latest=X[-1])
        stale = main.train_and_predict_model(X_clean, y_clean, 2, WINDOW, x_latest=None)
        assert fresh and stale, "%s: no prediction returned" % sym
        if abs(float(fresh[-1]) - float(stale[-1])) > 1e-9:
            differed += 1
    assert differed == len(frames), (
        "only %d/%d symbols differed between live and stale rows; "
        "the stale-row behaviour may be back" % (differed, len(frames)))


def test_refit_uses_all_clean_rows(main, frames):
    """The final fit before the live prediction must see more than the 80% split."""
    sym, df = next(iter(frames.items()))
    _, X, _, X_clean, y_clean = build_matrices(main, df)
    split = int(len(X_clean) * 0.8)

    seen = []
    original = main.RandomForestRegressor

    class Spy(original):
        def fit(self, X_fit, y_fit, *args, **kwargs):
            seen.append(len(X_fit))
            return super(Spy, self).fit(X_fit, y_fit, *args, **kwargs)

    main.RandomForestRegressor = Spy
    try:
        main.train_and_predict_model(X_clean, y_clean, 2, WINDOW, x_latest=X[-1])
    finally:
        main.RandomForestRegressor = original

    assert seen, "%s: no fit calls were recorded" % sym
    assert seen[-1] > split, (
        "%s: final fit saw %d rows, expected more than the %d-row training split"
        % (sym, seen[-1], split))


def test_backward_compatible_without_live_row(main, frames):
    """x_latest=None must still return a usable prediction rather than crashing."""
    sym, df = next(iter(frames.items()))
    _, _, _, X_clean, y_clean = build_matrices(main, df)
    out = main.train_and_predict_model(X_clean, y_clean, 2, WINDOW)
    assert out, "%s: fallback path returned nothing" % sym
    assert np.isfinite(float(out[-1])), "%s: fallback produced a non-finite value" % sym


# ------------------------------------------------------------- known issue --
def check_direction_model_actually_runs(main, frames):
    """DIAGNOSTIC, not yet an assertion.

    predict_direction_confidence still throws inside its categorical handling
    and falls back to a ticker-hash-derived direction/probability. Reported so
    the state stays visible; promote to an assertion once that is fixed.
    """
    def hash_fallback(ticker):
        h = sum(ord(c) for c in ticker)
        return ("up" if h % 3 != 0 else "down", 50.0 + (15.0 + (h % 20)) / 2)

    hashed = []
    for sym, df in frames.items():
        res = main.predict_direction_confidence(sym, df.copy(), WINDOW)
        direction, prob = hash_fallback(sym)
        if (res.get("direction") == direction
                and abs(res.get("direction_probability", -1.0) - prob) < 1e-9):
            hashed.append(sym)
    return hashed


TESTS = [
    test_target_not_forward_filled,
    test_direction_labels_exclude_nan_forward_returns,
    test_prediction_uses_latest_bar,
    test_stale_row_regression_guard,
    test_refit_uses_all_clean_rows,
    test_backward_compatible_without_live_row,
]


def run():
    if not os.path.exists(CACHE):
        print("SKIP: %s not present; these tests need the offline cache." % CACHE)
        return 0

    mod, _, frames = load_frames()
    if not frames:
        print("SKIP: no usable symbols in the cache.")
        return 0

    latest = next(iter(frames.values())).index[-1].date()
    print("Loaded %d symbols; latest bar %s\n" % (len(frames), latest))

    failures = 0
    for test in TESTS:
        try:
            test(mod, frames)
            print("  PASS  %s" % test.__name__)
        except AssertionError as exc:
            failures += 1
            print("  FAIL  %s: %s" % (test.__name__, exc))
        except Exception as exc:
            failures += 1
            print("  ERROR %s: %s: %s" % (test.__name__, type(exc).__name__, exc))

    hashed = check_direction_model_actually_runs(mod, frames)
    if hashed:
        print("\n  KNOWN ISSUE: direction model fell back to ticker-hash output "
              "for %d/%d symbols: %s" % (len(hashed), len(frames), ", ".join(hashed)))

    print("\n%d/%d passed" % (len(TESTS) - failures, len(TESTS)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
