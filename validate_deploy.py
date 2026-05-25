#!/usr/bin/env python3
"""Post-deployment validation for Oracle VM (signal-gen-only)."""
import sys
import os
import json


def main():
    errors = []

    core_files = [
        'trading_bot.py', 'main.py',
        'bot_config.py', 'bot_core.py', 'bot_utils.py', 'massive_api.py',
        'run_signals.sh',
    ]
    print("=" * 60)
    print("DEPLOYMENT VALIDATION (signal-gen-only)")
    print("=" * 60)

    for f in core_files:
        if not os.path.exists(f):
            errors.append(f"MISSING: {f}")
            print(f"  [X] {f}: FILE NOT FOUND")
        else:
            print(f"  [OK] {f}: exists ({os.path.getsize(f)} bytes)")

    # Imports
    print("\n--- Import Chain ---")
    for mod in ('trading_bot', 'massive_api', 'main'):
        try:
            __import__(mod)
            print(f"  [OK] {mod}: imported")
        except Exception as e:
            errors.append(f"{mod} import: {e}")
            print(f"  [X] {mod}: {e}")

    # Signals file
    print("\n--- Signals File ---")
    signals_path = 'signals/current_signals.json'
    if not os.path.exists(signals_path):
        errors.append(f"{signals_path} not found")
        print(f"  [X] {signals_path}: NOT FOUND")
    else:
        try:
            with open(signals_path) as fh:
                data = json.load(fh)
            signals = data.get('all_signals') or data.get('signals') or []
            print(f"  [OK] Loaded {len(signals)} signals")
            tickers = [s.get('ticker', '?') for s in signals]
            print(f"  Tickers: {tickers}")

            for s in signals:
                ticker = s.get('ticker', '?')
                lc = s.get('last_close', 0)
                ls = s.get('limit_sell', 0)
                d = s.get('direction', '?')
                if lc <= 0 or ls <= 0:
                    errors.append(f"{ticker}: invalid prices (close={lc}, sell={ls})")
                if str(d).lower() != 'up':
                    print(f"  [WARN] {ticker}: direction is '{d}'")
                if lc >= ls:
                    errors.append(f"{ticker}: last_close {lc} >= limit_sell {ls}")

            if len(signals) == 0:
                errors.append("No signals in file")
        except Exception as e:
            errors.append(f"Signals validation: {e}")
            print(f"  [X] Signals error: {e}")

    # Key packages (ibapi intentionally not required)
    print("\n--- Key Packages ---")
    packages = ['pandas', 'numpy', 'catboost', 'torch', 'sklearn', 'scipy']
    for pkg in packages:
        try:
            mod = __import__(pkg)
            ver = getattr(mod, '__version__', 'ok')
            print(f"  [OK] {pkg}: {ver}")
        except ImportError:
            errors.append(f"Missing package: {pkg}")
            print(f"  [X] {pkg}: NOT INSTALLED")

    print(f"\n{'=' * 60}")
    if errors:
        print(f"VALIDATION FAILED - {len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("VALIDATION PASSED - signal-gen pipeline ready")
        sys.exit(0)


if __name__ == '__main__':
    main()
