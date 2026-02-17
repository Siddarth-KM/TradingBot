"""
Massive.com API Wrapper (formerly Polygon.io)
Drop-in replacement for yfinance using paid tier (unlimited calls)

Author: Trading Bot
Date: February 2026
"""

import os
import time
import logging
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

try:
    from massive import RESTClient
    MASSIVE_AVAILABLE = True
except ImportError:
    MASSIVE_AVAILABLE = False
    print("WARNING: massive package not installed. Run: pip install massive")

# ============================================================================
# CONFIGURATION
# ============================================================================

# Massive.com API key - loaded from environment variable (never hardcode)
MASSIVE_API_KEY = os.environ.get("MASSIVE_API_KEY", "")
if not MASSIVE_API_KEY:
    logging.warning("MASSIVE_API_KEY not set. Export it: export MASSIVE_API_KEY=your_key_here")

# Paid tier: no rate limiting needed
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds to wait on transient errors

# Global client instance
_client = None

def _get_client():
    """Get or create Massive.com REST client"""
    global _client
    if _client is None:
        if not MASSIVE_AVAILABLE:
            raise ImportError("massive package not installed. Run: pip install massive")
        _client = RESTClient(api_key=MASSIVE_API_KEY)
    return _client

# ============================================================================
# CORE DATA FETCHING
# ============================================================================

def download(tickers, start=None, end=None, progress=False, group_by='ticker', **kwargs):
    """
    Download OHLCV data for one or multiple tickers.
    
    yfinance-compatible interface:
    - Single ticker: returns DataFrame with columns [Open, High, Low, Close, Volume, Adj Close]
    - Multiple tickers: returns DataFrame with MultiIndex columns [(ticker, column), ...]
    
    Args:
        tickers: str or list of str - ticker symbol(s)
        start: str or datetime - start date (default: 2 years ago)
        end: str or datetime - end date (default: today)
        progress: bool - ignored (for yfinance compatibility)
        group_by: str - 'ticker' for MultiIndex columns (default)
    
    Returns:
        pd.DataFrame with OHLCV data
    """
    # Handle single ticker vs list
    if isinstance(tickers, str):
        single_ticker = True
        ticker_list = [tickers]
    else:
        single_ticker = False
        ticker_list = list(tickers)
    
    # Parse dates
    if start is None:
        start_date = datetime.now() - timedelta(days=730)  # 2 years
    elif isinstance(start, str):
        start_date = datetime.strptime(start.split()[0], '%Y-%m-%d')
    else:
        start_date = start
    
    if end is None:
        end_date = datetime.now()
    elif isinstance(end, str):
        end_date = datetime.strptime(end.split()[0], '%Y-%m-%d')
    else:
        end_date = end
    
    # Format dates for API (YYYY-MM-DD)
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')
    
    # Download data for each ticker
    all_data = {}
    
    for ticker in ticker_list:
        df = _download_single(ticker, start_str, end_str)
        if df is not None and not df.empty:
            all_data[ticker] = df
    
    # Return empty DataFrame if no data
    if not all_data:
        return pd.DataFrame()
    
    # Single ticker: return simple DataFrame
    if single_ticker:
        return all_data.get(ticker_list[0], pd.DataFrame())
    
    # Multiple tickers: return MultiIndex DataFrame
    if group_by == 'ticker':
        # Create MultiIndex columns: (ticker, column)
        dfs = []
        for ticker, df in all_data.items():
            df_copy = df.copy()
            df_copy.columns = pd.MultiIndex.from_product([[ticker], df_copy.columns])
            dfs.append(df_copy)
        
        if dfs:
            result = pd.concat(dfs, axis=1)
            return result
    
    return pd.DataFrame()


def _download_single(ticker, start_str, end_str):
    """
    Download OHLCV data for a single ticker.
    
    Returns:
        pd.DataFrame with columns [Open, High, Low, Close, Volume, Adj Close]
        Index is DatetimeIndex
    """
    client = _get_client()
    
    # Handle special tickers (indexes with ^)
    api_ticker = ticker.replace('^', '')
    
    # Map common index symbols to Massive.com format
    ticker_mapping = {
        'VIX': 'VIX',       # VIX is available
        'DXY': 'UUP',       # US Dollar Index -> UUP ETF as proxy
        '^VIX': 'VIX',
        '^DXY': 'UUP',
        '^GSPC': 'SPY',     # S&P 500 -> SPY
        '^NDX': 'QQQ',      # NASDAQ 100 -> QQQ
        '^DJI': 'DIA',      # Dow Jones -> DIA
    }
    
    api_ticker = ticker_mapping.get(ticker, api_ticker)
    
    for attempt in range(MAX_RETRIES):
        try:
            # Fetch aggregates (daily bars)
            aggs = list(client.list_aggs(
                ticker=api_ticker,
                multiplier=1,
                timespan="day",
                from_=start_str,
                to=end_str,
                limit=50000
            ))
            
            if not aggs:
                logging.warning(f"[Massive API] No data returned for {ticker}")
                return None
            
            # Convert to DataFrame
            # Note: Polygon timestamps daily bars at midnight ET, which appears as
            # 23:00 CST on the previous calendar day. We adjust to show the actual trading date.
            data = []
            for bar in aggs:
                # Get the raw timestamp
                raw_dt = datetime.fromtimestamp(bar.timestamp / 1000)
                
                # If hour is 23 (midnight ET = 11PM CST), it's actually the next day's bar
                # Add 1 hour to get the correct trading date
                if raw_dt.hour == 23:
                    trading_date = raw_dt + timedelta(hours=1)
                else:
                    trading_date = raw_dt
                
                # Normalize to just the date (remove time component for daily bars)
                trading_date = trading_date.replace(hour=0, minute=0, second=0, microsecond=0)
                
                data.append({
                    'Date': trading_date,
                    'Open': bar.open,
                    'High': bar.high,
                    'Low': bar.low,
                    'Close': bar.close,
                    'Volume': bar.volume,
                    'Adj Close': bar.close,  # Massive doesn't have adjusted, use close
                    'VWAP': getattr(bar, 'vwap', None)
                })
            
            df = pd.DataFrame(data)
            df.set_index('Date', inplace=True)
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            
            # Ensure numeric types
            for col in ['Open', 'High', 'Low', 'Close', 'Volume', 'Adj Close']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            return df
            
        except Exception as e:
            error_str = str(e).lower()
            
            # Transient/rate errors - wait and retry
            if 'rate' in error_str or '429' in error_str or 'limit' in error_str or '5' in error_str[:1]:
                wait = RETRY_DELAY * (attempt + 1)
                logging.warning(f"[Massive API] Transient error on {ticker}, waiting {wait}s...")
                time.sleep(wait)
                continue
            
            # Other errors
            if attempt < MAX_RETRIES - 1:
                logging.warning(f"[Massive API] Error for {ticker}: {e}, retrying...")
                time.sleep(2)
                continue
            else:
                logging.error(f"[Massive API] Failed to download {ticker} after {MAX_RETRIES} attempts: {e}")
                return None
    
    return None


# ============================================================================
# TICKER CLASS (yfinance compatibility)
# ============================================================================

class Ticker:
    """
    yfinance-compatible Ticker class.
    
    Note: Massive.com has limited info compared to yfinance.
    Some properties return defaults or empty values.
    """
    
    def __init__(self, ticker, session=None):
        self.ticker = ticker
        self._info = None
        self._history = None
    
    @property
    def info(self):
        """
        Get ticker info. Limited compared to yfinance.
        Returns basic info dict with defaults for missing fields.
        """
        if self._info is None:
            self._info = {
                'symbol': self.ticker,
                'sector': 'Unknown',
                'industry': 'Unknown',
                'shortName': self.ticker,
                'longName': self.ticker,
            }
            
            # Try to get some info from ticker details
            try:
                client = _get_client()
                details = client.get_ticker_details(self.ticker)
                
                if details:
                    self._info.update({
                        'shortName': getattr(details, 'name', self.ticker),
                        'longName': getattr(details, 'name', self.ticker),
                        'sector': getattr(details, 'sic_description', 'Unknown'),
                        'industry': getattr(details, 'sic_description', 'Unknown'),
                        'description': getattr(details, 'description', ''),
                        'marketCap': getattr(details, 'market_cap', None),
                        'employees': getattr(details, 'total_employees', None),
                    })
            except Exception as e:
                logging.debug(f"[Massive API] Could not get details for {self.ticker}: {e}")
        
        return self._info
    
    def history(self, period=None, start=None, end=None, **kwargs):
        """
        Get historical OHLCV data.
        
        Args:
            period: str - e.g., '1y', '6mo', '1mo', '5d' (if start/end not provided)
            start: str or datetime
            end: str or datetime
        
        Returns:
            pd.DataFrame with OHLCV data
        """
        # Parse period if start not provided
        if start is None and period:
            period_map = {
                '1d': 1, '5d': 5, '1mo': 30, '3mo': 90,
                '6mo': 180, '1y': 365, '2y': 730, '5y': 1825, 'max': 3650
            }
            days = period_map.get(period, 365)
            start = datetime.now() - timedelta(days=days)
        
        return download(self.ticker, start=start, end=end, **kwargs)


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================




def test_connection():
    """Test API connection"""
    try:
        client = _get_client()
        
        # Test with data from 3 days ago
        end_date = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
        
        aggs = list(client.list_aggs(
            ticker="SPY",
            multiplier=1,
            timespan="day",
            from_=start_date,
            to=end_date,
            limit=10
        ))
        
        if aggs:
            print(f"✅ Massive.com API connection successful")
            print(f"   API Key: {MASSIVE_API_KEY[:8]}...")
            print(f"   Tier: Paid (no rate limiting)")
            print(f"   Test data: {len(aggs)} bars for SPY")
            return True
        else:
            print(f"❌ No data returned from Massive.com API")
            return False
        
    except Exception as e:
        print(f"❌ Massive.com API connection failed: {e}")
        return False


# ============================================================================
# MODULE-LEVEL ALIASES (for yfinance compatibility)
# ============================================================================

# These allow `from massive_api import download` or `massive_api.download(...)`
# Same interface as `yf.download(...)` or `yfinance.download(...)`

__all__ = ['download', 'Ticker', 'test_connection']
