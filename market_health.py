"""
Market Health Pipeline — Standalone Market Condition Assessment

Analyzes upcoming-week market health using 4 components:
  1. Vol Structure (25%) — VRP, VIX z-score, VIX momentum (FRED + massive_api)
  2. Credit/Macro (25%) — HY OAS spread, 3m10y yield curve (FRED + massive_api)
  3. Breadth/Internals (25%) — 200-day breadth, McClellan, 52w hi/lo (massive_api)
  4. Mean Reversion (25%) — SPY distance from SMA, RSI, Bollinger (massive_api)

Legacy components (--legacy flag):
  1. Market Internals (35%) — breadth, advance/decline
  2. Geopolitical/Global Risk (25%) — VIX, credit spreads, gold, oil, dollar
  3. News Sentiment (20%) — MarketAux headline sentiment
  4. Economic Calendar (20%) — FRED upcoming releases & surprise factor

Usage:
    python market_health.py                  # Run full pipeline (new components)
    python market_health.py --legacy         # Run legacy pipeline
    python market_health.py --component vol  # Run single component
    python market_health.py --dry-run        # Show scores without writing file
"""

import os
import sys
import json
import math
import time
import logging
import argparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests

# Local imports
import market_health_config as cfg

# Import massive_api for market data (same source as main pipeline)
# No yfinance — it blocks requests from cloud deployments
try:
    import massive_api
    MASSIVE_AVAILABLE = True
except ImportError:
    MASSIVE_AVAILABLE = False
    logging.warning("massive_api not available — market data downloads will fail")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# ============================================================================
# DATA DOWNLOADING UTILITIES
# ============================================================================

# Massive API doesn't carry pure indexes (VIX, ^GSPC, etc.)
# Map to tradeable ETF proxies that track them closely
_MASSIVE_TICKER_PROXIES = {
    'VIX': 'VIXY',       # ProShares VIX Short-Term Futures ETF
    '^VIX': 'VIXY',
    '^GSPC': 'SPY',
    '^DJI': 'DIA',
    '^NDX': 'QQQ',
}


def download_ticker(ticker, start_date, end_date):
    """
    Download OHLCV data for a single ticker via Massive API.
    No yfinance — yfinance blocks cloud deployments.
    Automatically maps non-tradeable indexes to ETF proxies.
    Returns DataFrame or None.
    """
    if not MASSIVE_AVAILABLE:
        logger.error(f"Cannot download {ticker}: massive_api not available")
        return None

    # Try original ticker first, then proxy if it fails
    api_ticker = ticker
    proxy_ticker = _MASSIVE_TICKER_PROXIES.get(ticker)

    try:
        df = massive_api.download(api_ticker, start=start_date, end=end_date)
        if df is not None and not df.empty:
            return df
    except Exception as e:
        logger.debug(f"Massive API failed for {api_ticker}: {e}")

    # Try proxy if original returned nothing
    if proxy_ticker and proxy_ticker != api_ticker:
        try:
            logger.debug(f"Trying proxy {proxy_ticker} for {ticker}")
            df = massive_api.download(proxy_ticker, start=start_date, end=end_date)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            logger.warning(f"Massive API proxy {proxy_ticker} also failed for {ticker}: {e}")

    logger.warning(f"No data returned for {ticker}")
    return None


def download_multiple_tickers(tickers, start_date, end_date):
    """
    Download OHLCV data for multiple tickers in parallel.
    Returns dict of {ticker: DataFrame}.
    """
    results = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(download_ticker, t, start_date, end_date): t
            for t in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                df = future.result()
                if df is not None and not df.empty:
                    results[ticker] = df
            except Exception as e:
                logger.warning(f"Download failed for {ticker}: {e}")
    return results


def fetch_fred_series(series_id, start_date, end_date):
    """
    Fetch a FRED time series as a pandas Series (date-indexed, float values).
    Returns None if FRED_API_KEY is missing or request fails.
    """
    if not cfg.FRED_API_KEY:
        logger.warning(f"FRED_API_KEY not set — cannot fetch {series_id}")
        return None
    try:
        url = f"{cfg.FRED_BASE_URL}/series/observations"
        params = {
            'series_id': series_id,
            'api_key': cfg.FRED_API_KEY,
            'file_type': 'json',
            'observation_start': start_date if isinstance(start_date, str) else start_date.strftime('%Y-%m-%d'),
            'observation_end': end_date if isinstance(end_date, str) else end_date.strftime('%Y-%m-%d'),
        }
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        obs = resp.json().get('observations', [])
        dates, values = [], []
        for o in obs:
            val = o.get('value', '.')
            if val != '.':
                try:
                    dates.append(pd.Timestamp(o['date']))
                    values.append(float(val))
                except (ValueError, KeyError):
                    pass
        if not dates:
            return None
        return pd.Series(values, index=pd.DatetimeIndex(dates), name=series_id).sort_index()
    except Exception as e:
        logger.warning(f"FRED fetch failed for {series_id}: {e}")
        return None


def _compute_vix_settlement_dates(start_year, end_year):
    """
    Compute VIX monthly futures settlement dates.
    VIX futures settle on the Wednesday that is 30 calendar days before
    the third Friday of the following calendar month.
    """
    import calendar
    from datetime import date, timedelta
    dates = []
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            # Third Friday of the FOLLOWING month
            next_month = month + 1
            next_year = year
            if next_month > 12:
                next_month = 1
                next_year += 1
            # Find 3rd Friday: first day of month, find first Friday, add 14 days
            first_day = date(next_year, next_month, 1)
            # weekday(): Monday=0, Friday=4
            days_to_friday = (4 - first_day.weekday()) % 7
            first_friday = first_day + timedelta(days=days_to_friday)
            third_friday = first_friday + timedelta(days=14)
            # 30 calendar days before
            settle = third_friday - timedelta(days=30)
            # Must be a Wednesday (weekday=2). Adjust if needed.
            if settle.weekday() != 2:
                # Move to nearest Wednesday
                offset = (2 - settle.weekday()) % 7
                if offset > 3:
                    offset -= 7
                settle = settle + timedelta(days=offset)
            dates.append(settle)
    return sorted(dates)


def fetch_vix_futures(start_date, end_date):
    """
    Fetch VIX futures term structure: VX1 (front month) and VX2 (second month).
    Returns DataFrame with columns ['VX1_Close', 'VX2_Close'] indexed by date.

    Data sources (tried in order):
      1. Nasdaq Data Link (CHRIS/CBOE_VX1, CHRIS/CBOE_VX2) — needs free API key
      2. Direct CBOE CDN download via requests
      3. vix_utils (CBOE CDN async download) — fallback
    Returns None if all sources fail.
    """
    # --- Source 1: Nasdaq Data Link ---
    api_key = cfg.NASDAQ_DATA_LINK_API_KEY
    if api_key:
        try:
            import nasdaqdatalink
            nasdaqdatalink.ApiConfig.api_key = api_key
            vx1_df = nasdaqdatalink.get('CHRIS/CBOE_VX1',
                                         start_date=str(start_date),
                                         end_date=str(end_date))
            vx2_df = nasdaqdatalink.get('CHRIS/CBOE_VX2',
                                         start_date=str(start_date),
                                         end_date=str(end_date))
            vx1_col = 'Settle' if 'Settle' in vx1_df.columns else 'Close'
            vx2_col = 'Settle' if 'Settle' in vx2_df.columns else 'Close'
            df = pd.DataFrame({
                'VX1_Close': vx1_df[vx1_col],
                'VX2_Close': vx2_df[vx2_col],
            })
            df.index = pd.to_datetime(df.index)
            df = df.dropna()
            if len(df) > 0:
                logger.info(f"VIX futures: {len(df)} obs from Nasdaq Data Link")
                return df
            logger.warning("Nasdaq Data Link: VIX futures returned empty dataset")
        except Exception as e:
            logger.warning(f"Nasdaq Data Link VIX futures failed: {e}")

    # --- Source 2: Direct CBOE CDN download ---
    try:
        result = _fetch_vix_futures_cboe_direct(start_date, end_date)
        if result is not None and len(result) > 0:
            logger.info(f"VIX futures: {len(result)} obs from CBOE direct download")
            return result
    except Exception as e:
        logger.warning(f"CBOE direct download failed: {e}")

    # --- Source 3: vix_utils (async CBOE CDN) ---
    try:
        from vix_utils import load_vix_term_structure, pivot_futures_on_monthly_tenor
        raw_records = load_vix_term_structure()
        wide = pivot_futures_on_monthly_tenor(raw_records)
        vx1 = wide[(1, 'Close')]
        vx2 = wide[(2, 'Close')]
        df = pd.DataFrame({'VX1_Close': vx1, 'VX2_Close': vx2})
        df.index = pd.to_datetime(df.index)
        df = df.dropna()
        mask = (df.index >= pd.Timestamp(start_date)) & (df.index <= pd.Timestamp(end_date))
        result = df[mask]
        if len(result) > 0:
            logger.info(f"VIX futures: {len(result)} obs from vix_utils/CBOE")
            return result
    except Exception as e:
        logger.warning(f"vix_utils fetch failed: {e}")

    return None


def _fetch_vix_futures_cboe_direct(start_date, end_date):
    """
    Download VIX futures contract CSVs directly from CBOE CDN using requests.
    For each trading day, identifies VX1 (front month) and VX2 (second month)
    based on settlement dates.
    """
    import io
    from datetime import date, timedelta

    start_dt = pd.Timestamp(start_date).date()
    end_dt = pd.Timestamp(end_date).date()

    # Compute settlement dates covering the range (need contracts from before start through after end)
    settle_dates = _compute_vix_settlement_dates(start_dt.year - 1, end_dt.year + 1)
    # Only keep settlements that are relevant (from ~2 months before start to after end)
    relevant = [d for d in settle_dates
                if d >= start_dt - timedelta(days=90) and d <= end_dt + timedelta(days=60)]

    headers = {'User-Agent': 'Mozilla/5.0'}
    base_url = 'https://cdn.cboe.com/data/us/futures/market_statistics/historical_data/VX'
    all_rows = []

    for settle_date in relevant:
        url = f'{base_url}/VX_{settle_date.isoformat()}.csv'
        try:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                continue
            df = pd.read_csv(io.StringIO(r.text))
            df['Settlement_Date'] = settle_date
            all_rows.append(df)
        except Exception:
            continue

    if not all_rows:
        return None

    raw = pd.concat(all_rows, ignore_index=True)
    raw['Trade Date'] = pd.to_datetime(raw['Trade Date'])
    raw['Settlement_Date'] = pd.to_datetime(raw['Settlement_Date'])

    # For each trade date, find VX1 (nearest settlement >= trade date) and VX2 (next)
    trade_dates = sorted(raw['Trade Date'].unique())
    vx1_list, vx2_list, date_list = [], [], []

    for td in trade_dates:
        td_date = pd.Timestamp(td)
        if td_date.date() < start_dt or td_date.date() > end_dt:
            continue
        # Get all contracts trading on this date, sorted by settlement
        day_data = raw[raw['Trade Date'] == td].sort_values('Settlement_Date')
        # VX1 = nearest unexpired (settlement >= trade date)
        active = day_data[day_data['Settlement_Date'] >= td]
        if len(active) < 2:
            continue
        vx1_close = float(active.iloc[0]['Close'])
        vx2_close = float(active.iloc[1]['Close'])
        if vx1_close > 0 and vx2_close > 0:
            date_list.append(td_date)
            vx1_list.append(vx1_close)
            vx2_list.append(vx2_close)

    if not date_list:
        return None

    result = pd.DataFrame({'VX1_Close': vx1_list, 'VX2_Close': vx2_list}, index=date_list)
    result.index.name = 'Date'
    return result


# ============================================================================
# NEW COMPONENT 1: VOL STRUCTURE (weight: 25%)
# ============================================================================

def score_vol_structure(as_of_date=None):
    """
    Assess forward-looking volatility regime via VRP and VIX dynamics.

    Sub-scores (each [-1, +1]):
      - VRP: implied vol (VIX) minus realized vol — high VRP = fear overpriced = bullish
      - VIX z-score: current VIX vs trailing distribution — contrarian
      - VIX momentum: 5-day change — rising VIX = worsening = bearish

    Returns:
        (score: float [-1,+1], details: dict)
        Positive = favorable vol regime, Negative = unfavorable
    """
    logger.info("Scoring vol structure...")
    ref_date = as_of_date or datetime.now()
    details = {}
    sub_scores = []

    end_str = ref_date.strftime('%Y-%m-%d')
    start_str = (ref_date - timedelta(days=180)).strftime('%Y-%m-%d')

    # Fetch VIX from FRED
    vix_series = fetch_fred_series(cfg.FRED_VIX_SERIES, start_str, end_str)

    # Fetch SPY for realized vol
    spy_df = download_ticker('SPY', start_str, end_str)

    # --- Sub-score 1: Volatility Risk Premium (VRP) ---
    if vix_series is not None and len(vix_series) >= 30 and spy_df is not None and len(spy_df) >= 30:
        vix_current = float(vix_series.iloc[-1])

        # Realized vol: annualized 20-day
        spy_returns = spy_df['Close'].pct_change().dropna()
        realized_vol = float(spy_returns.tail(cfg.VRP_REALIZED_VOL_WINDOW).std()) * np.sqrt(252) * 100

        vrp = vix_current - realized_vol

        # Z-score the VRP: compute trailing VRP series
        if len(vix_series) >= cfg.VRP_ZSCORE_WINDOW and len(spy_df) >= cfg.VRP_ZSCORE_WINDOW:
            # Build trailing VRP series for z-score
            spy_rv_series = spy_returns.rolling(cfg.VRP_REALIZED_VOL_WINDOW).std() * np.sqrt(252) * 100
            # Align VIX and RV on common dates
            common_dates = vix_series.index.intersection(spy_rv_series.index)
            if len(common_dates) >= cfg.VRP_ZSCORE_WINDOW:
                vrp_hist = vix_series.loc[common_dates] - spy_rv_series.loc[common_dates].values
                vrp_mean = float(vrp_hist.tail(cfg.VRP_ZSCORE_WINDOW).mean())
                vrp_std = float(vrp_hist.tail(cfg.VRP_ZSCORE_WINDOW).std())
                if vrp_std > 0.1:
                    vrp_zscore = (vrp - vrp_mean) / vrp_std
                    # High VRP z-score = fear is overpriced relative to reality = bullish
                    vrp_score = float(np.clip(vrp_zscore * 0.5, -1.0, 1.0))
                else:
                    vrp_score = 0.0
            else:
                # Fallback: raw VRP level. Average ~4-6 pts. Negative = bad.
                vrp_score = float(np.clip(vrp / 10.0, -1.0, 1.0))
        else:
            vrp_score = float(np.clip(vrp / 10.0, -1.0, 1.0))

        details['vix_level'] = round(vix_current, 2)
        details['realized_vol'] = round(realized_vol, 2)
        details['vrp'] = round(vrp, 2)
        details['vrp_score'] = round(vrp_score, 3)
        sub_scores.append(vrp_score)
    else:
        details['vrp_score'] = 0.0
        sub_scores.append(0.0)

    # --- Sub-score 2: VIX Z-score (contrarian: high VIX = fear peak = bullish) ---
    if vix_series is not None and len(vix_series) >= cfg.VIX_ZSCORE_WINDOW:
        vix_current = float(vix_series.iloc[-1])
        vix_trail = vix_series.tail(cfg.VIX_ZSCORE_WINDOW)
        vix_mean = float(vix_trail.mean())
        vix_std = float(vix_trail.std())

        if vix_std > 0.1:
            vix_z = (vix_current - vix_mean) / vix_std
            # CONTRARIAN: high VIX z-score = extreme fear = mean reversion = bullish
            vix_z_score = float(np.clip(vix_z * 0.4, -1.0, 1.0))
        else:
            vix_z_score = 0.0

        details['vix_zscore'] = round(vix_z if vix_std > 0.1 else 0.0, 3)
        details['vix_z_score'] = round(vix_z_score, 3)
        sub_scores.append(vix_z_score)
    else:
        details['vix_z_score'] = 0.0
        sub_scores.append(0.0)

    # --- Sub-score 3: VIX 5-day momentum (rising VIX = worsening) ---
    if vix_series is not None and len(vix_series) >= 6:
        vix_now = float(vix_series.iloc[-1])
        vix_5d_ago = float(vix_series.iloc[-6])
        if vix_5d_ago > 0:
            vix_mom = (vix_now / vix_5d_ago) - 1.0
            # Rising VIX = bad. Inverted: negative momentum = score positive.
            mom_score = float(np.clip(-vix_mom * 8.0, -1.0, 1.0))
        else:
            mom_score = 0.0

        details['vix_5d_change_pct'] = round(vix_mom * 100 if vix_5d_ago > 0 else 0.0, 2)
        details['vix_momentum_score'] = round(mom_score, 3)
        sub_scores.append(mom_score)
    else:
        details['vix_momentum_score'] = 0.0
        sub_scores.append(0.0)

    # Composite
    score = float(np.mean(sub_scores)) if sub_scores else 0.0
    score = float(np.clip(score, -1.0, 1.0))
    details['composite_score'] = round(score, 4)
    details['source'] = 'fred+massive'

    logger.info(f"Vol Structure score: {score:.4f}")
    return score, details


# ============================================================================
# NEW COMPONENT 2: CREDIT / MACRO (weight: 25%)
# ============================================================================

def score_credit_macro(as_of_date=None):
    """
    Assess institutional risk appetite via credit spreads and yield curve.

    Sub-scores (each [-1, +1]):
      - HY OAS z-score: widening spreads = risk-off = bearish
      - 3m10y yield spread: level and momentum (steepening = bullish)
      - HYG/LQD ratio: existing signal, kept as supplementary

    Returns:
        (score: float [-1,+1], details: dict)
    """
    logger.info("Scoring credit/macro...")
    ref_date = as_of_date or datetime.now()
    details = {}
    sub_scores = []

    end_str = ref_date.strftime('%Y-%m-%d')
    start_str = (ref_date - timedelta(days=180)).strftime('%Y-%m-%d')

    # --- Sub-score 1: HY OAS z-score ---
    hy_oas = fetch_fred_series(cfg.FRED_HY_OAS_SERIES, start_str, end_str)
    if hy_oas is not None and len(hy_oas) >= cfg.CREDIT_OAS_ZSCORE_WINDOW:
        oas_current = float(hy_oas.iloc[-1])
        oas_trail = hy_oas.tail(cfg.CREDIT_OAS_ZSCORE_WINDOW)
        oas_mean = float(oas_trail.mean())
        oas_std = float(oas_trail.std())

        if oas_std > 0.01:
            oas_z = (oas_current - oas_mean) / oas_std
            # Widening spreads (positive z) = risk-off = bearish → invert
            oas_score = float(np.clip(-oas_z * 0.5, -1.0, 1.0))
        else:
            oas_score = 0.0

        details['hy_oas_level'] = round(oas_current, 3)
        details['hy_oas_zscore'] = round(oas_z if oas_std > 0.01 else 0.0, 3)
        details['hy_oas_score'] = round(oas_score, 3)
        sub_scores.append(oas_score)
    else:
        details['hy_oas_score'] = 0.0
        sub_scores.append(0.0)

    # --- Sub-score 2: 3m10y Yield Curve ---
    yield_spread = fetch_fred_series(cfg.FRED_YIELD_3M10Y_SERIES, start_str, end_str)
    if yield_spread is not None and len(yield_spread) >= cfg.YIELD_CURVE_ZSCORE_WINDOW:
        yc_current = float(yield_spread.iloc[-1])
        yc_trail = yield_spread.tail(cfg.YIELD_CURVE_ZSCORE_WINDOW)
        yc_mean = float(yc_trail.mean())
        yc_std = float(yc_trail.std())

        # Level signal: positive spread = normal = bullish, negative = inverted = bearish
        level_score = float(np.clip(yc_current / 2.0, -1.0, 1.0))

        # Momentum signal: steepening (rising spread) = improving
        if len(yield_spread) >= cfg.YIELD_CURVE_MOMENTUM_DAYS + 1:
            yc_5d_ago = float(yield_spread.iloc[-cfg.YIELD_CURVE_MOMENTUM_DAYS - 1])
            yc_change = yc_current - yc_5d_ago
            change_score = float(np.clip(yc_change * 2.0, -1.0, 1.0))
        else:
            change_score = 0.0

        # Combine: 60% level, 40% momentum
        yc_score = float(np.clip(0.6 * level_score + 0.4 * change_score, -1.0, 1.0))

        details['yield_3m10y_level'] = round(yc_current, 3)
        details['yield_3m10y_change'] = round(yc_change if len(yield_spread) >= cfg.YIELD_CURVE_MOMENTUM_DAYS + 1 else 0.0, 3)
        details['yield_curve_score'] = round(yc_score, 3)
        sub_scores.append(yc_score)
    else:
        details['yield_curve_score'] = 0.0
        sub_scores.append(0.0)

    # --- Sub-score 3: HYG/LQD ratio (supplementary credit signal) ---
    hyg_df = download_ticker('HYG', start_str, end_str)
    lqd_df = download_ticker('LQD', start_str, end_str)
    if hyg_df is not None and lqd_df is not None:
        hyg_c = hyg_df['Close']
        lqd_c = lqd_df['Close']
        common_idx = hyg_c.index.intersection(lqd_c.index)
        if len(common_idx) >= 20:
            ratio = hyg_c.loc[common_idx] / lqd_c.loc[common_idx]
            ratio_current = float(ratio.iloc[-1])
            ratio_ma20 = float(ratio.rolling(20).mean().iloc[-1])
            credit_dev = (ratio_current / ratio_ma20 - 1.0) if ratio_ma20 > 0 else 0
            credit_score = float(np.clip(credit_dev * 20.0, -1.0, 1.0))

            details['hyg_lqd_ratio'] = round(ratio_current, 4)
            details['hyg_lqd_score'] = round(credit_score, 3)
            sub_scores.append(credit_score)
        else:
            details['hyg_lqd_score'] = 0.0
            sub_scores.append(0.0)
    else:
        details['hyg_lqd_score'] = 0.0
        sub_scores.append(0.0)

    # Composite
    score = float(np.mean(sub_scores)) if sub_scores else 0.0
    score = float(np.clip(score, -1.0, 1.0))
    details['composite_score'] = round(score, 4)
    details['source'] = 'fred+massive'

    logger.info(f"Credit/Macro score: {score:.4f}")
    return score, details


# ============================================================================
# NEW COMPONENT 3: BREADTH / INTERNALS (weight: 25%, improved)
# ============================================================================

def score_breadth_internals(as_of_date=None):
    """
    Assess market breadth using 200-day SMA (secular trend), McClellan, and
    52-week high/low proximity. Upgraded from old 50-day breadth.

    Sub-scores (each [-1, +1]):
      - 200-day breadth: % of sector ETFs above 200-day SMA
      - McClellan Oscillator approximation
      - 52-week high/low proximity

    Returns:
        (score: float [-1,+1], details: dict)
    """
    logger.info("Scoring breadth/internals (200-day)...")
    ref_date = as_of_date or datetime.now()
    details = {}
    sub_scores = []

    end_date = ref_date.strftime('%Y-%m-%d')
    start_date = (ref_date - timedelta(days=cfg.BREADTH_LOOKBACK_DAYS + 60)).strftime('%Y-%m-%d')

    spy_df = download_ticker('SPY', start_date, end_date)
    if spy_df is None or spy_df.empty:
        logger.error("Failed to download SPY data for breadth")
        return 0.0, {'error': 'SPY data unavailable'}

    breadth_tickers = [
        'XLK', 'XLF', 'XLV', 'XLE', 'XLI', 'XLY', 'XLP', 'XLB', 'XLU', 'XLRE', 'XLC',
        'RSP', 'IWM', 'MDY',
    ]
    data = download_multiple_tickers(breadth_tickers, start_date, end_date)

    if len(data) < 5:
        logger.warning(f"Only {len(data)} breadth tickers downloaded")
        if len(data) == 0:
            return 0.0, {'error': 'insufficient breadth data'}

    # --- Sub-score 1: 200-day Breadth ---
    above_sma_count = 0
    total_counted = 0
    for ticker, df in data.items():
        if len(df) < cfg.BREADTH_SMA_WINDOW:
            continue
        sma = df['Close'].rolling(cfg.BREADTH_SMA_WINDOW).mean()
        if pd.notna(sma.iloc[-1]) and pd.notna(df['Close'].iloc[-1]):
            total_counted += 1
            if df['Close'].iloc[-1] > sma.iloc[-1]:
                above_sma_count += 1

    if total_counted > 0:
        breadth_pct = above_sma_count / total_counted
        breadth_score = (breadth_pct - 0.5) * 2.0
        details['breadth_pct_above_sma200'] = round(breadth_pct * 100, 1)
        details['breadth_score'] = round(breadth_score, 3)
        sub_scores.append(breadth_score)
    else:
        details['breadth_score'] = 0.0
        sub_scores.append(0.0)

    # --- Sub-score 2: 52-week High/Low Proximity ---
    near_high = 0
    near_low = 0
    for ticker, df in data.items():
        if len(df) < 200:
            continue
        high_252 = df['High'].rolling(252, min_periods=200).max().iloc[-1]
        low_252 = df['Low'].rolling(252, min_periods=200).min().iloc[-1]
        current = df['Close'].iloc[-1]
        if pd.isna(high_252) or pd.isna(low_252):
            continue
        range_252 = high_252 - low_252
        if range_252 > 0:
            position = (current - low_252) / range_252
            if position > 0.9:
                near_high += 1
            elif position < 0.1:
                near_low += 1

    if near_high + near_low > 0:
        hl_score = (near_high - near_low) / max(near_high + near_low, 1)
    else:
        if len(spy_df) >= 200:
            high_252 = spy_df['High'].rolling(252, min_periods=200).max().iloc[-1]
            low_252 = spy_df['Low'].rolling(252, min_periods=200).min().iloc[-1]
            current = spy_df['Close'].iloc[-1]
            range_252 = high_252 - low_252
            hl_score = ((current - low_252) / range_252 - 0.5) * 2.0 if range_252 > 0 else 0.0
        else:
            hl_score = 0.0

    details['near_52w_high'] = near_high
    details['near_52w_low'] = near_low
    details['high_low_score'] = round(float(np.clip(hl_score, -1.0, 1.0)), 3)
    sub_scores.append(float(np.clip(hl_score, -1.0, 1.0)))

    # --- Sub-score 3: McClellan Oscillator Approximation ---
    if len(data) >= 5:
        ad_daily = []
        min_len = min(len(df) for df in data.values())
        lookback = min(cfg.MCCLELLAN_SLOW + 10, min_len - 1)

        for i in range(lookback, 0, -1):
            day_adv = 0
            day_dec = 0
            for ticker, df in data.items():
                if len(df) > i + 1:
                    ret = (df['Close'].iloc[-i] / df['Close'].iloc[-i - 1]) - 1
                    if ret > 0:
                        day_adv += 1
                    elif ret < 0:
                        day_dec += 1
            ad_daily.append(day_adv - day_dec)

        if len(ad_daily) >= cfg.MCCLELLAN_SLOW:
            ad_series = pd.Series(ad_daily)
            ema_fast = ad_series.ewm(span=cfg.MCCLELLAN_FAST, adjust=False).mean().iloc[-1]
            ema_slow = ad_series.ewm(span=cfg.MCCLELLAN_SLOW, adjust=False).mean().iloc[-1]
            mcclellan = ema_fast - ema_slow
            max_range = len(data) * 0.75
            mcclellan_score = float(np.clip(mcclellan / max(max_range, 1), -1.0, 1.0))
            details['mcclellan_oscillator'] = round(float(mcclellan), 3)
            details['mcclellan_score'] = round(mcclellan_score, 3)
            sub_scores.append(mcclellan_score)
        else:
            details['mcclellan_score'] = 0.0
            sub_scores.append(0.0)
    else:
        details['mcclellan_score'] = 0.0
        sub_scores.append(0.0)

    # Composite
    score = float(np.mean(sub_scores)) if sub_scores else 0.0
    score = float(np.clip(score, -1.0, 1.0))
    details['composite_score'] = round(score, 4)
    details['source'] = 'massive'

    logger.info(f"Breadth/Internals score: {score:.4f}")
    return score, details


# ============================================================================
# NEW COMPONENT 4: MEAN REVERSION (weight: 25%)
# ============================================================================

def score_mean_reversion(as_of_date=None):
    """
    Assess mean-reversion signals — the anti-trend component.
    This directly addresses the pipeline's main failure: going bearish after selloffs.

    Sub-scores (each [-1, +1]):
      - SPY distance from 20-week SMA: oversold = bullish (contrarian)
      - Weekly RSI(14): oversold (<30) = bullish, overbought (>70) = bearish
      - Bollinger Band width position: outside lower band = bullish

    Returns:
        (score: float [-1,+1], details: dict)
        Positive = oversold / likely reversion up, Negative = overbought
    """
    logger.info("Scoring mean reversion...")
    ref_date = as_of_date or datetime.now()
    details = {}
    sub_scores = []

    end_str = ref_date.strftime('%Y-%m-%d')
    start_str = (ref_date - timedelta(days=cfg.MEAN_REV_LOOKBACK_DAYS + 60)).strftime('%Y-%m-%d')

    spy_df = download_ticker('SPY', start_str, end_str)
    if spy_df is None or len(spy_df) < cfg.MEAN_REV_SMA_WINDOW:
        logger.error("Insufficient SPY data for mean reversion")
        return 0.0, {'error': 'SPY data insufficient'}

    close = spy_df['Close']

    # --- Sub-score 1: Distance from 20-week SMA (100-day) ---
    sma = close.rolling(cfg.MEAN_REV_SMA_WINDOW).mean()
    if pd.notna(sma.iloc[-1]) and sma.iloc[-1] > 0:
        current = float(close.iloc[-1])
        sma_val = float(sma.iloc[-1])
        deviation = (current / sma_val) - 1.0  # e.g., -0.05 = 5% below SMA

        # CONTRARIAN: below SMA = oversold = positive score (bullish reversion)
        # Above SMA = overbought = slightly negative
        # Scale: 5% below → +1.0, 5% above → -1.0
        dev_score = float(np.clip(-deviation * 20.0, -1.0, 1.0))

        details['spy_vs_sma100_pct'] = round(deviation * 100, 2)
        details['sma_deviation_score'] = round(dev_score, 3)
        sub_scores.append(dev_score)
    else:
        details['sma_deviation_score'] = 0.0
        sub_scores.append(0.0)

    # --- Sub-score 2: RSI(14) on daily data ---
    if len(close) >= cfg.MEAN_REV_RSI_WINDOW + 1:
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)

        avg_gain = gain.rolling(cfg.MEAN_REV_RSI_WINDOW, min_periods=cfg.MEAN_REV_RSI_WINDOW).mean()
        avg_loss = loss.rolling(cfg.MEAN_REV_RSI_WINDOW, min_periods=cfg.MEAN_REV_RSI_WINDOW).mean()

        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi_current = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50.0

        # CONTRARIAN: RSI < 30 = oversold = bullish (+1), RSI > 70 = overbought = bearish (-1)
        # Map [30, 70] linearly to [+1, -1], clamped
        rsi_score = float(np.clip((50 - rsi_current) / 20.0, -1.0, 1.0))

        details['rsi_14'] = round(rsi_current, 1)
        details['rsi_score'] = round(rsi_score, 3)
        sub_scores.append(rsi_score)
    else:
        details['rsi_score'] = 0.0
        sub_scores.append(0.0)

    # --- Sub-score 3: Bollinger Band position ---
    if len(close) >= cfg.MEAN_REV_BB_WINDOW:
        bb_sma = close.rolling(cfg.MEAN_REV_BB_WINDOW).mean()
        bb_std = close.rolling(cfg.MEAN_REV_BB_WINDOW).std()
        upper = bb_sma + cfg.MEAN_REV_BB_STD * bb_std
        lower = bb_sma - cfg.MEAN_REV_BB_STD * bb_std

        if pd.notna(upper.iloc[-1]) and pd.notna(lower.iloc[-1]) and upper.iloc[-1] > lower.iloc[-1]:
            current = float(close.iloc[-1])
            bb_upper = float(upper.iloc[-1])
            bb_lower = float(lower.iloc[-1])
            bb_mid = float(bb_sma.iloc[-1])

            # Position within bands: 0 = at lower, 1 = at upper
            bb_pos = (current - bb_lower) / (bb_upper - bb_lower)
            # CONTRARIAN: below midpoint = bullish, above = bearish
            bb_score = float(np.clip((0.5 - bb_pos) * 2.0, -1.0, 1.0))

            details['bb_position'] = round(bb_pos, 3)
            details['bb_score'] = round(bb_score, 3)
            sub_scores.append(bb_score)
        else:
            details['bb_score'] = 0.0
            sub_scores.append(0.0)
    else:
        details['bb_score'] = 0.0
        sub_scores.append(0.0)

    # Composite
    score = float(np.mean(sub_scores)) if sub_scores else 0.0
    score = float(np.clip(score, -1.0, 1.0))
    details['composite_score'] = round(score, 4)
    details['source'] = 'massive'

    logger.info(f"Mean Reversion score: {score:.4f}")
    return score, details


# ============================================================================
# LEGACY COMPONENT 1: MARKET INTERNALS (weight: 35% in legacy mode)
# ============================================================================

def score_market_internals(as_of_date=None):
    """
    Assess market health via internal breadth indicators.

    Sub-scores (each normalized to [-1, +1]):
      - Market breadth: % of SPY constituents above 50-day SMA
      - Advance/Decline: ratio of advancing vs declining stocks (recent 5 days)
      - 52-week highs vs lows: ratio from constituent price data
      - McClellan Oscillator approximation

    Args:
        as_of_date: datetime — treat this as "today" for all data. Defaults to now.

    Returns:
        (score: float [-1,+1], details: dict)
    """
    logger.info("Scoring market internals...")
    ref_date = as_of_date or datetime.now()
    details = {}
    sub_scores = []

    end_date = ref_date.strftime('%Y-%m-%d')
    start_date = (ref_date - timedelta(days=cfg.BREADTH_LOOKBACK_DAYS + 60)).strftime('%Y-%m-%d')

    # Download SPY for index reference
    spy_df = download_ticker('SPY', start_date, end_date)
    if spy_df is None or spy_df.empty:
        logger.error("Failed to download SPY data for internals")
        return 0.0, {'error': 'SPY data unavailable'}

    # Get SPY constituent list (use a representative sample for speed)
    # Download a broad market ETF set to approximate breadth
    breadth_tickers = [
        'XLK', 'XLF', 'XLV', 'XLE', 'XLI', 'XLY', 'XLP', 'XLB', 'XLU', 'XLRE', 'XLC',  # sector ETFs
        'RSP',  # equal-weight S&P 500
        'IWM',  # Russell 2000
        'MDY',  # S&P 400 Mid-Cap
    ]

    data = download_multiple_tickers(breadth_tickers, start_date, end_date)

    if len(data) < 5:
        logger.warning(f"Only {len(data)} breadth tickers downloaded, using limited data")
        if len(data) == 0:
            return 0.0, {'error': 'insufficient breadth data'}

    # --- Sub-score 1: Sector Breadth (% of sectors above 50-day SMA) ---
    above_sma_count = 0
    total_counted = 0
    for ticker, df in data.items():
        if len(df) < cfg.BREADTH_SMA_WINDOW:
            continue
        sma = df['Close'].rolling(cfg.BREADTH_SMA_WINDOW).mean()
        if pd.notna(sma.iloc[-1]) and pd.notna(df['Close'].iloc[-1]):
            total_counted += 1
            if df['Close'].iloc[-1] > sma.iloc[-1]:
                above_sma_count += 1

    if total_counted > 0:
        breadth_pct = above_sma_count / total_counted
        # Map [0, 1] → [-1, +1]: 50% above SMA = neutral (0)
        breadth_score = (breadth_pct - 0.5) * 2.0
        details['breadth_pct_above_sma50'] = round(breadth_pct * 100, 1)
        details['breadth_score'] = round(breadth_score, 3)
        sub_scores.append(breadth_score)
    else:
        details['breadth_score'] = 0.0
        sub_scores.append(0.0)

    # --- Sub-score 2: Advance/Decline (5-day net) ---
    recent_days = 5
    advancing = 0
    declining = 0
    for ticker, df in data.items():
        if len(df) < recent_days + 1:
            continue
        ret = (df['Close'].iloc[-1] / df['Close'].iloc[-recent_days - 1]) - 1
        if ret > 0.001:
            advancing += 1
        elif ret < -0.001:
            declining += 1

    if advancing + declining > 0:
        ad_ratio = (advancing - declining) / (advancing + declining)
        details['advancing'] = advancing
        details['declining'] = declining
        details['ad_ratio_score'] = round(ad_ratio, 3)
        sub_scores.append(np.clip(ad_ratio, -1.0, 1.0))
    else:
        details['ad_ratio_score'] = 0.0
        sub_scores.append(0.0)

    # --- Sub-score 3: 52-week High/Low Proximity ---
    near_high = 0
    near_low = 0
    for ticker, df in data.items():
        if len(df) < 200:
            continue
        high_252 = df['High'].rolling(252, min_periods=200).max().iloc[-1]
        low_252 = df['Low'].rolling(252, min_periods=200).min().iloc[-1]
        current = df['Close'].iloc[-1]
        if pd.isna(high_252) or pd.isna(low_252):
            continue
        range_252 = high_252 - low_252
        if range_252 > 0:
            position = (current - low_252) / range_252  # 0 = at low, 1 = at high
            if position > 0.9:
                near_high += 1
            elif position < 0.1:
                near_low += 1

    if near_high + near_low > 0:
        hl_score = (near_high - near_low) / max(near_high + near_low, 1)
    else:
        # If no extreme readings, use SPY's position in its range
        if len(spy_df) >= 200:
            high_252 = spy_df['High'].rolling(252, min_periods=200).max().iloc[-1]
            low_252 = spy_df['Low'].rolling(252, min_periods=200).min().iloc[-1]
            current = spy_df['Close'].iloc[-1]
            range_252 = high_252 - low_252
            if range_252 > 0:
                hl_score = ((current - low_252) / range_252 - 0.5) * 2.0
            else:
                hl_score = 0.0
        else:
            hl_score = 0.0

    details['near_52w_high'] = near_high
    details['near_52w_low'] = near_low
    details['high_low_score'] = round(np.clip(hl_score, -1.0, 1.0), 3)
    sub_scores.append(np.clip(hl_score, -1.0, 1.0))

    # --- Sub-score 4: McClellan Oscillator Approximation ---
    # Use daily advance-decline difference from sector ETFs over past 39 days
    if len(data) >= 5:
        ad_daily = []
        min_len = min(len(df) for df in data.values())
        lookback = min(cfg.MCCLELLAN_SLOW + 10, min_len - 1)

        for i in range(lookback, 0, -1):
            day_adv = 0
            day_dec = 0
            for ticker, df in data.items():
                if len(df) > i + 1:
                    ret = (df['Close'].iloc[-i] / df['Close'].iloc[-i - 1]) - 1
                    if ret > 0:
                        day_adv += 1
                    elif ret < 0:
                        day_dec += 1
            ad_daily.append(day_adv - day_dec)

        if len(ad_daily) >= cfg.MCCLELLAN_SLOW:
            ad_series = pd.Series(ad_daily)
            ema_fast = ad_series.ewm(span=cfg.MCCLELLAN_FAST, adjust=False).mean().iloc[-1]
            ema_slow = ad_series.ewm(span=cfg.MCCLELLAN_SLOW, adjust=False).mean().iloc[-1]
            mcclellan = ema_fast - ema_slow
            # Normalize: typical range is roughly [-10, +10] for 14 ETFs
            max_range = len(data) * 0.75
            mcclellan_score = np.clip(mcclellan / max(max_range, 1), -1.0, 1.0)
            details['mcclellan_oscillator'] = round(float(mcclellan), 3)
            details['mcclellan_score'] = round(float(mcclellan_score), 3)
            sub_scores.append(float(mcclellan_score))
        else:
            details['mcclellan_score'] = 0.0
            sub_scores.append(0.0)
    else:
        details['mcclellan_score'] = 0.0
        sub_scores.append(0.0)

    # Composite
    score = float(np.mean(sub_scores)) if sub_scores else 0.0
    score = float(np.clip(score, -1.0, 1.0))
    details['composite_score'] = round(score, 4)
    details['sub_score_count'] = len(sub_scores)

    logger.info(f"Market Internals score: {score:.4f}")
    return score, details


# ============================================================================
# COMPONENT 2: GEOPOLITICAL / GLOBAL RISK (weight: 25%)
# ============================================================================

def score_geopolitical_risk(as_of_date=None):
    """
    Assess global risk via proxy indicators.

    Sub-scores:
      - VIX level relative to thresholds and its 20-day average
      - Credit spreads: HYG/LQD ratio vs historical
      - Gold momentum: rising gold = risk-off
      - Oil volatility: USO realized vol
      - Dollar strength: DXY trend

    Args:
        as_of_date: datetime — treat this as "today" for all data. Defaults to now.

    Returns:
        (score: float [-1,+1], details: dict)
        Positive = risk-on (healthy), Negative = risk-off (unhealthy)
    """
    logger.info("Scoring geopolitical risk...")
    ref_date = as_of_date or datetime.now()
    details = {}
    sub_scores = []

    end_date = ref_date.strftime('%Y-%m-%d')
    start_date = (ref_date - timedelta(days=90)).strftime('%Y-%m-%d')

    data = download_multiple_tickers(cfg.GEO_TICKERS, start_date, end_date)

    # --- Sub-score 1: VIX Proxy (VIXY) ---
    # Uses momentum & relative position since VIXY price ≠ VIX index level
    if 'VIX' in data and len(data['VIX']) >= cfg.GEO_AVG_WINDOW:
        vix_df = data['VIX']
        vix_current = float(vix_df['Close'].iloc[-1])
        vix_ma20 = float(vix_df['Close'].rolling(cfg.GEO_AVG_WINDOW).mean().iloc[-1])

        # Score 1: Current vs 20-day MA — above MA = elevated fear = negative
        vix_vs_ma = (vix_current / vix_ma20) - 1.0 if vix_ma20 > 0 else 0
        # 10% above MA → score of -1, 10% below → score of +1
        ma_score = np.clip(-vix_vs_ma * 10.0, -1.0, 1.0)

        # Score 2: 5-day momentum — rising = worsening
        if len(vix_df) >= 6:
            vix_5d_ret = (vix_df['Close'].iloc[-1] / vix_df['Close'].iloc[-6]) - 1
            momentum_score = np.clip(float(-vix_5d_ret) * 8.0, -1.0, 1.0)
        else:
            momentum_score = 0.0

        # Score 3: Position in 60-day range
        vix_60d = vix_df['Close'].tail(60)
        if len(vix_60d) >= 20:
            range_high = vix_60d.max()
            range_low = vix_60d.min()
            if range_high > range_low:
                position = (vix_current - range_low) / (range_high - range_low)
                # High in range = fear = negative
                range_score = float((0.5 - position) * 2.0)
            else:
                range_score = 0.0
        else:
            range_score = 0.0

        vix_score = np.clip(0.4 * ma_score + 0.35 * momentum_score + 0.25 * range_score, -1.0, 1.0)

        details['vixy_current'] = round(vix_current, 2)
        details['vixy_ma20'] = round(vix_ma20, 2)
        details['vixy_vs_ma_pct'] = round(vix_vs_ma * 100, 1)
        details['vix_score'] = round(float(vix_score), 3)
        sub_scores.append(float(vix_score))
    else:
        details['vix_score'] = 0.0
        sub_scores.append(0.0)

    # --- Sub-score 2: Credit Spreads (HYG/LQD) ---
    if 'HYG' in data and 'LQD' in data:
        hyg = data['HYG']['Close']
        lqd = data['LQD']['Close']
        # Align indexes
        common_idx = hyg.index.intersection(lqd.index)
        if len(common_idx) >= 20:
            ratio = hyg.loc[common_idx] / lqd.loc[common_idx]
            ratio_current = float(ratio.iloc[-1])
            ratio_ma20 = float(ratio.rolling(20).mean().iloc[-1])

            # Higher ratio = risk-on (HY outperforming IG)
            # Compare current to moving average
            credit_deviation = (ratio_current / ratio_ma20 - 1.0) if ratio_ma20 > 0 else 0
            credit_score = np.clip(credit_deviation * 20.0, -1.0, 1.0)

            details['hyg_lqd_ratio'] = round(ratio_current, 4)
            details['hyg_lqd_ma20'] = round(ratio_ma20, 4)
            details['credit_score'] = round(float(credit_score), 3)
            sub_scores.append(float(credit_score))
        else:
            details['credit_score'] = 0.0
            sub_scores.append(0.0)
    else:
        details['credit_score'] = 0.0
        sub_scores.append(0.0)

    # --- Sub-score 3: Gold Momentum (rising gold = risk-off = negative) ---
    if 'GLD' in data and len(data['GLD']) >= cfg.GEO_MOMENTUM_WINDOW + 1:
        gld = data['GLD']['Close']
        gld_ret = (gld.iloc[-1] / gld.iloc[-cfg.GEO_MOMENTUM_WINDOW - 1]) - 1
        # Invert: rising gold → negative score (risk-off)
        gold_score = np.clip(float(-gld_ret) * 10.0, -1.0, 1.0)

        details['gld_5d_return'] = round(float(gld_ret) * 100, 2)
        details['gold_score'] = round(float(gold_score), 3)
        sub_scores.append(float(gold_score))
    else:
        details['gold_score'] = 0.0
        sub_scores.append(0.0)

    # --- Sub-score 4: Oil Volatility (high vol = uncertainty = negative) ---
    if 'USO' in data and len(data['USO']) >= 20:
        uso = data['USO']['Close']
        oil_returns = uso.pct_change().dropna()
        oil_vol = float(oil_returns.tail(20).std()) * np.sqrt(252)  # annualized
        # Typical oil vol: 20-40%. >50% = stressed
        oil_score = np.clip(1.0 - (oil_vol - 0.20) / 0.30, -1.0, 1.0)

        details['oil_annual_vol'] = round(oil_vol * 100, 1)
        details['oil_score'] = round(float(oil_score), 3)
        sub_scores.append(float(oil_score))
    else:
        details['oil_score'] = 0.0
        sub_scores.append(0.0)

    # --- Sub-score 5: Dollar Strength (strong dollar often = risk-off for equities) ---
    if 'DXY' in data and len(data['DXY']) >= cfg.GEO_MOMENTUM_WINDOW + 1:
        dxy = data['DXY']['Close']
        dxy_ret = (dxy.iloc[-1] / dxy.iloc[-cfg.GEO_MOMENTUM_WINDOW - 1]) - 1
        # Strengthening dollar → slight negative for equities
        dxy_score = np.clip(float(-dxy_ret) * 15.0, -1.0, 1.0)

        details['dxy_5d_return'] = round(float(dxy_ret) * 100, 2)
        details['dxy_score'] = round(float(dxy_score), 3)
        sub_scores.append(float(dxy_score))
    else:
        details['dxy_score'] = 0.0
        sub_scores.append(0.0)

    # Composite
    score = float(np.mean(sub_scores)) if sub_scores else 0.0
    score = float(np.clip(score, -1.0, 1.0))
    details['composite_score'] = round(score, 4)

    logger.info(f"Geopolitical Risk score: {score:.4f}")
    return score, details


# ============================================================================
# COMPONENT 3: NEWS SENTIMENT (weight: 20%)
# ============================================================================

def score_news_sentiment(as_of_date=None):
    """
    Assess market sentiment from recent financial news headlines via MarketAux API.

    Metrics:
      - Average headline sentiment
      - Ratio of negative to positive articles
      - Presence of crisis keywords

    Args:
        as_of_date: datetime — treat this as "today" for all data. Defaults to now.
            When set to a past date, forces VIXY fallback (MarketAux has no historical data).

    Returns:
        (score: float [-1,+1], details: dict)
    """
    logger.info("Scoring news sentiment...")
    ref_date = as_of_date or datetime.now()
    details = {}

    # Force VIXY fallback for historical backtesting (MarketAux has no historical headlines)
    is_historical = as_of_date is not None and as_of_date.date() < datetime.now().date()

    if not cfg.MARKETAUX_API_KEY or is_historical:
        if is_historical:
            logger.info("Historical date — using VIXY fallback (no historical news API)")
        else:
            logger.warning("MARKETAUX_API_KEY not set — using VIX-based fallback")
        return _news_fallback_via_vix(as_of_date=ref_date)

    try:
        published_after = (ref_date - timedelta(days=cfg.NEWS_LOOKBACK_DAYS)).strftime('%Y-%m-%dT00:00')

        params = {
            'api_token': cfg.MARKETAUX_API_KEY,
            'language': 'en',
            'filter_entities': 'true',
            'published_after': published_after,
            'limit': cfg.NEWS_MAX_ARTICLES,
            'domains': 'reuters.com,cnbc.com,bloomberg.com,wsj.com,marketwatch.com,finance.yahoo.com',
        }

        response = requests.get(cfg.MARKETAUX_BASE_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        articles = data.get('data', [])
        if not articles:
            logger.warning("No articles returned from MarketAux")
            return _news_fallback_via_vix(as_of_date=ref_date)

        # Extract sentiment scores
        sentiments = []
        crisis_count = 0
        positive_count = 0

        for article in articles:
            # MarketAux provides sentiment in entities or as overall
            sentiment_val = None

            # Check for entity-level sentiment
            entities = article.get('entities', [])
            for entity in entities:
                if 'sentiment_score' in entity:
                    sentiment_val = float(entity['sentiment_score'])
                    break

            # Fallback: check title for keyword sentiment
            title = (article.get('title', '') or '').lower()
            description = (article.get('description', '') or '').lower()
            text = title + ' ' + description

            if sentiment_val is not None:
                sentiments.append(sentiment_val)

            # Keyword detection
            for kw in cfg.NEWS_CRISIS_KEYWORDS:
                if kw in text:
                    crisis_count += 1
                    break
            for kw in cfg.NEWS_POSITIVE_KEYWORDS:
                if kw in text:
                    positive_count += 1
                    break

        # --- Calculate sub-scores ---
        # 1. Average sentiment
        if sentiments:
            avg_sentiment = float(np.mean(sentiments))
            # MarketAux sentiment is roughly [-1, +1] already
            sentiment_score = np.clip(avg_sentiment, -1.0, 1.0)
        else:
            sentiment_score = 0.0

        # 2. Crisis keyword ratio
        total_articles = len(articles)
        crisis_ratio = crisis_count / max(total_articles, 1)
        positive_ratio = positive_count / max(total_articles, 1)
        # High crisis ratio → negative; high positive → positive
        keyword_score = np.clip((positive_ratio - crisis_ratio) * 3.0, -1.0, 1.0)

        # Combine: 60% API sentiment + 40% keyword analysis
        if sentiments:
            score = 0.6 * sentiment_score + 0.4 * keyword_score
        else:
            score = keyword_score  # 100% keyword if no API sentiment available

        score = float(np.clip(score, -1.0, 1.0))

        details['articles_analyzed'] = total_articles
        details['avg_sentiment'] = round(float(sentiment_score), 3)
        details['crisis_keyword_count'] = crisis_count
        details['positive_keyword_count'] = positive_count
        details['keyword_score'] = round(float(keyword_score), 3)
        details['composite_score'] = round(score, 4)
        details['source'] = 'marketaux'

        logger.info(f"News Sentiment score: {score:.4f} ({total_articles} articles)")
        return score, details

    except requests.exceptions.RequestException as e:
        logger.warning(f"MarketAux API request failed: {e}")
        return _news_fallback_via_vix(as_of_date=ref_date)
    except (KeyError, ValueError, TypeError) as e:
        logger.warning(f"MarketAux response parsing error: {e}")
        return _news_fallback_via_vix(as_of_date=ref_date)


def _news_fallback_via_vix(as_of_date=None):
    """
    Fallback news sentiment using VIXY (VIX proxy ETF) price momentum.
    When news API is unavailable, rising VIXY = rising fear = negative sentiment.
    Uses momentum rather than absolute price since VIXY ≠ VIX level.
    """
    logger.info("Using VIXY momentum fallback for news sentiment")
    ref_date = as_of_date or datetime.now()

    end_date = ref_date.strftime('%Y-%m-%d')
    start_date = (ref_date - timedelta(days=30)).strftime('%Y-%m-%d')

    vixy_df = download_ticker('VIX', start_date, end_date)  # maps to VIXY via proxy
    if vixy_df is None or vixy_df.empty or len(vixy_df) < 6:
        return 0.0, {'error': 'VIXY unavailable for fallback', 'source': 'fallback_vixy'}

    # Use 5-day momentum: rising VIXY = rising fear = negative
    vixy_5d_ret = (vixy_df['Close'].iloc[-1] / vixy_df['Close'].iloc[-6]) - 1
    # Use 20-day position: where is VIXY relative to its recent range
    vixy_20d = vixy_df['Close'].tail(20)
    if len(vixy_20d) >= 10:
        range_high = vixy_20d.max()
        range_low = vixy_20d.min()
        if range_high > range_low:
            position = (vixy_df['Close'].iloc[-1] - range_low) / (range_high - range_low)
            # position 1.0 = at 20d high (max fear), 0.0 = at 20d low (calm)
            position_score = (0.5 - position) * 2.0  # invert: high position = negative
        else:
            position_score = 0.0
    else:
        position_score = 0.0

    # Momentum score: rising VIXY = negative
    momentum_score = float(np.clip(-vixy_5d_ret * 10.0, -1.0, 1.0))

    # Combine: 50% momentum + 50% range position
    score = float(np.clip(0.5 * momentum_score + 0.5 * position_score, -1.0, 1.0))

    details = {
        'vixy_5d_return': round(float(vixy_5d_ret) * 100, 2),
        'momentum_score': round(momentum_score, 3),
        'position_score': round(float(position_score), 3),
        'composite_score': round(score, 4),
        'source': 'fallback_vixy',
    }

    logger.info(f"News Sentiment (VIXY fallback) score: {score:.4f}")
    return score, details


# ============================================================================
# COMPONENT 4: ECONOMIC CALENDAR (weight: 20%)
# ============================================================================

def score_economic_calendar(as_of_date=None):
    """
    Assess upcoming economic event risk via FRED API.

    Metrics:
      - Number of high-impact events in next 7 calendar days
      - Weighted event density (FOMC/CPI/NFP weighted higher)
      - Recent data surprise factor (actual vs trend)

    Args:
        as_of_date: datetime — treat this as "today" for all data. Defaults to now.

    Returns:
        (score: float [-1,+1], details: dict)
    """
    logger.info("Scoring economic calendar...")
    ref_date = as_of_date or datetime.now()
    details = {}

    if not cfg.FRED_API_KEY:
        logger.warning("FRED_API_KEY not set — using static calendar fallback")
        return _econ_calendar_fallback()

    try:
        # Check FRED releases in the upcoming window
        now = ref_date
        horizon_end = now + timedelta(days=cfg.ECON_EVENT_HORIZON_DAYS)

        upcoming_events = []
        total_impact_weight = 0
        recent_surprises = []

        for series_id, name in cfg.HIGH_IMPACT_SERIES.items():
            impact = cfg.EVENT_IMPACT.get(series_id, 1)

            try:
                # Get the release dates for this series
                release_info = _fred_get_release_dates(series_id, ref_date=now)
                if release_info is None:
                    continue

                # Check if any release falls within our horizon
                for release_date in release_info:
                    if now.date() <= release_date <= horizon_end.date():
                        upcoming_events.append({
                            'series': series_id,
                            'name': name,
                            'date': release_date.isoformat(),
                            'impact': impact,
                        })
                        total_impact_weight += impact

                # Get recent surprise factor (last observation vs trend)
                surprise = _fred_get_surprise_factor(series_id, ref_date=now)
                if surprise is not None:
                    recent_surprises.append(surprise * impact)  # weight by impact

            except Exception as e:
                logger.debug(f"FRED error for {series_id}: {e}")
                continue

        # --- Score Calculation ---

        # 1. Event density score: more high-impact events → more uncertainty → lower score
        # Typical week: 2-4 events, impact_weight 3-8
        # Heavy week: 5+ events, impact_weight 10+
        density_score = np.clip(1.0 - (total_impact_weight / 10.0), -1.0, 0.5)

        # 2. Surprise factor: positive surprises = economy stronger than expected = bullish
        if recent_surprises:
            avg_surprise = float(np.mean(recent_surprises))
            surprise_score = np.clip(avg_surprise, -1.0, 1.0)
        else:
            surprise_score = 0.0

        # Combine: 60% density + 40% surprise
        score = 0.6 * density_score + 0.4 * surprise_score
        score = float(np.clip(score, -1.0, 1.0))

        details['upcoming_events'] = upcoming_events
        details['total_impact_weight'] = total_impact_weight
        details['density_score'] = round(float(density_score), 3)
        details['avg_surprise_score'] = round(float(surprise_score), 3)
        details['composite_score'] = round(score, 4)
        details['source'] = 'fred'

        logger.info(f"Economic Calendar score: {score:.4f} ({len(upcoming_events)} upcoming events)")
        return score, details

    except Exception as e:
        logger.warning(f"FRED API error: {e}")
        return _econ_calendar_fallback()


def _fred_get_release_dates(series_id, ref_date=None):
    """
    Get upcoming release dates for a FRED series.
    Returns list of datetime.date objects, or None.
    """
    ref = ref_date or datetime.now()
    try:
        # First, get the release ID for this series
        url = f"{cfg.FRED_BASE_URL}/series/release"
        params = {
            'series_id': series_id,
            'api_key': cfg.FRED_API_KEY,
            'file_type': 'json',
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        release_data = resp.json()

        releases = release_data.get('releases', [])
        if not releases:
            return None

        release_id = releases[0].get('id')
        if not release_id:
            return None

        # Get release dates
        url = f"{cfg.FRED_BASE_URL}/release/dates"
        params = {
            'release_id': release_id,
            'api_key': cfg.FRED_API_KEY,
            'file_type': 'json',
            'realtime_start': ref.strftime('%Y-%m-%d'),
            'realtime_end': (ref + timedelta(days=cfg.ECON_EVENT_HORIZON_DAYS + 7)).strftime('%Y-%m-%d'),
            'include_release_dates_with_no_data': 'true',
            'sort_order': 'asc',
            'limit': 5,
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        dates_data = resp.json()

        release_dates = []
        for rd in dates_data.get('release_dates', []):
            date_str = rd.get('date', '')
            if date_str:
                release_dates.append(datetime.strptime(date_str, '%Y-%m-%d').date())

        return release_dates if release_dates else None

    except Exception as e:
        logger.debug(f"FRED release dates error for {series_id}: {e}")
        return None


def _fred_get_surprise_factor(series_id, ref_date=None):
    """
    Estimate surprise factor for the most recent release.
    Compares last observation to the trend of prior observations.
    Returns float [-1, +1] where positive = better than trend.
    """
    ref = ref_date or datetime.now()
    try:
        url = f"{cfg.FRED_BASE_URL}/series/observations"
        params = {
            'series_id': series_id,
            'api_key': cfg.FRED_API_KEY,
            'file_type': 'json',
            'sort_order': 'desc',
            'limit': 12,  # last 12 observations for trend
            'observation_end': ref.strftime('%Y-%m-%d'),  # prevent future data leakage
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        obs_data = resp.json()

        observations = obs_data.get('observations', [])
        values = []
        for obs in observations:
            val = obs.get('value', '.')
            if val != '.':
                try:
                    values.append(float(val))
                except ValueError:
                    pass

        if len(values) < 3:
            return None

        # Latest vs mean of previous
        latest = values[0]
        prior_mean = float(np.mean(values[1:]))

        if prior_mean == 0:
            return 0.0

        # For unemployment/claims, LOWER is better (invert)
        invert_series = {'UNRATE', 'ICSA'}
        deviation = (latest - prior_mean) / abs(prior_mean)

        if series_id in invert_series:
            deviation = -deviation

        # Scale: 1% deviation → 0.5 surprise score
        return float(np.clip(deviation * 50.0, -1.0, 1.0))

    except Exception as e:
        logger.debug(f"FRED surprise factor error for {series_id}: {e}")
        return None


def _econ_calendar_fallback():
    """
    Fallback when FRED API unavailable.
    Returns neutral score with note about missing data.
    """
    logger.info("Using economic calendar fallback (neutral)")
    return 0.0, {
        'composite_score': 0.0,
        'source': 'fallback_neutral',
        'note': 'FRED API unavailable — assuming neutral economic outlook',
    }


# ============================================================================
# COMPOSITE SCORING & SIGMOID SCALING
# ============================================================================

def compute_health_score(component_scores, weights=None):
    """
    Combine individual component scores into a weighted composite.

    Args:
        component_scores: dict of {component_name: (score, details)}
        weights: dict of {component_name: weight}. Defaults to cfg.COMPONENT_WEIGHTS.

    Returns:
        float: composite health score [-1, +1]
    """
    if weights is None:
        weights = cfg.COMPONENT_WEIGHTS

    weighted_sum = 0.0
    total_weight = 0.0

    for component, (score, _details) in component_scores.items():
        weight = weights.get(component, 0.0)
        weighted_sum += score * weight
        total_weight += weight

    if total_weight > 0:
        return weighted_sum / total_weight
    return 0.0


def _compute_kelly_fraction(health_score, historical_scores, historical_returns):
    """
    Compute discrete Kelly fraction f* from trailing data (walk-forward).

    Uses linear probability model to estimate signal-conditional win rate W,
    pooled payoff ratio R, then f* = W - (1-W)/R.

    Returns f* (typically -0.15 to +0.25 for weekly SPY) or None if insufficient data.
    """
    n = len(historical_scores)
    if n < cfg.KELLY_MIN_OBS:
        return None

    scores = np.array(historical_scores, dtype=float)
    returns = np.array(historical_returns, dtype=float)

    # Signal-conditional win rate via linear probability model
    wins = (returns > 0).astype(float)
    score_var = np.var(scores)
    if score_var < 1e-10:
        W = float(np.mean(wins))
    else:
        b = float(np.cov(scores, wins)[0, 1] / score_var)
        a = float(np.mean(wins) - b * np.mean(scores))
        W = a + b * health_score
        W = max(0.01, min(0.99, W))

    # Payoff ratio: pooled from trailing window
    winning = returns[returns > 0]
    losing = returns[returns <= 0]
    if len(winning) == 0 or len(losing) == 0:
        return None
    avg_win = float(np.mean(winning))
    avg_loss = float(np.mean(np.abs(losing)))
    if avg_loss < 1e-10:
        return None
    R = avg_win / avg_loss

    # Discrete Kelly: f* = W - (1 - W) / R
    f_star = W - (1.0 - W) / R
    return f_star


def health_to_allocation_kelly_scaled(health_score, historical_scores, historical_returns):
    """
    Kelly Scaled: map discrete Kelly f* from [0, THEORETICAL_MAX] → [FLOOR, CAP].

    Negative f* → floor (50%). Positive f* scaled linearly.
    Never reaches 100% — only approaches CAP (99.99%) at theoretical max edge.
    """
    f_star = _compute_kelly_fraction(health_score, historical_scores, historical_returns)
    if f_star is None:
        return cfg.KELLY_CAP  # burn-in default

    if f_star <= 0:
        return cfg.KELLY_FLOOR

    alloc = cfg.KELLY_FLOOR + (f_star / cfg.KELLY_THEORETICAL_MAX) * (cfg.KELLY_CAP - cfg.KELLY_FLOOR)
    alloc = max(cfg.KELLY_FLOOR, min(cfg.KELLY_CAP, alloc))
    return round(alloc, 4)



# ============================================================================
# ADVANCED KELLY VARIANTS
# ============================================================================

def health_to_allocation_kelly_voltarget(health_score, historical_scores, historical_returns,
                                          spy_daily, as_of_date):
    """
    Volatility-Targeted Kelly: scale allocation to maintain consistent portfolio vol.
    allocation = kelly_scaled * (target_vol / realized_vol)
    """
    base_alloc = health_to_allocation_kelly_scaled(
        health_score, historical_scores, historical_returns)

    if spy_daily is None or len(spy_daily) < cfg.KELLY_VOL_REALIZED_WINDOW + 5:
        return base_alloc

    as_of = pd.Timestamp(as_of_date)
    spy_avail = spy_daily[spy_daily.index <= as_of]
    if len(spy_avail) < cfg.KELLY_VOL_REALIZED_WINDOW + 1:
        return base_alloc

    closes = spy_avail['Close'].iloc[-(cfg.KELLY_VOL_REALIZED_WINDOW + 1):]
    daily_returns = np.log(closes / closes.shift(1)).dropna()
    realized_vol = float(daily_returns.std() * np.sqrt(252))

    if realized_vol < 0.01:
        return base_alloc

    vol_ratio = cfg.KELLY_VOL_TARGET / realized_vol
    alloc = base_alloc * vol_ratio
    alloc = max(cfg.KELLY_VOL_FLOOR, min(cfg.KELLY_CAP, alloc))
    return round(alloc, 4)


def health_to_allocation_kelly_voltarget_mhvrp(health_score, historical_scores, historical_returns,
                                                  spy_daily, vix_series, as_of_date,
                                                  vix_futures=None):
    """
    Multi-Horizon VRP Crossover with VIX Futures Term Structure.

    When real VIX futures data (VX1/VX2) is available:
        Uses actual term structure spread (VX2-VX1)/VX1 for contango/backwardation
        detection, plus VRP (VX1 implied vol vs 20d realized vol).

    Fallback (spot VIX proxy):
        Compares short-term VRP (VIX - RV_10d) vs long-term VRP (VIX - RV_60d).

    Regimes:
        Backwardation + positive VRP → temp spike, expected to revert → boost
        Steep contango + negative VRP → structural stress → drag
        Neutral → standard Kelly VT allocation
    """
    # Start from standard Kelly VT
    vt_alloc = health_to_allocation_kelly_voltarget(
        health_score, historical_scores, historical_returns,
        spy_daily, as_of_date)

    if spy_daily is None:
        return vt_alloc

    as_of = pd.Timestamp(as_of_date)
    spy_avail = spy_daily[spy_daily.index <= as_of]
    long_w = cfg.KELLY_MHVRP_LONG_RV_WINDOW
    if len(spy_avail) < long_w + 1:
        return vt_alloc

    # --- Path A: Real VIX futures term structure ---
    if vix_futures is not None:
        vf_avail = vix_futures[vix_futures.index <= as_of]
        if len(vf_avail) >= 5:
            vx1 = float(vf_avail['VX1_Close'].iloc[-1])  # front month
            vx2 = float(vf_avail['VX2_Close'].iloc[-1])  # second month

            if vx1 > 0:
                # True contango/backwardation from actual futures curve
                term_spread = (vx2 - vx1) / vx1

                # VRP: VX1 implied vol vs 20d realized vol
                implied_vol = vx1 / 100.0
                closes = spy_avail['Close'].iloc[-(cfg.KELLY_VOL_REALIZED_WINDOW + 1):]
                daily_returns = np.log(closes / closes.shift(1)).dropna()
                rv_20d = float(daily_returns.std() * np.sqrt(252))
                vrp = implied_vol - rv_20d

                # Regime detection using real term structure
                if term_spread < cfg.KELLY_MHVRP_BACKWARDATION_SPREAD and vrp > 0:
                    boost = cfg.KELLY_MHVRP_BACKWARDATION_BOOST
                elif term_spread > cfg.KELLY_MHVRP_CONTANGO_SPREAD and vrp < 0:
                    boost = cfg.KELLY_MHVRP_CONTANGO_DRAG
                else:
                    boost = 1.0

                alloc = vt_alloc * boost
                alloc = max(cfg.KELLY_MHVRP_FLOOR, min(cfg.KELLY_CAP, alloc))
                return round(alloc, 4)

    # --- Path B: Fallback to spot VIX proxy ---
    if vix_series is None:
        return vt_alloc

    vix_available = vix_series[vix_series.index <= as_of]
    if len(vix_available) < 10:
        return vt_alloc

    current_vix = float(vix_available.iloc[-1]) / 100.0
    closes = spy_avail['Close']

    # Short-term realized vol (10d)
    short_w = cfg.KELLY_MHVRP_SHORT_RV_WINDOW
    c_short = closes.iloc[-(short_w + 1):]
    ret_short = np.log(c_short / c_short.shift(1)).dropna()
    rv_short = float(ret_short.std() * np.sqrt(252))

    # Long-term realized vol (60d)
    c_long = closes.iloc[-(long_w + 1):]
    ret_long = np.log(c_long / c_long.shift(1)).dropna()
    rv_long = float(ret_long.std() * np.sqrt(252))

    vrp_short = current_vix - rv_short
    vrp_long = current_vix - rv_long

    if vrp_short < 0 and vrp_long > 0:
        boost = cfg.KELLY_MHVRP_BACKWARDATION_BOOST
    elif vrp_short > 0 and vrp_long < 0:
        boost = cfg.KELLY_MHVRP_CONTANGO_DRAG
    else:
        boost = 1.0

    alloc = vt_alloc * boost
    alloc = max(cfg.KELLY_MHVRP_FLOOR, min(cfg.KELLY_CAP, alloc))
    return round(alloc, 4)




# ============================================================================
# OUTPUT
# ============================================================================

def save_health_report(health_score, allocation_pct, component_scores,
                        regime=None, as_of_date=None):
    """
    Save market health report to signals/market_health.json.
    """
    now = as_of_date or datetime.now()
    horizon_end = now + timedelta(days=cfg.ECON_EVENT_HORIZON_DAYS)

    report = {
        'timestamp': now.isoformat(),
        'health_score': round(health_score, 4),
        'allocation_pct': round(allocation_pct, 4),
        'regime': regime or 'UNKNOWN',
        'components': {},
        'event_horizon': f"{now.strftime('%Y-%m-%d')} to {horizon_end.strftime('%Y-%m-%d')}",
    }

    for component, (score, details) in component_scores.items():
        weight = cfg.COMPONENT_WEIGHTS.get(component, 0.0)
        report['components'][component] = {
            'score': round(score, 4),
            'weight': weight,
            'weighted_contribution': round(score * weight, 4),
            'details': _sanitize_for_json(details),
        }

    # Ensure output directory exists
    os.makedirs(cfg.HEALTH_OUTPUT_DIR, exist_ok=True)

    output_path = os.path.join(cfg.HEALTH_OUTPUT_DIR, cfg.HEALTH_OUTPUT_FILE)
    with open(output_path, 'w') as f:
        json.dump(report, f, indent=2, default=str)

    # Also save timestamped backup
    backup_name = f"market_health_{now.strftime('%Y%m%d_%H%M%S')}.json"
    backup_path = os.path.join(cfg.HEALTH_OUTPUT_DIR, backup_name)
    with open(backup_path, 'w') as f:
        json.dump(report, f, indent=2, default=str)

    logger.info(f"Health report saved to {output_path}")
    logger.info(f"Backup saved to {backup_path}")

    return report


def _sanitize_for_json(obj):
    """Replace NaN/inf with None for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, (np.floating, np.integer)):
        val = float(obj)
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    return obj


# ============================================================================
# HEALTH HISTORY TRACKING (for Kelly f* accumulation)
# ============================================================================

HEALTH_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    'signals', 'health_history.json')


def _load_health_history():
    """
    Load historical health scores and SPY weekly returns from history file.
    Returns (scores_list, returns_list) for Kelly f* computation.
    """
    try:
        if not os.path.exists(HEALTH_HISTORY_FILE):
            return [], []
        with open(HEALTH_HISTORY_FILE, 'r') as f:
            history = json.load(f)
        scores = [entry['health_score'] for entry in history if 'health_score' in entry]
        returns = [entry['spy_weekly_return'] for entry in history
                   if 'spy_weekly_return' in entry and entry['spy_weekly_return'] is not None]
        return scores, returns
    except Exception as e:
        logger.warning(f"Could not load health history: {e}")
        return [], []


def _append_health_history(ref_date, health_score, spy_daily):
    """
    Append this week's health_score and SPY weekly return to history file.
    SPY weekly return = (close today - close 5 trading days ago) / close 5 trading days ago.
    """
    spy_weekly_return = None
    if spy_daily is not None and len(spy_daily) >= 6:
        try:
            recent = spy_daily['Close'].iloc[-6:]
            spy_weekly_return = float((recent.iloc[-1] / recent.iloc[0]) - 1)
        except Exception as e:
            logger.warning(f"Could not compute SPY weekly return: {e}")

    entry = {
        'date': ref_date.strftime('%Y-%m-%d'),
        'health_score': round(health_score, 4),
        'spy_weekly_return': round(spy_weekly_return, 6) if spy_weekly_return is not None else None,
    }

    try:
        history = []
        if os.path.exists(HEALTH_HISTORY_FILE):
            with open(HEALTH_HISTORY_FILE, 'r') as f:
                history = json.load(f)

        # Avoid duplicate entries for same date
        existing_dates = {e['date'] for e in history}
        if entry['date'] not in existing_dates:
            history.append(entry)

            os.makedirs(os.path.dirname(HEALTH_HISTORY_FILE), exist_ok=True)
            with open(HEALTH_HISTORY_FILE, 'w') as f:
                json.dump(history, f, indent=2)
            logger.info(f"Health history: appended week {entry['date']} ({len(history)} total entries)")
        else:
            logger.info(f"Health history: {entry['date']} already exists, skipping")
    except Exception as e:
        logger.warning(f"Could not save health history: {e}")


# ============================================================================
# MAIN PIPELINE
# ============================================================================

def run_pipeline(dry_run=False, single_component=None, as_of_date=None, legacy=False):
    """
    Execute the full market health pipeline.

    Args:
        dry_run: If True, print results but don't write file.
        single_component: If set, only run that component.
            New: 'vol', 'credit', 'breadth', 'meanrev'
            Legacy: 'internals', 'geo', 'news', 'econ'
        as_of_date: datetime — treat this as "today" for all data downloads.
            Defaults to None (uses datetime.now()). Set for backtesting.
        legacy: If True, use the old 4-component pipeline.

    Returns:
        dict: The full health report, or None on failure.
    """
    ref_date = as_of_date or datetime.now()

    mode_label = "LEGACY" if legacy else "NEW"
    logger.info("=" * 60)
    if as_of_date:
        logger.info(f"MARKET HEALTH PIPELINE [{mode_label}] — Backtest as of {ref_date.strftime('%Y-%m-%d')}")
    else:
        logger.info(f"MARKET HEALTH PIPELINE [{mode_label}] — Starting")
    logger.info("=" * 60)

    start_time = time.time()
    component_scores = {}

    if legacy:
        component_map = {
            'internals': ('market_internals', score_market_internals),
            'geo': ('geopolitical_risk', score_geopolitical_risk),
            'news': ('news_sentiment', score_news_sentiment),
            'econ': ('economic_calendar', score_economic_calendar),
        }
        weights = cfg.LEGACY_COMPONENT_WEIGHTS
    else:
        component_map = {
            'vol': ('vol_structure', score_vol_structure),
            'credit': ('credit_macro', score_credit_macro),
            'breadth': ('breadth_internals', score_breadth_internals),
            'meanrev': ('mean_reversion', score_mean_reversion),
        }
        weights = cfg.COMPONENT_WEIGHTS

    if single_component:
        if single_component not in component_map:
            logger.error(f"Unknown component: {single_component}. Choose from: {list(component_map.keys())}")
            return None
        key, func = component_map[single_component]
        score, details = func(as_of_date=ref_date)
        component_scores[key] = (score, details)
        # Fill others with neutral
        for k, (name, _) in component_map.items():
            if name not in component_scores:
                component_scores[name] = (0.0, {'source': 'skipped'})
    else:
        # Run all components
        for short_name, (full_name, func) in component_map.items():
            try:
                score, details = func(as_of_date=ref_date)
                component_scores[full_name] = (score, details)
            except Exception as e:
                logger.error(f"Component {full_name} failed: {e}")
                component_scores[full_name] = (0.0, {'error': str(e)})

    # Compute composite
    health_score = compute_health_score(component_scores, weights=weights)

    # --- Kelly MHVRP allocation ---
    # Fetch data needed for MHVRP: SPY daily, VIX series, VIX futures, history
    historical_scores, historical_returns = _load_health_history()

    try:
        spy_start = ref_date - timedelta(days=365)
        spy_daily = download_ticker('SPY', spy_start.strftime('%Y-%m-%d'),
                                     ref_date.strftime('%Y-%m-%d'))
    except Exception as e:
        logger.warning(f"SPY download for MHVRP failed: {e}")
        spy_daily = None

    try:
        vix_series = fetch_fred_series(cfg.FRED_VIX_SERIES,
                                        (ref_date - timedelta(days=365)).strftime('%Y-%m-%d'),
                                        ref_date.strftime('%Y-%m-%d'))
    except Exception as e:
        logger.warning(f"VIX series fetch for MHVRP failed: {e}")
        vix_series = None

    try:
        vix_futures = fetch_vix_futures(ref_date - timedelta(days=365), ref_date)
    except Exception as e:
        logger.warning(f"VIX futures fetch for MHVRP failed: {e}")
        vix_futures = None

    try:
        allocation_pct = health_to_allocation_kelly_voltarget_mhvrp(
            health_score, historical_scores, historical_returns,
            spy_daily, vix_series, ref_date, vix_futures=vix_futures)
    except Exception as e:
        logger.error(f"Kelly MHVRP allocation failed: {e}, using default {cfg.DEFAULT_ALLOCATION_PCT}")
        allocation_pct = cfg.DEFAULT_ALLOCATION_PCT

    # Determine regime label for logging
    if allocation_pct >= 0.95:
        regime = 'FULL'
    elif allocation_pct >= 0.80:
        regime = 'NORMAL'
    elif allocation_pct >= 0.65:
        regime = 'CAUTIOUS'
    else:
        regime = 'DEFENSIVE'

    elapsed = time.time() - start_time

    # Print summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("MARKET HEALTH RESULTS")
    logger.info("=" * 60)

    for comp_name, (score, details) in component_scores.items():
        weight = weights.get(comp_name, 0)
        weighted = score * weight
        source = details.get('source', 'live')
        logger.info(f"  {comp_name:25s}  score={score:+.4f}  weight={weight:.0%}  contribution={weighted:+.4f}  [{source}]")

    logger.info("-" * 60)
    logger.info(f"  {'COMPOSITE HEALTH':25s}  {health_score:+.4f}")
    logger.info(f"  {'KELLY MHVRP ALLOC':25s}  {allocation_pct:.1%}  [{regime}]")
    logger.info(f"  {'VIX futures':25s}  {'available' if vix_futures is not None else 'unavailable (fallback)'}")
    logger.info(f"  {'Kelly history':25s}  {len(historical_scores)} weeks {'(f* active)' if len(historical_scores) >= cfg.KELLY_MIN_OBS else '(accumulating)'}")
    logger.info(f"  {'Pipeline time':25s}  {elapsed:.1f}s")
    logger.info("=" * 60)

    # Append this week's data to history for future Kelly f* computation
    _append_health_history(ref_date, health_score, spy_daily)

    if dry_run:
        logger.info("DRY RUN — no file written")
        report = {
            'health_score': round(health_score, 4),
            'allocation_pct': round(allocation_pct, 4),
            'regime': regime,
            'components': {
                name: {'score': round(s, 4), 'weight': weights.get(name, 0)}
                for name, (s, _) in component_scores.items()
            },
        }
        return report

    # Save output
    report = save_health_report(health_score, allocation_pct, component_scores,
                                 regime=regime, as_of_date=ref_date)
    return report


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Market Health Pipeline — Assess upcoming week market conditions'
    )
    parser.add_argument(
        '--component', '-c',
        choices=['vol', 'credit', 'breadth', 'meanrev', 'internals', 'geo', 'news', 'econ'],
        help='Run a single component only'
    )
    parser.add_argument(
        '--dry-run', '-d',
        action='store_true',
        help='Print results without writing output file'
    )
    parser.add_argument(
        '--legacy',
        action='store_true',
        help='Use legacy pipeline (internals/geo/news/econ) instead of new components'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable debug logging'
    )
    parser.add_argument(
        '--as-of',
        type=str,
        default=None,
        help='Run as of a specific date (YYYY-MM-DD) for backtesting'
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Auto-detect legacy mode from component name
    legacy = args.legacy
    if args.component in ('internals', 'geo', 'news', 'econ'):
        legacy = True

    # Parse as_of_date if provided
    as_of_date = None
    if args.as_of:
        as_of_date = datetime.strptime(args.as_of, '%Y-%m-%d')

    # Validate API keys
    if not cfg.FRED_API_KEY:
        logger.warning("FRED_API_KEY not set. FRED-based signals will return neutral.")
        logger.warning("  Set it: export FRED_API_KEY=your_key_here")
    if not cfg.MARKETAUX_API_KEY and legacy:
        logger.warning("MARKETAUX_API_KEY not set. News sentiment will use VIX fallback.")

    report = run_pipeline(
        dry_run=args.dry_run,
        single_component=args.component,
        as_of_date=as_of_date,
        legacy=legacy,
    )

    if report:
        print(f"\nAllocation: {report['allocation_pct']:.1%}")
        return 0
    else:
        print("\nPipeline failed.")
        return 1


if __name__ == '__main__':
    sys.exit(main())
