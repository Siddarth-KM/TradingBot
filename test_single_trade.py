#!/usr/bin/env python3
"""
Simple test script to verify IB Gateway connection and order placement
Tests buying 1 share of SPY
"""

import time
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
import threading

class TestTradingApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.nextOrderId = None
        self.order_status = {}
        self.connected = False
        
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        print(f"Error {errorCode}: {errorString}")
        
    def nextValidId(self, orderId):
        self.nextOrderId = orderId
        self.connected = True
        print(f"✅ Connected to IB Gateway. Next order ID: {orderId}")
        
    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice, 
                    permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
        self.order_status[orderId] = {
            'status': status,
            'filled': filled,
            'remaining': remaining,
            'avgFillPrice': avgFillPrice
        }
        print(f"📊 Order {orderId}: {status} | Filled: {filled} | Remaining: {remaining} | Avg Price: ${avgFillPrice:.2f}")
        
    def execDetails(self, reqId, contract, execution):
        print(f"✅ EXECUTION: {execution.shares} shares of {contract.symbol} @ ${execution.price:.2f}")

def create_stock_contract(symbol):
    """Create a stock contract"""
    contract = Contract()
    contract.symbol = symbol
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    contract.primaryExchange = "NASDAQ"  # For routing
    return contract

def create_market_order(action, quantity):
    """Create a market order"""
    order = Order()
    order.action = action  # "BUY" or "SELL"
    order.orderType = "MKT"
    order.totalQuantity = quantity
    order.transmit = True
    order.eTradeOnly = False
    order.firmQuoteOnly = False
    return order

def run_test(host='127.0.0.1', port=4002):
    """Test connection and place a single order"""
    
    print("=" * 70)
    print("🧪 IB GATEWAY TRADE TEST")
    print("=" * 70)
    print(f"Connecting to IB Gateway at {host}:{port}...")
    
    # Create app instance
    app = TestTradingApp()
    
    # Connect in a separate thread
    app.connect(host, port, clientId=999)
    
    # Start the socket in a thread
    api_thread = threading.Thread(target=app.run, daemon=True)
    api_thread.start()
    
    # Wait for connection
    timeout = 10
    start_time = time.time()
    while not app.connected and (time.time() - start_time) < timeout:
        time.sleep(0.1)
    
    if not app.connected:
        print("❌ Failed to connect to IB Gateway")
        print("Make sure:")
        print("  1. IB Gateway is running")
        print("  2. Port 4002 is configured")
        print("  3. API connections are enabled")
        return False
    
    print(f"\n{'='*70}")
    print("📈 PLACING TEST ORDER: BUY 1 share of SPY")
    print(f"{'='*70}\n")
    
    # Create contract and order
    contract = create_stock_contract("SPY")
    order = create_market_order("BUY", 1)
    
    # Place order
    order_id = app.nextOrderId
    app.placeOrder(order_id, contract, order)
    print(f"🔔 Order submitted with ID: {order_id}")
    
    # Wait for order status updates
    print("\nWaiting for order confirmation (30 seconds max)...")
    timeout = 30
    start_time = time.time()
    
    while (time.time() - start_time) < timeout:
        if order_id in app.order_status:
            status_info = app.order_status[order_id]
            status = status_info['status']
            
            if status in ['Filled', 'Cancelled', 'ApiCancelled']:
                print(f"\n{'='*70}")
                if status == 'Filled':
                    print("✅ ORDER FILLED SUCCESSFULLY!")
                    print(f"   Shares: {status_info['filled']}")
                    print(f"   Avg Price: ${status_info['avgFillPrice']:.2f}")
                    print("✅ Trade executor is working correctly!")
                else:
                    print(f"⚠️  Order {status}")
                print(f"{'='*70}\n")
                break
        time.sleep(0.5)
    else:
        print("\n⏱️  Timeout waiting for order confirmation")
        print("Check IB Gateway for order status")
    
    # Disconnect
    app.disconnect()
    print("\n🔌 Disconnected from IB Gateway")
    
    return True

if __name__ == "__main__":
    try:
        run_test()
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
