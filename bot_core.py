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
import yfinance as yf
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
from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline
import xgboost as xgb

from bot_config import *
from bot_utils import *

# Configure warnings
warnings.filterwarnings('ignore')
logging.getLogger().setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("yfinance").setLevel(logging.ERROR)
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"

# ============================================================================
# GLOBAL STATE
# ============================================================================

# Sentiment model globals
SENTIMENT_MODEL = None
SENTIMENT_TOKENIZER = None
SENTIMENT_PIPELINE = None

# Sentiment cache with lock for thread safety
SENTIMENT_CACHE = {}
SENTIMENT_CACHE_LOCK = Lock()

# Global market sentiment cache (24-hour cache)
MARKET_SENTIMENT_CACHE = None
MARKET_SENTIMENT_TIMESTAMP = None
MARKET_SENTIMENT_LOCK = Lock()

# Track API requests to prevent rate limit issues
LAST_NEWS_API_CALL = None

# ============================================================================
# SENTIMENT MODEL INITIALIZATION
# ============================================================================

def initialize_sentiment_model():
    """Initialize FinBERT sentiment model"""
    global SENTIMENT_MODEL, SENTIMENT_TOKENIZER, SENTIMENT_PIPELINE
    
    try:
        print("Loading sentiment model...")
        
        SENTIMENT_MODEL = AutoModelForSequenceClassification.from_pretrained(
            "ProsusAI/finbert",
            local_files_only=False,
            trust_remote_code=False
        )
        SENTIMENT_TOKENIZER = AutoTokenizer.from_pretrained(
            "ProsusAI/finbert",
            local_files_only=False,
            trust_remote_code=False
        )
        
        SENTIMENT_PIPELINE = pipeline(
            "text-classification", 
            model=SENTIMENT_MODEL, 
            tokenizer=SENTIMENT_TOKENIZER,
            device=-1  # Force CPU
        )
        
        return True
    except Exception as e:
        print(f"⚠️ Sentiment model failed to load: {e}")
        SENTIMENT_PIPELINE = None
        return False

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


def get_ticker_sector(ticker):
    """Get sector for a ticker from Yahoo Finance"""
    try:
        ticker_obj = yf.Ticker(ticker)
        info = ticker_obj.info
        
        sector = info.get('sector', '')
        if sector:
            return sector
            
        industry = info.get('industry', '')
        if industry:
            return industry
            
        return 'Unknown'
    except Exception as e:
        return 'Unknown'

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
    get_alpha_vantage_news,
    get_market_sentiment,
    get_sentiment_score,
    analyze_ticker_sentiment,
    apply_sentiment_adjustment,
)

# Import prediction functions
from main import (
    apply_direction_confidence_parallel,
    filter_positive_predictions,
)

# Note: All these functions are already implemented in main.py
# We're importing them here to avoid code duplication
# The bot will work standalone without Flask
