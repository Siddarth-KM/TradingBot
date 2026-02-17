#!/usr/bin/env python3
"""Test import chain on VM"""
import sys
print("Python:", sys.version)
print("CWD:", __import__('os').getcwd())

try:
    import massive_api
    print("✓ massive_api imported")
except Exception as e:
    print(f"✗ massive_api: {e}")

try:
    import main
    print("✓ main imported")
except Exception as e:
    print(f"✗ main: {e}")

try:
    import bot_core
    print("✓ bot_core imported")
except Exception as e:
    print(f"✗ bot_core: {e}")

try:
    import trade_executor
    print("✓ trade_executor imported")
except Exception as e:
    print(f"✗ trade_executor: {e}")

try:
    import trading_bot
    print("✓ trading_bot imported")
except Exception as e:
    print(f"✗ trading_bot: {e}")

print("ALL IMPORTS DONE")
