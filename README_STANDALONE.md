# Trading Bot - Standalone Version

A standalone trading bot that generates buy signals for stocks across multiple indexes using machine learning and sentiment analysis. **No web interface required** - runs entirely from the command line.

## Features

- ✅ **Fully Standalone**: No Flask, no web server, no frontend dependencies
- 📊 **Multi-Index Analysis**: Analyzes SPY, NASDAQ, SP400, and SPSM indexes
- 🤖 **ML-Powered**: Uses ensemble models (XGBoost, Random Forest, CatBoost, etc.)
- 📰 **Sentiment Analysis**: Incorporates FinBERT sentiment from news articles
- 🎯 **Top Stock Selection**: Returns top 2 stocks per index with positive UP predictions
- 💾 **Multiple Output Formats**: Saves results as JSON and CSV
- ⚙️ **Configurable**: Command-line arguments for customization

## File Structure

```
TradingBot/
├── trading_bot.py      # Main executable - run this!
├── bot_config.py       # All configuration constants
├── bot_utils.py        # Utility functions
├── bot_core.py         # Core trading logic (imports from main.py)
├── main.py             # Original code with all implementations
├── signals/            # Output directory (auto-created)
│   ├── signals_YYYYMMDD_HHMMSS.json
│   └── signals_YYYYMMDD_HHMMSS.csv
└── .venv/              # Python virtual environment
```

## Requirements

- Python 3.13+
- Virtual environment with all dependencies installed (see requirenments.txt)

## Installation

All dependencies should already be installed from `requirenments.txt`. If not:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirenments.txt
```

## Usage

### Basic Usage (All Indexes)

```powershell
.\.venv\Scripts\python.exe trading_bot.py
```

This will:
- Analyze all 4 indexes (SPY, NASDAQ, SP400, SPSM)
- Use 5-day prediction window
- Pull 18 months of historical data
- Save results as both JSON and CSV

### Custom Index Selection

```powershell
# Analyze only SPY and NASDAQ
.\.venv\Scripts\python.exe trading_bot.py --indexes SPY NASDAQ

# Analyze only SPY
.\.venv\Scripts\python.exe trading_bot.py --indexes SPY
```

### Custom Prediction Window

```powershell
# 10-day prediction window instead of 5
.\.venv\Scripts\python.exe trading_bot.py --window 10
```

### Force Data Refresh

```powershell
# Bypass cache and download fresh data
.\.venv\Scripts\python.exe trading_bot.py --refresh
```

### Output Format Selection

```powershell
# JSON only
.\.venv\Scripts\python.exe trading_bot.py --output json

# CSV only
.\.venv\Scripts\python.exe trading_bot.py --output csv

# Both (default)
.\.venv\Scripts\python.exe trading_bot.py --output both
```

### Custom Start Date

```powershell
# Use data from specific date
.\.venv\Scripts\python.exe trading_bot.py --start-date 2023-01-01
```

### Combined Options

```powershell
# Full custom run
.\.venv\Scripts\python.exe trading_bot.py ^
    --indexes SPY NASDAQ ^
    --window 7 ^
    --refresh ^
    --output json ^
    --start-date 2023-06-01
```

## Output

### Console Output

The bot displays:
- Initialization status
- Download progress
- Market regime detection
- Model training progress
- Sentiment analysis results
- Final signals with:
  - Ticker symbol
  - Predicted return percentage
  - Direction (UP/DOWN) with confidence
  - Current price
  - Hold period

### JSON Output

Saved to `signals/signals_YYYYMMDD_HHMMSS.json`:

```json
{
  "timestamp": "2025-10-29T16:38:25",
  "prediction_window": 5,
  "total_signals": 8,
  "signals_by_index": {
    "SPY": [
      {
        "ticker": "AAPL",
        "action": "BUY",
        "predicted_return_pct": 3.45,
        "direction": "up",
        "direction_probability": 78.5,
        "last_close": 150.23,
        "hold_days": 5
      }
    ]
  },
  "summary": {
    "avg_predicted_return": 2.87,
    "best_signal": "AAPL",
    "best_return": 3.45
  }
}
```

### CSV Output

Saved to `signals/signals_YYYYMMDD_HHMMSS.csv`:

```csv
ticker,action,index,predicted_return_pct,direction,direction_probability,last_close,hold_days,timestamp
AAPL,BUY,SPY,3.45,up,78.5,150.23,5,2025-10-29T16:38:25
MSFT,BUY,SPY,2.89,up,82.1,380.45,5,2025-10-29T16:38:25
```

## How It Works

1. **Data Collection**: Downloads historical stock data for index constituents
2. **Feature Engineering**: Calculates 50+ technical indicators (RSI, MACD, Bollinger Bands, etc.)
3. **Market Regime Detection**: Identifies current market condition (bull/bear/sideways/volatile)
4. **Model Training**: Trains ensemble ML models tailored to market regime
5. **Sentiment Analysis**: Analyzes news sentiment using FinBERT
6. **Prediction Generation**: Generates 5-day return predictions
7. **Directional Confidence**: Adds UP/DOWN classification with CatBoost
8. **Filtering**: Keeps only stocks with positive predictions AND UP direction
9. **Selection**: Picks top 2 stocks per index by predicted return
10. **Output**: Saves and displays trading signals

## Configuration

Edit `bot_config.py` to modify:
- Default indexes to analyze
- Prediction window (days)
- Lookback period (months)
- Number of stocks per index
- API keys
- Model parameters
- Output directory

## Troubleshooting

### "No data found" errors
Some tickers may have timezone issues or be delisted. This is normal - the bot will skip them and continue.

### Slow execution
- Use `--indexes SPY` to test with one index first
- The first run downloads data; subsequent runs use cache
- Use `--refresh` to force fresh data download

### Module import errors
Ensure you're running from the correct directory with the virtual environment:
```powershell
cd c:\Users\sidda\Downloads\TradingBot
.\.venv\Scripts\python.exe trading_bot.py
```

## Scheduling

### Windows Task Scheduler

To run daily at market open (9:30 AM EST):

1. Open Task Scheduler
2. Create Basic Task
3. Set trigger: Daily at 9:30 AM
4. Action: Start a program
   - Program: `C:\Users\sidda\Downloads\TradingBot\.venv\Scripts\python.exe`
   - Arguments: `trading_bot.py`
   - Start in: `C:\Users\sidda\Downloads\TradingBot`

### Manual Batch Script

Create `run_bot.bat`:
```batch
@echo off
cd /d C:\Users\sidda\Downloads\TradingBot
.\.venv\Scripts\python.exe trading_bot.py
pause
```

Double-click to run!

## Notes

- The bot uses cached data by default (valid for 24 hours)
- First run takes longer as it downloads all data
- Sentiment model loads once at startup
- Results are timestamped to avoid overwriting
- The bot doesn't execute trades - it only generates signals

## Support

For issues or questions, review the console output for detailed error messages. The bot prints extensive logging to help debug any issues.

---

**Disclaimer**: This bot is for educational and research purposes only. Always do your own research before making investment decisions.
