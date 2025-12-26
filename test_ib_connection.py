"""
Test IB Connection
==================
Run this with TWS open and logged in to verify API connection works.

Usage:
    python test_ib_connection.py
"""
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
import time
import threading


class TestApp(EWrapper, EClient):
    def __init__(self):
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)
        self.connected = False
        self.account_value = 0
        self.account_id = ""
    
    def nextValidId(self, orderId):
        self.connected = True
        print(f"✅ Connected! Next Order ID: {orderId}")
    
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode in [2104, 2106, 2158, 2119]:
            return  # Ignore info messages
        print(f"Error {errorCode}: {errorString}")
    
    def accountSummary(self, reqId, account, tag, value, currency):
        if tag == "NetLiquidation":
            self.account_id = account
            self.account_value = float(value)
            print(f"💰 Account: {account}")
            print(f"💰 Value: ${self.account_value:,.2f} {currency}")
    
    def accountSummaryEnd(self, reqId):
        print("✅ Account info received")


def main():
    print("="*60)
    print("IB CONNECTION TEST")
    print("="*60)
    print()
    print("⚠️  Make sure TWS is running and logged in!")
    print("⚠️  Make sure API is enabled (Edit → Global Config → API)")
    print()
    print("="*60)
    
    app = TestApp()
    
    # Connect to paper trading port
    print("\n🔌 Connecting to localhost:7497 (paper trading)...")
    
    try:
        app.connect("127.0.0.1", 7497, clientId=99)
    except Exception as e:
        print(f"\n❌ CONNECTION ERROR: {e}")
        print("\nMake sure TWS is running!")
        return
    
    # Start message thread
    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    
    # Wait for connection
    print("   Waiting for connection...")
    time.sleep(5)
    
    if not app.connected:
        print("\n" + "="*60)
        print("❌ CONNECTION FAILED!")
        print("="*60)
        print("\nTroubleshooting checklist:")
        print("  1. Is TWS running and logged in?")
        print("  2. Go to: Edit → Global Configuration → API → Settings")
        print("  3. Check: 'Enable ActiveX and Socket Clients' ✅")
        print("  4. Check: Socket port is 7497")
        print("  5. UNCHECK: 'Read-Only API' ❌")
        print("  6. Click OK and RESTART TWS")
        print("="*60)
        return
    
    # Request account info
    print("\n📊 Requesting account information...")
    app.reqAccountSummary(1, "All", "NetLiquidation")
    
    time.sleep(5)
    
    print("\n" + "="*60)
    if app.account_value > 0:
        print("✅ SUCCESS! IB connection is working!")
        print(f"   Account ID: {app.account_id}")
        print(f"   Account Value: ${app.account_value:,.2f}")
        print()
        print("You can now use trade_executor.py!")
    else:
        print("⚠️ Connected but couldn't get account value")
        print("   This might be normal if markets are closed")
    print("="*60)
    
    app.disconnect()
    print("\nDisconnected from IB")


if __name__ == "__main__":
    main()
