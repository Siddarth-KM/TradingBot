"""
Trading Bot Utility Functions
Helper functions for data processing, market regime detection, and model selection
"""

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator, WilliamsRIndicator, StochasticOscillator
from ta.trend import MACD
from ta.volatility import AverageTrueRange
from ta.volume import OnBalanceVolumeIndicator, ChaikinMoneyFlowIndicator

from bot_config import MIN_DATA_POINTS


def ensure_iterable(obj):
    """Ensure the object is iterable. If not, wrap it in a list."""
    if isinstance(obj, (list, np.ndarray, pd.Series)):
        return obj
    return [obj]


def to_scalar(x):
    """
    Convert any array-like or nested value to a scalar.
    Handles multiple data types safely including nested arrays, lists, and Series objects.
    """
    try:
        # If it's None or already scalar
        if x is None or np.isscalar(x):
            return x
            
        # If it's a pandas Series
        if isinstance(x, pd.Series):
            if len(x) > 0:
                return to_scalar(x.iloc[0])
            return np.nan
            
        # If it's a numpy array
        if isinstance(x, np.ndarray):
            x = x.flatten()  # Flatten any multi-dimensional array
            if x.size > 0:
                return float(x[0])
            return np.nan
            
        # If it's a list or tuple
        if isinstance(x, (list, tuple)):
            if len(x) > 0:
                return to_scalar(x[0])
            return np.nan
            
        # Try direct float conversion as last resort
        return float(x)
    except Exception:
        return np.nan


def flatten_series(s):
    """
    Flatten any Series or DataFrame to a 1D Series.
    Enhanced to handle 2D arrays from YFinance data.
    """
    # If DataFrame, take first column
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
        
    # If numpy array, flatten to 1D
    if isinstance(s, np.ndarray):
        # Special handling for 2D arrays
        if s.ndim > 1:
            # Take first column for 2D arrays
            s = s[:, 0] if s.shape[1] > 0 else s.flatten()
        s = s.flatten()
        return pd.Series(s)
        
    # If Series, ensure values are scalar
    if isinstance(s, pd.Series):
        # Check if any values are arrays/lists and flatten them
        if s.dtype == 'object':
            return s.apply(lambda x: to_scalar(x) if hasattr(x, '__len__') else x)
        return s
        
    # Fallback: convert to 1D array then Series
    try:
        arr = np.asarray(s).flatten()
        return pd.Series(arr)
    except:
        # Ultimate fallback: return empty series
        return pd.Series()


def calc_slope(x):
    """Calculate slope of a series"""
    # Ensure x is iterable and has length
    if not hasattr(x, '__len__') or isinstance(x, (str, np.number)):
        x = [x]
    x = ensure_iterable(x)
    
    if len(x) < 2:
        return np.nan
    return np.polyfit(range(len(x)), x, 1)[0]


def sanitize_for_json(obj):
    """Recursively replace NaN and inf values with None for JSON serialization."""
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    elif isinstance(obj, (float, np.floating)):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(obj)
    elif isinstance(obj, (int, np.integer)):
        return int(obj)
    else:
        return obj


def select_models_for_market(market_condition, is_custom=False):
    """Select appropriate models based on market condition"""
    if is_custom:
        # For custom tickers, use a balanced selection
        return [2, 7, 6]  # Random Forest, SVR, Bayesian Ridge
    
    model_selections = {
        'bull': [1, 4, 8],      # XGBoost, Extra Trees, Gradient Boosting
        'bear': [6, 9, 2],      # Bayesian Ridge, Elastic Net, Random Forest
        'sideways': [2, 7, 6],  # Random Forest, SVR, Bayesian Ridge
        'volatile': [4, 5, 8]   # Extra Trees, AdaBoost, Gradient Boosting
    }
    
    return model_selections.get(market_condition, [2, 7, 6])


def detect_market_regime(etf_df):
    """Robust regime detection using multiple indicators from the index ETF ticker's data."""
    if etf_df is None or len(etf_df) < MIN_DATA_POINTS:
        return 'sideways', 0.5
        
    # Create a copy to avoid modifying the original dataframe
    df = etf_df.copy()
    
    # STEP 1: Pre-process all OHLCV columns to ensure they're 1D arrays
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if col in df.columns:
            # Check if column contains arrays or has unexpected structure
            try:
                # First check: If any element is an array/list type
                if df[col].dtype == 'object':
                    df[col] = df[col].apply(lambda x: to_scalar(x) if hasattr(x, '__len__') else x)
                
                # Second check: For multi-dimensional numpy arrays
                sample = df[col].iloc[0] if len(df) > 0 else None
                if hasattr(sample, 'shape') and hasattr(sample, '__len__') and len(sample) > 0:
                    df[col] = df[col].apply(lambda x: to_scalar(x))
                
                # Ensure all values are proper numeric type
                df[col] = pd.to_numeric(df[col], errors='coerce')
            except Exception as e:
                print(f"Error flattening column {col}: {e}")
                # Provide default values if conversion fails
                df[col] = np.nan
    
    # STEP 2: Now proceed with indicator calculations using flattened data
    try:
        # Calculate indicators if not present
        if 'SMA_20' not in df:
            df['SMA_20'] = flatten_series(df['Close'].rolling(window=20).mean())
        if 'SMA_50' not in df:
            df['SMA_50'] = flatten_series(df['Close'].rolling(window=50).mean())
        
        # MACD calculation with proper error handling
        try:
            if 'macd' not in df or 'macd_signal' not in df:
                macd = MACD(close=df['Close'])
                macd_val_raw = macd.macd()
                macd_signal_raw = macd.macd_signal()
                df['macd'] = flatten_series(macd_val_raw)
                df['macd_signal'] = flatten_series(macd_signal_raw)
                df['ema_diff'] = flatten_series(df['macd'] - df['macd_signal'])
        except Exception as e:
            print(f"Error calculating MACD: {e}")
            df['macd'] = np.nan
            df['macd_signal'] = np.nan
            df['ema_diff'] = np.nan
            
        # Continue with other indicators with similar error handling
        try:
            if 'stoch_k' not in df:
                stoch = StochasticOscillator(high=df['High'], low=df['Low'], close=df['Close'])
                df['stoch_k'] = flatten_series(stoch.stoch())
        except Exception as e:
            print(f"Error calculating Stochastic: {e}")
            df['stoch_k'] = np.nan
            
        try:
            if 'Donchian_Width' not in df:
                df['Donchian_Width'] = flatten_series(df['High'].rolling(window=20).max() - df['Low'].rolling(window=20).min())
        except Exception as e:
            print(f"Error calculating Donchian Width: {e}")
            df['Donchian_Width'] = np.nan
            
        try:
            if 'RSI' not in df:
                df['RSI'] = flatten_series(RSIIndicator(close=df['Close']).rsi())
        except Exception as e:
            print(f"Error calculating RSI: {e}")
            df['RSI'] = np.nan
            
        try:
            if 'williams_r' not in df:
                df['williams_r'] = flatten_series(WilliamsRIndicator(high=df['High'], low=df['Low'], close=df['Close'], lbp=14).williams_r())
        except Exception as e:
            print(f"Error calculating Williams %R: {e}")
            df['williams_r'] = np.nan
            
        try:
            if 'ATR' not in df:
                df['ATR'] = flatten_series(AverageTrueRange(high=df['High'], low=df['Low'], close=df['Close']).average_true_range())
        except Exception as e:
            print(f"Error calculating ATR: {e}")
            df['ATR'] = np.nan
            
        try:
            if 'rolling_20d_std' not in df:
                df['rolling_20d_std'] = flatten_series(df['Close'].rolling(window=20).std())
        except Exception as e:
            print(f"Error calculating rolling std: {e}")
            df['rolling_20d_std'] = np.nan
            
        try:
            if 'percent_b' not in df:
                upper = flatten_series(df['SMA_20'] + 2*df['Close'].rolling(window=20).std())
                lower = flatten_series(df['SMA_20'] - 2*df['Close'].rolling(window=20).std())
                df['percent_b'] = flatten_series((df['Close'] - lower) / (upper - lower))
        except Exception as e:
            print(f"Error calculating Percent B: {e}")
            df['percent_b'] = np.nan
            
        try:
            if 'OBV' not in df:
                df['OBV'] = flatten_series(OnBalanceVolumeIndicator(close=df['Close'], volume=df['Volume']).on_balance_volume())
        except Exception as e:
            print(f"Error calculating OBV: {e}")
            df['OBV'] = np.nan
    
    except Exception as e:
        print(f"Error in market regime detection: {e}")
        return 'sideways', 0.5  # Default fallback
        
    # Continue with remaining indicators with error handling
    try:
        if 'cmf' not in df:
            df['cmf'] = flatten_series(ChaikinMoneyFlowIndicator(high=df['High'], low=df['Low'], close=df['Close'], volume=df['Volume']).chaikin_money_flow())
    except Exception as e:
        print(f"Error calculating CMF: {e}")
        df['cmf'] = np.nan
        
    recent = df.tail(30)
    
    # --- Trend ---
    sma_20 = recent['SMA_20'].iloc[-1]
    sma_50 = recent['SMA_50'].iloc[-1]
    price = recent['Close'].iloc[-1]
    macd_val = recent['macd'].iloc[-1]
    macd_signal = recent['macd_signal'].iloc[-1]
    ema_diff = recent['ema_diff'].iloc[-1]
    donchian_width = recent['Donchian_Width'].iloc[-1]
    
    # --- Momentum ---
    rsi = recent['RSI'].iloc[-1]
    willr = recent['williams_r'].iloc[-1]
    stoch_k = recent['stoch_k'].iloc[-1]
    
    # --- Volatility ---
    atr = recent['ATR'].iloc[-1]
    rolling_std = recent['rolling_20d_std'].iloc[-1]
    percent_b = recent['percent_b'].iloc[-1]
    
    # --- Volume ---
    obv = recent['OBV'].iloc[-1]
    cmf = recent['cmf'].iloc[-1]
    
    # --- Regime logic ---
    # Trend regime
    bull = (price > sma_20 > sma_50) and (macd_val > macd_signal) and (ema_diff > 0) and (donchian_width > 0.02 * price)
    bear = (price < sma_20 < sma_50) and (macd_val < macd_signal) and (ema_diff < 0) and (donchian_width > 0.02 * price)
    
    # Momentum regime
    overbought = (rsi > 70) or (willr > -20) or (stoch_k > 80)
    oversold = (rsi < 30) or (willr < -80) or (stoch_k < 20)
    
    # Volatility regime
    high_vol = (atr > 0.03 * price) or (rolling_std > 0.03 * price) or (percent_b > 0.95 or percent_b < 0.05)
    
    # Volume regime
    strong_volume = (cmf > 0.1) or (obv > 0)
    weak_volume = (cmf < -0.1) or (obv < 0)
    
    # Decision
    if bull and not high_vol and strong_volume:
        return 'bull', 0.8
    elif bear and not high_vol and weak_volume:
        return 'bear', 0.8
    elif high_vol:
        return 'volatile', 0.7
    elif overbought or oversold:
        return 'sideways', 0.5
    else:
        return 'sideways', 0.5
