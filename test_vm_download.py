#!/usr/bin/env python3
"""Quick test of Massive API download from VM"""
import massive_api as yf
import time

print("Testing Massive API multi-ticker download...")
start = time.time()
df = yf.download(["AAPL", "MSFT", "GOOGL"], period="1y", interval="1d", group_by="ticker")
elapsed = time.time() - start

print(f"Downloaded {len(df)} rows for 3 tickers in {elapsed:.1f}s")
print(f"Shape: {df.shape}")
tickers = list(df.columns.get_level_values(0).unique())
print(f"Tickers: {tickers}")
print(f"Last date: {df.index[-1]}")
print(f"Rate: ~{3/elapsed*60:.1f} tickers/min")
print()

# Show last 3 rows for AAPL
print("AAPL last 3 rows:")
print(df["AAPL"].tail(3))
print()
print("TEST PASSED")
