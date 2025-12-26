TradingBot
=========

Automated trading bot using Interactive Brokers (IB Gateway) for paper trading.

Quick start
- Review the Oracle deploy guide: [deploy/ORACLE_DEPLOYMENT_GUIDE.md](deploy/ORACLE_DEPLOYMENT_GUIDE.md)
- Create a virtualenv and install dependencies:
  - `python -m venv .venv`
  - Windows: `.venv\\Scripts\\activate`; Linux/macOS: `source .venv/bin/activate`
  - `pip install -r requirements.txt`
- Configure credentials and IB Gateway settings as described in the Oracle guide.

Repository files
- `trade_executor.py`: scheduler and IB execution engine
- `trading_bot.py`: signal generation
- `deploy/ORACLE_DEPLOYMENT_GUIDE.md`: deployment instructions for Oracle Cloud VM

License
- This project is licensed under the MIT License. See LICENSE for details.
