"""
Market Health Pipeline Configuration
Weights, thresholds, API settings, and allocation parameters for market health scoring.
"""

import os

# ============================================================================
# LOAD .env FILE (no external dependency needed)
# ============================================================================

def _load_dotenv(filepath='.env'):
    """Load key=value pairs from .env file into os.environ."""
    try:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filepath)
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if value and key not in os.environ:  # don't override existing env vars
                    os.environ[key] = value
    except FileNotFoundError:
        pass

_load_dotenv()

# ============================================================================
# API KEYS (load from environment variables — never hardcode)
# ============================================================================

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
MARKETAUX_API_KEY = os.environ.get("MARKETAUX_API_KEY", "")
NASDAQ_DATA_LINK_API_KEY = os.environ.get("NASDAQ_DATA_LINK_API_KEY", "")

# ============================================================================
# COMPONENT WEIGHTS (must sum to 1.0)
# ============================================================================

COMPONENT_WEIGHTS = {
    'vol_structure': 0.25,
    'credit_macro': 0.25,
    'breadth_internals': 0.25,
    'mean_reversion': 0.25,
}

# Legacy weights (used when running old pipeline via --legacy flag)
LEGACY_COMPONENT_WEIGHTS = {
    'market_internals': 0.35,
    'geopolitical_risk': 0.25,
    'news_sentiment': 0.20,
    'economic_calendar': 0.20,
}

# ============================================================================
# KELLY CRITERION PARAMETERS
# ============================================================================

KELLY_MIN_OBS = 26            # minimum weeks before Kelly produces estimates
KELLY_LOOKBACK = 52           # rolling window size (weeks)
KELLY_FLOOR = 0.50            # hard floor for all Kelly strategies
KELLY_CAP = 0.9999            # hard cap — never quite 100% (Kelly principle)
KELLY_THEORETICAL_MAX = 0.25  # upper bound of f* for weekly SPY (Scaled strategy)
# ============================================================================
# KELLY VOLTARGET PARAMETERS
# ============================================================================

# --- Base Volatility-Targeted Kelly ---
KELLY_VOL_TARGET = 0.15             # 15% annualized target vol
KELLY_VOL_REALIZED_WINDOW = 20      # trading days for realized vol
KELLY_VOL_FLOOR = 0.50

# --- Multi-Horizon VRP Crossover ---
KELLY_MHVRP_SHORT_RV_WINDOW = 10    # short-term realized vol window (fallback)
KELLY_MHVRP_LONG_RV_WINDOW = 60     # long-term realized vol window (fallback)
KELLY_MHVRP_BACKWARDATION_BOOST = 1.25  # temp spike, expected to revert
KELLY_MHVRP_CONTANGO_DRAG = 0.85        # calm short-term, long-term stressed
KELLY_MHVRP_FLOOR = 0.50

# VIX Futures Term Structure Thresholds (used when vix_utils data available)
KELLY_MHVRP_BACKWARDATION_SPREAD = -0.05  # (VX2-VX1)/VX1 below this = backwardation
KELLY_MHVRP_CONTANGO_SPREAD = 0.10        # (VX2-VX1)/VX1 above this = steep contango

# ============================================================================
# VOL STRUCTURE CONFIG (new component)
# ============================================================================

# FRED series for VIX level (daily close)
FRED_VIX_SERIES = 'VIXCLS'
VRP_REALIZED_VOL_WINDOW = 20  # trading days for realized vol calculation
VRP_ZSCORE_WINDOW = 60        # days for VRP z-score normalization
VIX_ZSCORE_WINDOW = 60        # days for VIX level z-score

# ============================================================================
# CREDIT / MACRO CONFIG (new component)
# ============================================================================

# FRED series for credit and yield curve signals
FRED_HY_OAS_SERIES = 'BAMLH0A0HYM2'   # ICE BofA US High Yield OAS
FRED_YIELD_3M10Y_SERIES = 'T10Y3M'     # 10-Year minus 3-Month Treasury spread
FRED_YIELD_2S10S_SERIES = 'T10Y2Y'     # 10-Year minus 2-Year Treasury spread

CREDIT_OAS_ZSCORE_WINDOW = 60          # days for OAS z-score
YIELD_CURVE_ZSCORE_WINDOW = 60         # days for yield spread z-score
YIELD_CURVE_MOMENTUM_DAYS = 5          # days for yield curve change signal

# ============================================================================
# BREADTH / INTERNALS CONFIG (improved: 200-day SMA)
# ============================================================================

# Tickers used for breadth / internals
BREADTH_INDEX = 'SPY'         # SPY constituents used for breadth calculation
BREADTH_SMA_WINDOW = 200      # SMA window — 200-day for secular trend (was 50)
BREADTH_LOOKBACK_DAYS = 260   # days of history needed (enough for 200-day SMA)

# ============================================================================
# MEAN REVERSION CONFIG (new component)
# ============================================================================

MEAN_REV_SMA_WINDOW = 100     # 20-week ≈ 100 trading days
MEAN_REV_RSI_WINDOW = 14      # standard RSI period (weekly-equivalent on daily)
MEAN_REV_BB_WINDOW = 20       # Bollinger Band SMA window (days)
MEAN_REV_BB_STD = 2.0         # Bollinger Band standard deviations
MEAN_REV_LOOKBACK_DAYS = 260  # history needed for SMA + RSI

# ============================================================================
# LEGACY MARKET INTERNALS CONFIG
# ============================================================================

# Put/Call ratio source
PUTCALL_TICKER = '^PCALL'     # CBOE equity put/call ratio (fallback: estimate)

# McClellan Oscillator EMAs
MCCLELLAN_FAST = 19
MCCLELLAN_SLOW = 39

# ============================================================================
# GEOPOLITICAL / GLOBAL RISK CONFIG
# ============================================================================

# Tickers for risk proxy calculation
GEO_TICKERS = ['VIX', 'GLD', 'DXY', 'TLT', 'HYG', 'LQD', 'USO']

# VIX thresholds
VIX_CALM = 15.0
VIX_ELEVATED = 20.0
VIX_HIGH = 25.0
VIX_EXTREME = 35.0

# Lookback windows
GEO_MOMENTUM_WINDOW = 5       # days for momentum/trend signals
GEO_AVG_WINDOW = 20           # days for VIX moving average

# Credit spread: HYG/LQD ratio thresholds
CREDIT_SPREAD_NORMAL = 0.72   # typical HYG/LQD ratio
CREDIT_SPREAD_STRESS = 0.68   # widening = risk-off

# ============================================================================
# NEWS SENTIMENT CONFIG (MarketAux)
# ============================================================================

MARKETAUX_BASE_URL = "https://api.marketaux.com/v1/news/all"
NEWS_LOOKBACK_DAYS = 3        # days of headlines to analyze
NEWS_MAX_ARTICLES = 50        # max articles per request
NEWS_CRISIS_KEYWORDS = [
    'crash', 'recession', 'crisis', 'collapse', 'default', 'bankruptcy',
    'war', 'invasion', 'sanctions', 'tariff', 'shutdown', 'downgrade',
    'contagion', 'panic', 'plunge', 'sell-off', 'bear market',
]
NEWS_POSITIVE_KEYWORDS = [
    'rally', 'surge', 'record high', 'bull market', 'recovery',
    'growth', 'expansion', 'upgrade', 'stimulus', 'rate cut',
]

# ============================================================================
# ECONOMIC CALENDAR CONFIG (FRED)
# ============================================================================

FRED_BASE_URL = "https://api.stlouisfed.org/fred"
ECON_EVENT_HORIZON_DAYS = 7   # rolling 7 calendar days

# High-impact FRED series IDs and their names
HIGH_IMPACT_SERIES = {
    'UNRATE': 'Unemployment Rate',
    'CPIAUCSL': 'CPI (All Urban Consumers)',
    'PPIFIS': 'PPI (Final Demand)',
    'PAYEMS': 'Nonfarm Payrolls',
    'GDP': 'Gross Domestic Product',
    'FEDFUNDS': 'Federal Funds Rate',
    'RETAILSALES': 'Retail Sales',       # proxy series
    'ICSA': 'Initial Jobless Claims',
    'DGORDER': 'Durable Goods Orders',
    'HOUST': 'Housing Starts',
}

# Impact classification for scoring
EVENT_IMPACT = {
    'FEDFUNDS': 3,     # FOMC decisions — highest impact
    'CPIAUCSL': 3,     # CPI — market-moving
    'PAYEMS': 3,        # NFP — market-moving
    'GDP': 3,           # GDP — market-moving
    'PPIFIS': 2,        # PPI — moderate
    'UNRATE': 2,        # Unemployment — moderate
    'ICSA': 1,          # Weekly claims — lower
    'RETAILSALES': 2,   # Retail — moderate
    'DGORDER': 1,       # Durable goods — lower
    'HOUST': 1,         # Housing — lower
}

# ============================================================================
# OUTPUT CONFIG
# ============================================================================

HEALTH_OUTPUT_DIR = 'signals'
HEALTH_OUTPUT_FILE = 'market_health.json'
DEFAULT_ALLOCATION_PCT = 0.80  # fallback if pipeline fails
MAX_HEALTH_AGE_HOURS = 24      # staleness threshold
