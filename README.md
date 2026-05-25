TradingBot
=========

INDEX-LAB swing-trading signal generator. Produces a weekly `signals.json` consumed by manual execution and downstream agents (email, Excel tracker, metrics). Execution is intentionally manual — see [archive/README.md](archive/README.md) for the rationale behind retiring the IB API auto-executor.

Quick start
- Review the Oracle deploy guide: [deploy/ORACLE_DEPLOYMENT_GUIDE.md](deploy/ORACLE_DEPLOYMENT_GUIDE.md)
- Create a virtualenv and install dependencies:
  - `python -m venv .venv`
  - Windows: `.venv\\Scripts\\activate`; Linux/macOS: `source .venv/bin/activate`
  - `pip install -r requirements.txt`

Repository files
- `trading_bot.py`: signal generation (entry point)
- `run_signals.sh`: weekly signal generation runner (invoked by systemd timer on the VM)
- `deploy/tradingbot-signals.service` / `.timer`: systemd unit + timer for weekly Monday signal gen
- `archive/trade_executor.py`: archived IB execution harness (not in active use; see archive/README.md)
- `deploy/ORACLE_DEPLOYMENT_GUIDE.md`: deployment instructions for Oracle Cloud VM

License
- This project is licensed under the MIT License. See LICENSE for details.
