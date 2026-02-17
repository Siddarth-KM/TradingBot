"""
Trading Bot Core Logic
Data downloading, feature engineering, model training, sentiment analysis, and prediction generation
Extracted from main.py - no Flask dependencies
"""

import logging
import os
import pickle
import random
import re
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from threading import Lock

import numpy as np
import pandas as pd
import requests
# Use Massive.com API instead of yfinance
import massive_api as yf
from bs4 import BeautifulSoup
from catboost import CatBoostClassifier, Pool
from scipy import stats
from sklearn.ensemble import (
    RandomForestRegressor, ExtraTreesRegressor, 
    GradientBoostingRegressor, AdaBoostRegressor
)
from sklearn.linear_model import BayesianRidge, ElasticNet
from sklearn.model_selection import cross_val_score, TimeSeriesSplit
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler, MinMaxScaler
from sklearn.svm import SVR
from ta.momentum import RSIIndicator, WilliamsRIndicator, StochasticOscillator
from ta.trend import MACD
from ta.volatility import AverageTrueRange
from ta.volume import OnBalanceVolumeIndicator, ChaikinMoneyFlowIndicator
import xgboost as xgb

from bot_config import *
from bot_utils import *

# Configure warnings
warnings.filterwarnings('ignore')
logging.getLogger().setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("massive").setLevel(logging.ERROR)

# ============================================================================
# GLOBAL STATE
# ============================================================================

# Sentiment cache with lock for thread safety
SENTIMENT_CACHE = {}
SENTIMENT_CACHE_LOCK = Lock()

# Global market sentiment cache (24-hour cache)
MARKET_SENTIMENT_CACHE = None
MARKET_SENTIMENT_TIMESTAMP = None
MARKET_SENTIMENT_LOCK = Lock()

# ============================================================================
# SENTIMENT CACHING
# ============================================================================

def get_cached_sentiment(ticker):
    """Get cached sentiment data if available and not expired"""
    with SENTIMENT_CACHE_LOCK:
        if ticker in SENTIMENT_CACHE:
            timestamp, data = SENTIMENT_CACHE[ticker]
            cache_seconds = SENTIMENT_CONFIG['SENTIMENT_CACHE_HOURS'] * 3600
            if time.time() - timestamp < cache_seconds:
                return data
    return None


def cache_sentiment(ticker, sentiment_data):
    """Store sentiment data in cache with timestamp"""
    with SENTIMENT_CACHE_LOCK:
        SENTIMENT_CACHE[ticker] = (time.time(), sentiment_data)
        
        # Cache cleanup - remove oldest entries if > 100 items
        if len(SENTIMENT_CACHE) > 100:
            sorted_items = sorted(
                SENTIMENT_CACHE.items(), 
                key=lambda x: x[1][0], 
                reverse=True
            )
            SENTIMENT_CACHE.clear()
            for k, v in sorted_items[:80]:
                SENTIMENT_CACHE[k] = v

# ============================================================================
# IMPORT CORE FUNCTIONS FROM MAIN.PY
# (Temporary approach - importing from main.py to avoid duplication)
# ============================================================================

# Import caching functions
from main import (
    get_cached_data,
    save_to_cache,
    clear_stale_cache,
    manage_cache_size,
)

# Import data downloading functions
from main import (
    scrape_index_constituents,
    download_market_data_cache,
    download_index_data,
    download_single_ticker_data,
)

# Import feature engineering functions
from main import (
    add_features_to_stock_original,
    add_features_parallel,
)

# Import model training functions
from main import (
    train_models_parallel,
)

# Import sentiment functions
from main import (
    get_market_sentiment,
    get_sentiment_score,
    analyze_ticker_sentiment,
    apply_sentiment_adjustment,
)

# Import prediction functions
from main import (
    select_models_for_market,
    apply_direction_confidence_parallel,
    filter_positive_predictions,
)

# Note: All these functions are already implemented in main.py
# We're importing them here to avoid code duplication
# The bot will work standalone without Flask
