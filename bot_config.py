"""
Trading Bot Configuration
All constants, settings, and mappings for the standalone trading bot
"""

import os
from datetime import datetime, timedelta

# ============================================================================
# TRADING PARAMETERS
# ============================================================================

# Indexes to analyze
INDEXES_TO_ANALYZE = ['SPY', 'NASDAQ', 'SP400', 'SPSM']

# Prediction settings
PREDICTION_WINDOW = 5  # days ahead to predict
LOOKBACK_MONTHS = 18   # months of historical data
TOP_N_PER_INDEX = 2    # number of stocks to select per index

# ============================================================================
# DATA MANAGEMENT
# ============================================================================

# Cache settings
CACHE_MAX_SIZE = 50
CACHE_MAX_AGE_HOURS = 24
CACHE_DURATION = 24 * 60 * 60  # 24 hours in seconds
CACHE_DIR = 'cache'

# Threading
# Oracle VM has 2 cores and 956MB RAM; higher values cause OOM during model training.
# Keep this at 2 unless deployment target changes.
THREAD_POOL_SIZE = 2

# Data quality
MIN_DATA_POINTS = 50
FORWARD_FILL_LIMIT = 5

# Market symbols for cross-asset features
MARKET_SYMBOLS = ['SPY', 'VIX', 'DXY', 'TLT', 'GLD', 'QQQ']

# ============================================================================
# MODEL PARAMETERS
# ============================================================================

MIN_FEATURES = 5
MIN_CONFIDENCE_SAMPLES = 30
DEFAULT_CONFIDENCE_LEVEL = 95
DEFAULT_MARGIN_ERROR = 0.05
TRADING_DAYS_YEAR = 252
FEATURE_ROLLING_WINDOW = 20

# ============================================================================
# MARKET REGIME THRESHOLDS
# ============================================================================

# VIX thresholds
VIX_HIGH_THRESHOLD = 25
VIX_VERY_HIGH_THRESHOLD = 30
VIX_LOW_THRESHOLD = 15

# Market condition thresholds
SIDEWAYS_PRICE_THRESHOLD = 0.05
SIDEWAYS_SMA_THRESHOLD = 0.03
BULL_PRICE_THRESHOLD = 0.1
BULL_SMA_THRESHOLD = 0.05
BEAR_PRICE_THRESHOLD = -0.1
BEAR_SMA_THRESHOLD = -0.05
HIGH_VOLATILITY_THRESHOLD = 0.03

# Regime-based volatility
REGIME_VOLATILITY_BULL = 0.015
REGIME_VOLATILITY_BEAR = 0.025
REGIME_VOLATILITY_SIDEWAYS = 0.02
REGIME_VOLATILITY_VOLATILE = 0.035
VOLATILITY_ADJUSTMENT_BASE = 0.5

# ============================================================================
# SENTIMENT ANALYSIS
# ============================================================================

SENTIMENT_CONFIG = {
    'MARKET_NEWS_WEIGHT': 0.3,
    'SENTIMENT_CACHE_HOURS': 1,
    'MARKET_SENTIMENT_CACHE_HOURS': 24,
    'SENTIMENT_ADJUSTMENT_MAX': 0.25,
}

# ============================================================================
# INDEX CONFIGURATIONS
# ============================================================================

# Wikipedia URLs for scraping index constituents
INDEX_URLS = {
    'SPY': 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
    'NASDAQ': 'https://en.wikipedia.org/wiki/Nasdaq-100',
    'SP400': 'https://en.wikipedia.org/wiki/List_of_S%26P_400_companies',
    'SPSM': 'https://en.wikipedia.org/wiki/List_of_S%26P_600_companies'
}

# ETF tickers for index proxies
INDEX_ETF_TICKERS = {
    'NASDAQ': 'QQQ',
    'SPY': 'SPY',
    'SP400': 'MDY',
    'SPSM': 'SPSM',
}

# ============================================================================
# DATA FRESHNESS GUARD
# ============================================================================

# US equity market close in UTC. NYSE closes 16:00 ET; that is 20:00 UTC under
# EDT (Mar-Nov) and 21:00 UTC under EST (Nov-Mar). We use 20:00 (the earlier,
# more conservative value) only to decide whether *today's* session is complete
# — the live run fires ~5h after close so this boundary never bites in practice.
MARKET_CLOSE_UTC_HOUR = 20

# NYSE full-day closures, 2026-2027 (observed dates — the day the market is
# actually shut). Used by expected_last_trading_day() so the freshness guard
# does not false-alarm when the prior session was a holiday. Update annually.
US_MARKET_HOLIDAYS = {
    # 2026
    '2026-01-01',  # New Year's Day
    '2026-01-19',  # MLK Jr. Day
    '2026-02-16',  # Washington's Birthday
    '2026-04-03',  # Good Friday
    '2026-05-25',  # Memorial Day
    '2026-06-19',  # Juneteenth
    '2026-07-03',  # Independence Day (observed, Jul 4 = Sat)
    '2026-09-07',  # Labor Day
    '2026-11-26',  # Thanksgiving
    '2026-12-25',  # Christmas
    # 2027
    '2027-01-01',  # New Year's Day
    '2027-01-18',  # MLK Jr. Day
    '2027-02-15',  # Washington's Birthday
    '2027-03-26',  # Good Friday
    '2027-05-31',  # Memorial Day
    '2027-06-18',  # Juneteenth (observed, Jun 19 = Sat)
    '2027-07-05',  # Independence Day (observed, Jul 4 = Sun)
    '2027-09-06',  # Labor Day
    '2027-11-25',  # Thanksgiving
    '2027-12-24',  # Christmas (observed, Dec 25 = Sat)
}

# ============================================================================
# OUTPUT SETTINGS
# ============================================================================

OUTPUT_DIR = 'signals'
OUTPUT_FORMATS = ['json', 'csv']

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_default_start_date():
    """Calculate default start date (18 months ago)"""
    return (datetime.now() - timedelta(days=LOOKBACK_MONTHS * 30)).strftime('%Y-%m-%d')

def is_index_ticker(ticker):
    """Check if a ticker is one of our supported indexes"""
    return ticker.upper() in INDEXES_TO_ANALYZE
