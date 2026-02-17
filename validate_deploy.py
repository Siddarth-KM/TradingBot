#!/usr/bin/env python3
"""Post-deployment validation for Oracle VM."""
import sys
import os

def main():
    errors = []
    
    # 1. Syntax check all core files
    core_files = [
        'trade_executor.py', 'trading_bot.py', 'main.py',
        'bot_config.py', 'bot_core.py', 'bot_utils.py', 'massive_api.py'
    ]
    print("=" * 60)
    print("DEPLOYMENT VALIDATION")
    print("=" * 60)
    
    for f in core_files:
        if not os.path.exists(f):
            errors.append(f"MISSING: {f}")
            print(f"  ✗ {f}: FILE NOT FOUND")
        else:
            print(f"  ✓ {f}: exists ({os.path.getsize(f)} bytes)")
    
    # 2. Import chain
    print("\n--- Import Chain ---")
    try:
        from trade_executor import (
            US_MARKET_HOLIDAYS, is_market_holiday, IBAutoTrader,
            load_signals, save_pending_orders, load_pending_orders,
            clear_pending_orders, run_scheduled_cycle, execute_once,
            sell_all, check_pending, LIMIT_ORDER_HOUR, MARKET_ORDER_HOUR,
            MAX_POSITIONS, LIMIT_PREMIUM_PCT, MAX_CONNECTION_RETRIES,
            PAPER_TRADING_PORT
        )
        print("  ✓ trade_executor: all functions imported")
    except Exception as e:
        errors.append(f"trade_executor import: {e}")
        print(f"  ✗ trade_executor: {e}")
    
    try:
        import trading_bot
        print("  ✓ trading_bot: imported")
    except Exception as e:
        errors.append(f"trading_bot import: {e}")
        print(f"  ✗ trading_bot: {e}")
    
    try:
        import massive_api
        print("  ✓ massive_api: imported")
    except Exception as e:
        errors.append(f"massive_api import: {e}")
        print(f"  ✗ massive_api: {e}")
    
    try:
        import main
        print("  ✓ main: imported")
    except Exception as e:
        errors.append(f"main import: {e}")
        print(f"  ✗ main: {e}")

    # 3. Validate trade_executor config
    print("\n--- Trade Executor Config ---")
    try:
        from trade_executor import (
            US_MARKET_HOLIDAYS, is_market_holiday, LIMIT_ORDER_HOUR,
            MARKET_ORDER_HOUR, MAX_POSITIONS, LIMIT_PREMIUM_PCT,
            PAPER_TRADING_PORT
        )
        from datetime import date
        
        holidays_2026 = sum(1 for d in US_MARKET_HOLIDAYS if d.year == 2026)
        print(f"  Total holidays in DB: {len(US_MARKET_HOLIDAYS)}")
        print(f"  2026 holidays: {holidays_2026}")
        if holidays_2026 < 10:
            errors.append(f"Only {holidays_2026} holidays for 2026 (expected 10)")
        
        today_is_holiday = is_market_holiday()
        print(f"  Today is holiday: {today_is_holiday}")
        from trade_executor import LIMIT_ORDER_MINUTE
        print(f"  Limit order hour: {LIMIT_ORDER_HOUR}:{LIMIT_ORDER_MINUTE:02d} CST (Monday)")
        print(f"  Market order hour: {MARKET_ORDER_HOUR}:00 CST (Tuesday)")
        print(f"  Max positions: {MAX_POSITIONS}")
        print(f"  Limit premium: {LIMIT_PREMIUM_PCT*100:.1f}%")
        print(f"  IB Gateway port: {PAPER_TRADING_PORT}")
        
        if LIMIT_ORDER_HOUR != 16:
            errors.append(f"LIMIT_ORDER_HOUR is {LIMIT_ORDER_HOUR}, expected 16")
        if MARKET_ORDER_HOUR != 8:
            errors.append(f"MARKET_ORDER_HOUR is {MARKET_ORDER_HOUR}, expected 8")
        if MAX_POSITIONS != 8:
            errors.append(f"MAX_POSITIONS is {MAX_POSITIONS}, expected 8")
        if PAPER_TRADING_PORT != 4002:
            errors.append(f"PAPER_TRADING_PORT is {PAPER_TRADING_PORT}, expected 4002")
            
    except Exception as e:
        errors.append(f"Config validation: {e}")
        print(f"  ✗ Config error: {e}")
    
    # 4. Validate signals file
    print("\n--- Signals File ---")
    try:
        from trade_executor import load_signals
        signals_path = 'signals/current_signals.json'
        if not os.path.exists(signals_path):
            errors.append("signals/current_signals.json not found")
            print(f"  ✗ {signals_path}: NOT FOUND")
        else:
            signals = load_signals(signals_path)
            print(f"  ✓ Loaded {len(signals)} signals")
            tickers = [s.get('ticker', '?') for s in signals]
            print(f"  Tickers: {tickers}")
            
            for s in signals:
                ticker = s.get('ticker', '?')
                lc = s.get('last_close', 0)
                ls = s.get('limit_sell', 0)
                d = s.get('direction', '?')
                if lc <= 0 or ls <= 0:
                    errors.append(f"{ticker}: invalid prices (close={lc}, sell={ls})")
                if d.lower() != 'up':
                    print(f"  ⚠ {ticker}: direction is '{d}' (won't be traded)")
                if lc >= ls:
                    errors.append(f"{ticker}: last_close {lc} >= limit_sell {ls}")
                    
            if len(signals) == 0:
                errors.append("No signals in file")
    except Exception as e:
        errors.append(f"Signals validation: {e}")
        print(f"  ✗ Signals error: {e}")
    
    # 5. Check IB Gateway port
    print("\n--- IB Gateway ---")
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', 4002))
    sock.close()
    if result == 0:
        print("  ✓ IB Gateway port 4002 is listening")
    else:
        print("  ⚠ IB Gateway port 4002 is NOT listening (start IB Gateway first)")
    
    # 6. Check virtualenv packages
    print("\n--- Key Packages ---")
    packages = ['pandas', 'numpy', 'catboost', 'torch', 'sklearn', 'ibapi', 'scipy']
    for pkg in packages:
        try:
            mod = __import__(pkg)
            ver = getattr(mod, '__version__', 'ok')
            print(f"  ✓ {pkg}: {ver}")
        except ImportError:
            errors.append(f"Missing package: {pkg}")
            print(f"  ✗ {pkg}: NOT INSTALLED")
    
    # 7. Pending orders file check
    print("\n--- Order Tracking ---")
    pending_path = 'signals/pending_orders.json'
    if os.path.exists(pending_path):
        import json
        with open(pending_path) as f:
            data = json.load(f)
        orders = data.get('orders', {})
        print(f"  ⚠ Pending orders file exists with {len(orders)} orders")
        print(f"    Created: {data.get('created_at', '?')}")
    else:
        print("  ✓ No stale pending orders file")
    
    # Final result
    print(f"\n{'=' * 60}")
    if errors:
        print(f"VALIDATION FAILED - {len(errors)} error(s):")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print("✅ VALIDATION PASSED - VM is ready for production")
        sys.exit(0)

if __name__ == '__main__':
    main()
