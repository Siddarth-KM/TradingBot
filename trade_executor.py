"""
Interactive Brokers Auto-Trader v2.0 - Citadel-Quality Implementation
=============================================================
Executes trades from trading bot signals using IB API with smart order routing.

Features:
- Monday 4:00 PM CST: Generate trading signals (run predictor)
- Monday 4:30 PM CST: Place LIMIT orders at live_price * 1.001 (0.1% above market)
  - Retries every 30 minutes until all orders placed (deadline 10 PM CST)
- Tuesday 8:00 AM CST: Cancel unfilled orders, replace with MARKET orders
- Live price fetching from IB API (fallback to last_close if unavailable)
- Dynamic account value from IB (position size = account_value / 8)
- Bracket orders with profit target and stop loss
- Order tracking via JSON persistence between Monday and Tuesday
- Comprehensive edge case handling:
  - Skip if live_price >= limit_sell (overnight gap up)
  - Skip if already holding position
  - Handle partial fills (cancel remaining, place market for unfilled)
  - Market holiday detection
  - Connection retry with exponential backoff (3 attempts)
  - IB Gateway disconnection handling with reconnect

Usage:
    python trade_executor.py schedule          # Run dual-schedule mode
    python trade_executor.py <signals_json>    # Manual execute
    python trade_executor.py sell              # Sell all positions
    
TWS/Gateway Settings:
    - Enable API: Edit → Global Config → API → Settings
    - Socket port: 4002 (paper) or 4001 (live)
    - Allow localhost connections
    - Disable Read-Only API
"""

import json
import sys
import time
import math
import threading
import subprocess
import os
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
import socket as socket_module

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.ticktype import TickTypeEnum


# Configuration
PAPER_TRADING_PORT = 4002  # IB Gateway paper trading port
LIVE_TRADING_PORT = 4001   # IB Gateway live trading port
CLIENT_ID = 1

# Fixed signals file path (trading_bot.py always saves to this location)
SIGNALS_FILE = "signals/current_signals.json"
ORDER_TRACKING_FILE = "signals/pending_orders.json"  # Track orders between Monday and Tuesday

# Scheduling Configuration
CST_TIMEZONE = ZoneInfo("America/Chicago")

# PHASE 0: Signal Generation (Monday 4:00 PM CST)
SIGNAL_GEN_DAY = 0        # Monday = 0
SIGNAL_GEN_HOUR = 16      # 4:00 PM CST
SIGNAL_GEN_MINUTE = 0

# PHASE 1: Limit Order Placement (Monday 4:30 PM CST - after signals generated)
LIMIT_ORDER_DAY = 0       # Monday = 0
LIMIT_ORDER_HOUR = 16     # 4:30 PM CST
LIMIT_ORDER_MINUTE = 30
LIMIT_ORDER_RETRY_MINUTES = 30   # Retry every 30 minutes until orders placed
LIMIT_ORDER_DEADLINE_HOUR = 22   # Monday 10 PM CST - stop trying

# PHASE 2: Market Order Fallback (Tuesday 8 AM CST - before market open)
MARKET_ORDER_DAY = 1      # Tuesday = 1
MARKET_ORDER_HOUR = 8     # 8:00 AM CST - before market open (8:30 AM CST)
MARKET_ORDER_MINUTE = 0
MARKET_ORDER_DEADLINE_HOUR = 9   # Tuesday 9 AM CST - retry market fallback until 30 min after open

MAX_POSITIONS = 8  # Fixed 8 positions, 1/8 of account each

# Limit order premium (0.1% above current market price)
LIMIT_PREMIUM_PCT = 0.001

# Connection retry settings
MAX_CONNECTION_RETRIES = 3
RETRY_DELAY_SECONDS = 5

# Index-based stop loss percentages
STOP_LOSS_PERCENTAGES = {
    'SPY': 0.02,      # 2% for S&P 500
    'NASDAQ': 0.02,   # 2% for NASDAQ     # 2% for NASDAQ ETF
    'SPSM': 0.03,     # 3% for S&P 600 Small Cap
    'MDY': 0.03,      # 3% for S&P 400 Mid Cap
}
DEFAULT_STOP_LOSS = 0.025  # 2.5% default


class IBAutoTrader(EWrapper, EClient):
    """
    Interactive Brokers Automated Trading Engine v2.0
    
    Features:
    - Live price fetching via IB API
    - Limit orders at market + 0.1% premium
    - Market order fallback for unfilled orders
    - Dynamic account value fetching
    - Partial fill handling
    - Connection retry with exponential backoff
    """
    
    def __init__(self):
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)
        
        # Connection state
        self.next_order_id: Optional[int] = None
        self.connected = threading.Event()
        self.account_ready = threading.Event()
        
        # Account data
        self.account_value: float = 0.0
        self.positions: Dict[str, float] = {}
        
        # Order tracking
        self.order_status_map: Dict[int, dict] = {}  # Enhanced: stores full status info
        self.filled_orders: Dict[int, dict] = {}
        self.active_brackets: Dict[str, List[int]] = {}  # symbol -> [entry_id, profit_id, stop_id]
        
        # Live price data
        self.live_prices: Dict[str, float] = {}  # symbol -> last price
        self.price_ready: Dict[str, threading.Event] = {}  # symbol -> event
        self.price_request_ids: Dict[int, str] = {}  # reqId -> symbol
        
        # Disconnection handling
        self._reconnecting = False
        self._connection_lost = threading.Event()
        
    # ===== EWrapper Callbacks =====
    
    def nextValidId(self, orderId: int):
        """Called when connection is established with next valid order ID."""
        self.next_order_id = orderId
        self.connected.set()
        self._reconnecting = False
        
    def connectionClosed(self):
        """Called when connection to IB is lost."""
        print("⚠️ IB Gateway connection lost!")
        self._connection_lost.set()
        self.connected.clear()
        
    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = ""):
        """Handle errors from IB."""
        # Ignore non-critical messages
        if errorCode in [2104, 2106, 2158]:  # Market data farm connected messages
            return
        if errorCode == 2119:  # Market data farm is connecting
            return
        if errorCode == 2103:  # Market data connection is OK
            return
        if errorCode == 165:  # Historical data farm connection
            return
        
        # Connection errors - trigger reconnection
        if errorCode in [502, 504, 1100, 1101, 1102]:
            print(f"⚠️ Connection error {errorCode}: {errorString}")
            self._connection_lost.set()
            return
            
        # Price data not available
        if errorCode == 354:  # No subscription for real-time data
            print(f"⚠️ No market data subscription for reqId {reqId}")
            # Set price to 0 so fallback kicks in
            if reqId in self.price_request_ids:
                symbol = self.price_request_ids[reqId]
                self.live_prices[symbol] = 0
                if symbol in self.price_ready:
                    self.price_ready[symbol].set()
            return
            
        print(f"IB Error {errorCode}: {errorString}")
        
    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str):
        """Receive account summary data."""
        if tag == "NetLiquidation":
            self.account_value = float(value)
            
    def accountSummaryEnd(self, reqId: int):
        """Account summary request completed."""
        self.account_ready.set()
        
    def position(self, account: str, contract: Contract, pos: float, avgCost: float):
        """Receive position updates."""
        self.positions[contract.symbol] = pos
        
    def positionEnd(self):
        """Position request completed."""
        pass
        
    def orderStatus(self, orderId: int, status: str, filled: float, remaining: float,
                    avgFillPrice: float, permId: int, parentId: int, lastFillPrice: float,
                    clientId: int, whyHeld: str, mktCapPrice: float):
        """Track order status updates with full details."""
        self.order_status_map[orderId] = {
            'status': status,
            'filled': filled,
            'remaining': remaining,
            'avgFillPrice': avgFillPrice,
            'parentId': parentId
        }
        
        if status == "Filled":
            self.filled_orders[orderId] = {
                'filled': filled,
                'avgPrice': avgFillPrice
            }
            
    def openOrder(self, orderId: int, contract: Contract, order: Order, orderState):
        """Receive open order details."""
        if orderId not in self.order_status_map:
            self.order_status_map[orderId] = {
                'status': orderState.status,
                'filled': 0,
                'remaining': order.totalQuantity,
                'avgFillPrice': 0,
                'symbol': contract.symbol
            }
            
    def tickPrice(self, reqId: int, tickType: int, price: float, attrib):
        """Receive live price tick data."""
        if reqId not in self.price_request_ids:
            return
            
        symbol = self.price_request_ids[reqId]
        
        # LAST price (tick type 4) or CLOSE price (tick type 9)
        if tickType in [4, 9] and price > 0:
            self.live_prices[symbol] = price
            if symbol in self.price_ready:
                self.price_ready[symbol].set()
                
    def tickSize(self, reqId: int, tickType: int, size: int):
        """Receive tick size data (required callback)."""
        pass
        
    def tickGeneric(self, reqId: int, tickType: int, value: float):
        """Receive generic tick data (required callback)."""
        pass
        
    def tickString(self, reqId: int, tickType: int, value: str):
        """Receive tick string data (required callback)."""
        pass
            
    def execDetails(self, reqId: int, contract: Contract, execution):
        """Handle execution details for order tracking."""
        pass  # Reserved for future execution analytics
    
    # ===== Helper Methods =====
    
    def _get_next_order_id(self) -> int:
        """Atomically get and increment order ID."""
        order_id = self.next_order_id
        self.next_order_id += 1
        return order_id
    
    def _create_order(self, action: str, quantity: int, order_type: str,
                      price: float = 0.0, stop_price: float = 0.0,
                      tif: str = "DAY", outside_rth: bool = True,
                      parent_id: int = 0, oca_group: str = "",
                      transmit: bool = True) -> Order:
        """
        Factory method for creating IB orders with proper defaults.
        
        Args:
            action: BUY or SELL
            quantity: Number of whole shares
            order_type: LMT, MKT, STP
            price: Limit price (for LMT orders)
            stop_price: Stop trigger price (for STP orders)
            tif: Time in force (DAY, GTC, IOC)
            outside_rth: Allow outside regular trading hours
            parent_id: Parent order ID for bracket orders
            oca_group: One-Cancels-All group name
            transmit: Whether to transmit immediately
            
        Returns:
            Configured Order object
        """
        order = Order()
        order.action = action
        order.totalQuantity = quantity
        order.orderType = order_type
        order.tif = tif
        order.outsideRth = outside_rth
        order.transmit = transmit
        
        # Price fields
        if order_type == "LMT":
            order.lmtPrice = round(price, 2)
        elif order_type == "STP":
            order.auxPrice = round(stop_price, 2)
        
        # Bracket/OCA configuration
        if parent_id > 0:
            order.parentId = parent_id
        if oca_group:
            order.ocaGroup = oca_group
            order.ocaType = 1  # Cancel remaining on fill
        
        # IB API bug fixes
        order.eTradeOnly = False
        order.firmQuoteOnly = False
        
        return order
    
    def _create_contract(self, symbol: str) -> Contract:
        """Create a stock contract."""
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        return contract
    
    def _get_stop_loss_pct(self, index: str) -> float:
        """Get stop loss percentage based on index."""
        return STOP_LOSS_PERCENTAGES.get(index.upper(), DEFAULT_STOP_LOSS)
    
    # ===== Live Price Fetching =====
    
    def get_live_price(self, symbol: str, timeout: float = 10.0) -> Optional[float]:
        """
        Fetch live price for a symbol from IB.
        
        Uses delayed market data if real-time not available.
        
        Args:
            symbol: Stock ticker symbol
            timeout: Maximum seconds to wait for price data
            
        Returns:
            Live price or None if unavailable
        """
        # Initialize tracking
        req_id = self._get_next_order_id()
        self.price_request_ids[req_id] = symbol
        self.price_ready[symbol] = threading.Event()
        self.live_prices[symbol] = 0
        
        # Create contract
        contract = self._create_contract(symbol)
        
        # Request market data (delayed if no subscription)
        # Generic tick types: 233=RTVolume for last price
        self.reqMktData(req_id, contract, "", False, False, [])
        
        # Wait for price data
        if self.price_ready[symbol].wait(timeout=timeout):
            price = self.live_prices.get(symbol, 0)
            # Cancel market data subscription
            self.cancelMktData(req_id)
            return price if price > 0 else None
        
        # Timeout - cancel and return None
        self.cancelMktData(req_id)
        return None
    
    def get_live_prices_batch(self, symbols: List[str], timeout: float = 15.0) -> Dict[str, float]:
        """
        Fetch live prices for multiple symbols concurrently.
        
        Args:
            symbols: List of stock ticker symbols
            timeout: Maximum seconds to wait for all prices
            
        Returns:
            Dict mapping symbols to their live prices (0 if unavailable)
        """
        results = {}
        req_ids = {}
        
        # Request all prices
        for symbol in symbols:
            req_id = self._get_next_order_id()
            req_ids[symbol] = req_id
            self.price_request_ids[req_id] = symbol
            self.price_ready[symbol] = threading.Event()
            self.live_prices[symbol] = 0
            
            contract = self._create_contract(symbol)
            self.reqMktData(req_id, contract, "", False, False, [])
            time.sleep(0.05)  # Small delay to avoid overwhelming IB
        
        # Wait for all prices (with timeout)
        start_time = time.time()
        for symbol in symbols:
            remaining_time = timeout - (time.time() - start_time)
            if remaining_time > 0:
                self.price_ready[symbol].wait(timeout=remaining_time)
            results[symbol] = self.live_prices.get(symbol, 0)
        
        # Cancel all subscriptions
        for symbol, req_id in req_ids.items():
            self.cancelMktData(req_id)
            
        return results
    
    # ===== Position Sizing =====
    
    def calculate_position_size(self, capital: float, price: float) -> tuple:
        """
        Calculate optimal whole share position size.
        
        Uses floor division to ensure we never exceed allocated capital.
        IB API does not support fractional shares, so whole shares only.
        
        Args:
            capital: Maximum capital to deploy for this position
            price: Entry price per share
            
        Returns:
            tuple: (shares: int, deployed_capital: float, unused_capital: float)
        """
        if price <= 0:
            return (0, 0.0, capital)
        
        shares = int(math.floor(capital / price))
        deployed = shares * price
        unused = capital - deployed
        
        return (shares, round(deployed, 2), round(unused, 2))
    
    # ===== Order Creation =====
    
    def create_limit_order(self, symbol: str, index: str, entry_price: float,
                          limit_sell_price: float, capital_per_position: float,
                          tif: str = "GTC") -> Tuple[List[int], dict]:
        """
        Create a limit buy order with bracket (profit target + stop loss).
        
        This is used for Monday evening - place limit at entry_price (live + 0.1%).
        Order will sit until filled or cancelled Tuesday morning.
        
        Args:
            symbol: Stock ticker
            index: Index membership (for stop loss calculation)
            entry_price: Limit price for entry (should be live_price * 1.001)
            limit_sell_price: Profit target price
            capital_per_position: Maximum capital to deploy
            tif: Time in force (GTC for overnight orders)
            
        Returns:
            Tuple of (order_ids list, tracking_info dict)
        """
        # Calculate position size (whole shares only)
        shares, deployed_capital, unused_capital = self.calculate_position_size(
            capital_per_position, entry_price
        )
        
        # Validate position
        if shares <= 0:
            print(f"\n  ⚠️ {symbol}: Insufficient capital for even 1 share @ ${entry_price:.2f}")
            return ([], {})
        
        # Calculate stop loss price
        stop_loss_pct = self._get_stop_loss_pct(index)
        stop_price = round(entry_price * (1 - stop_loss_pct), 2)
        
        # Create contract
        contract = self._create_contract(symbol)
        
        # Reserve order IDs
        entry_id = self._get_next_order_id()
        profit_id = self._get_next_order_id()
        stop_id = self._get_next_order_id()
        
        # OCA group links profit target and stop loss
        oca_group = f"BKT_{symbol}_{entry_id}"
        
        # === ENTRY ORDER (LIMIT) ===
        entry_order = self._create_order(
            action="BUY",
            quantity=shares,
            order_type="LMT",
            price=entry_price,
            tif=tif,  # GTC so it stays active overnight
            outside_rth=True,
            transmit=False  # Hold until all legs ready
        )
        entry_order.orderId = entry_id
        
        # === PROFIT TARGET ===
        profit_order = self._create_order(
            action="SELL",
            quantity=shares,
            order_type="LMT",
            price=limit_sell_price,
            tif="GTC",
            outside_rth=True,
            parent_id=entry_id,
            oca_group=oca_group,
            transmit=False
        )
        profit_order.orderId = profit_id
        
        # === STOP LOSS ===
        stop_order = self._create_order(
            action="SELL",
            quantity=shares,
            order_type="STP",
            stop_price=stop_price,
            tif="GTC",
            outside_rth=True,
            parent_id=entry_id,
            oca_group=oca_group,
            transmit=True  # Transmit entire bracket
        )
        stop_order.orderId = stop_id
        
        # Submit bracket to IB
        self.placeOrder(entry_id, contract, entry_order)
        self.placeOrder(profit_id, contract, profit_order)
        self.placeOrder(stop_id, contract, stop_order)
        
        # Track active bracket
        self.active_brackets[symbol] = [entry_id, profit_id, stop_id]
        
        # Build tracking info for JSON persistence
        tracking_info = {
            'symbol': symbol,
            'index': index,
            'entry_order_id': entry_id,
            'profit_order_id': profit_id,
            'stop_order_id': stop_id,
            'shares': shares,
            'entry_price': entry_price,
            'limit_sell': limit_sell_price,
            'stop_price': stop_price,
            'capital_deployed': deployed_capital,
            'order_type': 'LIMIT',
            'placed_at': datetime.now(CST_TIMEZONE).isoformat()
        }
        
        # Execution summary
        expected_profit = (limit_sell_price - entry_price) * shares
        expected_loss = (entry_price - stop_price) * shares
        risk_reward = expected_profit / expected_loss if expected_loss > 0 else 0
        
        print(f"\n  📊 {symbol} ({index}) - LIMIT ORDER:")
        print(f"     Shares:  {shares:,} @ ${entry_price:.2f} limit = ${deployed_capital:,.2f}")
        print(f"     Target:  ${limit_sell_price:.2f} (+${expected_profit:,.2f})")
        print(f"     Stop:    ${stop_price:.2f} (-${expected_loss:,.2f}) [{stop_loss_pct*100:.1f}%]")
        print(f"     R:R:     {risk_reward:.2f}")
        if unused_capital > 0:
            print(f"     Unused:  ${unused_capital:.2f}")
        
        return ([entry_id, profit_id, stop_id], tracking_info)
    
    def create_market_order(self, symbol: str, index: str, limit_sell_price: float,
                           capital_per_position: float, current_price: float = 0) -> Tuple[List[int], dict]:
        """
        Create a market buy order with bracket (profit target + stop loss).
        
        This is used Tuesday morning for orders that didn't fill overnight.
        
        Args:
            symbol: Stock ticker
            index: Index membership (for stop loss calculation)
            limit_sell_price: Profit target price
            capital_per_position: Maximum capital to deploy
            current_price: Current market price (for position sizing, uses limit_sell if 0)
            
        Returns:
            Tuple of (order_ids list, tracking_info dict)
        """
        # Use limit_sell as reference if no current price (conservative sizing)
        reference_price = current_price if current_price > 0 else limit_sell_price * 0.99
        
        # Calculate position size (whole shares only)
        shares, deployed_capital, unused_capital = self.calculate_position_size(
            capital_per_position, reference_price
        )
        
        # Validate position
        if shares <= 0:
            print(f"\n  ⚠️ {symbol}: Insufficient capital for even 1 share @ ${reference_price:.2f}")
            return ([], {})
        
        # Calculate stop loss price (based on reference price)
        stop_loss_pct = self._get_stop_loss_pct(index)
        stop_price = round(reference_price * (1 - stop_loss_pct), 2)
        
        # Create contract
        contract = self._create_contract(symbol)
        
        # Reserve order IDs
        entry_id = self._get_next_order_id()
        profit_id = self._get_next_order_id()
        stop_id = self._get_next_order_id()
        
        # OCA group links profit target and stop loss
        oca_group = f"BKT_{symbol}_{entry_id}"
        
        # === ENTRY ORDER (MARKET) ===
        entry_order = self._create_order(
            action="BUY",
            quantity=shares,
            order_type="MKT",
            tif="DAY",
            outside_rth=True,
            transmit=False  # Hold until all legs ready
        )
        entry_order.orderId = entry_id
        
        # === PROFIT TARGET ===
        profit_order = self._create_order(
            action="SELL",
            quantity=shares,
            order_type="LMT",
            price=limit_sell_price,
            tif="GTC",
            outside_rth=True,
            parent_id=entry_id,
            oca_group=oca_group,
            transmit=False
        )
        profit_order.orderId = profit_id
        
        # === STOP LOSS ===
        stop_order = self._create_order(
            action="SELL",
            quantity=shares,
            order_type="STP",
            stop_price=stop_price,
            tif="GTC",
            outside_rth=True,
            parent_id=entry_id,
            oca_group=oca_group,
            transmit=True  # Transmit entire bracket
        )
        stop_order.orderId = stop_id
        
        # Submit bracket to IB
        self.placeOrder(entry_id, contract, entry_order)
        self.placeOrder(profit_id, contract, profit_order)
        self.placeOrder(stop_id, contract, stop_order)
        
        # Track active bracket
        self.active_brackets[symbol] = [entry_id, profit_id, stop_id]
        
        # Build tracking info
        tracking_info = {
            'symbol': symbol,
            'index': index,
            'entry_order_id': entry_id,
            'profit_order_id': profit_id,
            'stop_order_id': stop_id,
            'shares': shares,
            'entry_price': reference_price,  # Estimated
            'limit_sell': limit_sell_price,
            'stop_price': stop_price,
            'capital_deployed': deployed_capital,
            'order_type': 'MARKET',
            'placed_at': datetime.now(CST_TIMEZONE).isoformat()
        }
        
        # Execution summary
        expected_profit = (limit_sell_price - reference_price) * shares
        expected_loss = (reference_price - stop_price) * shares
        
        print(f"\n  📊 {symbol} ({index}) - MARKET ORDER:")
        print(f"     Shares:  {shares:,} @ MKT (~${reference_price:.2f}) = ~${deployed_capital:,.2f}")
        print(f"     Target:  ${limit_sell_price:.2f} (+~${expected_profit:,.2f})")
        print(f"     Stop:    ${stop_price:.2f} (-~${expected_loss:,.2f}) [{stop_loss_pct*100:.1f}%]")
        if unused_capital > 0:
            print(f"     Unused:  ${unused_capital:.2f}")
        
        return ([entry_id, profit_id, stop_id], tracking_info)
    
    def create_bracket_order(self, symbol: str, index: str, entry_price: float,
                            limit_sell_price: float, capital_per_position: float) -> List[int]:
        """
        Create a bracket order (entry + profit target + stop loss).
        
        LEGACY METHOD - kept for backward compatibility.
        For new code, use create_limit_order() or create_market_order().
        
        All legs use identical whole share quantities for clean execution.
        OCA group ensures profit target and stop loss cancel each other.
        
        Args:
            symbol: Stock ticker
            index: Index membership (for stop loss calculation)
            entry_price: Limit price for entry
            limit_sell_price: Profit target price
            capital_per_position: Maximum capital to deploy
            
        Returns:
            List of order IDs: [entry_id, profit_id, stop_id]
        """
        order_ids, _ = self.create_limit_order(
            symbol=symbol,
            index=index,
            entry_price=entry_price,
            limit_sell_price=limit_sell_price,
            capital_per_position=capital_per_position,
            tif="DAY"  # Legacy behavior
        )
        return order_ids
    
    # ===== Order Status Checking =====
    
    def get_order_status(self, order_id: int) -> dict:
        """
        Get the current status of an order.
        
        Args:
            order_id: IB order ID
            
        Returns:
            Dict with status, filled, remaining quantities
        """
        # Request fresh order status
        self.reqOpenOrders()
        time.sleep(1)  # Wait for response
        
        return self.order_status_map.get(order_id, {
            'status': 'Unknown',
            'filled': 0,
            'remaining': 0
        })
    
    def check_order_fill_status(self, order_ids: List[int]) -> Dict[int, dict]:
        """
        Check fill status for multiple orders.
        
        Args:
            order_ids: List of order IDs to check
            
        Returns:
            Dict mapping order_id to status info
        """
        # Clear and refresh order status
        self.reqOpenOrders()
        time.sleep(2)  # Wait for all responses
        
        results = {}
        for order_id in order_ids:
            results[order_id] = self.order_status_map.get(order_id, {
                'status': 'Unknown',
                'filled': 0,
                'remaining': 0
            })
        
        return results
    
    def cancel_order(self, order_id: int) -> bool:
        """
        Cancel a specific order.
        
        Args:
            order_id: IB order ID to cancel
            
        Returns:
            True if cancel request was sent
        """
        try:
            self.cancelOrder(order_id, "")  # Empty string for manualCancelOrderTime
            print(f"  ↩️ Cancel request sent for order {order_id}")
            return True
        except Exception as e:
            print(f"  ⚠️ Failed to cancel order {order_id}: {e}")
            return False
    
    def handle_partial_fill(self, symbol: str, tracking_info: dict, 
                           capital_per_position: float) -> Tuple[List[int], dict]:
        """
        Handle partially filled order - cancel remaining and place market order.
        
        Edge Case #7: If order is partially filled by Tuesday morning,
        cancel the unfilled portion and place a market order for remaining shares.
        
        Args:
            symbol: Stock ticker
            tracking_info: Order tracking info from Monday
            capital_per_position: Original capital allocation
            
        Returns:
            Tuple of (new order_ids, updated tracking_info)
        """
        entry_order_id = tracking_info.get('entry_order_id')
        original_shares = tracking_info.get('shares', 0)
        
        # Get current status
        status = self.get_order_status(entry_order_id)
        filled = status.get('filled', 0)
        remaining = status.get('remaining', 0)
        
        if filled == 0:
            # Nothing filled - just cancel and place new market order
            print(f"  📊 {symbol}: No fills - placing full market order")
            self.cancel_order(entry_order_id)
            time.sleep(1)
            return self.create_market_order(
                symbol=symbol,
                index=tracking_info.get('index', 'SPY'),
                limit_sell_price=tracking_info.get('limit_sell', 0),
                capital_per_position=capital_per_position
            )
        
        elif remaining > 0:
            # Partial fill - cancel remaining, keep filled portion
            print(f"  📊 {symbol}: Partially filled ({filled}/{original_shares} shares)")
            print(f"     Cancelling remaining {remaining} shares, placing market order")
            
            # Cancel the unfilled portion
            self.cancel_order(entry_order_id)
            time.sleep(1)
            
            # Calculate remaining capital for market order
            filled_capital = filled * tracking_info.get('entry_price', 0)
            remaining_capital = capital_per_position - filled_capital
            
            if remaining_capital > 0 and remaining > 0:
                # Place market order for unfilled shares
                return self.create_market_order(
                    symbol=symbol,
                    index=tracking_info.get('index', 'SPY'),
                    limit_sell_price=tracking_info.get('limit_sell', 0),
                    capital_per_position=remaining_capital
                )
            
        # Fully filled - no action needed
        print(f"  ✅ {symbol}: Fully filled ({filled} shares)")
        return ([], tracking_info)
    
    # ===== Position Management =====
    
    def request_positions(self):
        """Request current positions from IB."""
        self.positions.clear()
        self.reqPositions()
        time.sleep(2)  # Wait for position data
        
    def cancel_all_orders(self):
        """Cancel all open orders."""
        self.reqGlobalCancel()
        print("✓ Cancelled all open orders")
        time.sleep(1)
        
    def sell_all_positions(self) -> int:
        """
        Sell all current positions at market.
        
        Returns:
            Number of sell orders placed
        """
        self.request_positions()
        
        if not self.positions:
            print("No positions to sell")
            return 0
            
        # First cancel any existing orders
        self.cancel_all_orders()
        
        sell_count = 0
        print(f"\n{'='*60}")
        print(f"SELLING ALL POSITIONS")
        print(f"{'='*60}")
        
        for symbol, quantity in self.positions.items():
            if quantity <= 0:
                continue
            
            shares = int(abs(quantity))
            contract = self._create_contract(symbol)
            
            order = self._create_order(
                action="SELL",
                quantity=shares,
                order_type="MKT",
                tif="DAY",
                outside_rth=True
            )
            
            order_id = self._get_next_order_id()
            self.placeOrder(order_id, contract, order)
            
            print(f"  📤 SELL {shares:,} {symbol} @ MKT")
            sell_count += 1
            time.sleep(0.1)
            
        print(f"\n✓ Placed {sell_count} sell orders")
        return sell_count
    
    # ===== Main Execution =====
    
    def connect_and_run(self, host: str = "127.0.0.1", port: int = PAPER_TRADING_PORT,
                       max_retries: int = MAX_CONNECTION_RETRIES) -> bool:
        """
        Connect to IB with retry logic (Edge Case #9).
        
        Args:
            host: IB Gateway host
            port: IB Gateway port
            max_retries: Maximum connection attempts
            
        Returns:
            True if connected successfully
        """
        for attempt in range(1, max_retries + 1):
            try:
                print(f"  🔌 Connection attempt {attempt}/{max_retries}...")
                self.connect(host, port, CLIENT_ID)
                
                # Start message processing thread
                api_thread = threading.Thread(target=self.run, daemon=True)
                api_thread.start()
                
                # Wait for connection
                if self.connected.wait(timeout=15):
                    print(f"✓ Connected to IB on port {port}")
                    self._connection_lost.clear()
                    return True
                else:
                    print(f"  ⚠️ Attempt {attempt}: Connection timeout")
                    self.disconnect()
                    
            except Exception as e:
                print(f"  ⚠️ Attempt {attempt}: {e}")
                
            if attempt < max_retries:
                delay = RETRY_DELAY_SECONDS * (2 ** (attempt - 1))  # Exponential backoff
                print(f"  ⏳ Retrying in {delay} seconds...")
                time.sleep(delay)
        
        raise ConnectionError(f"Failed to connect to IB Gateway after {max_retries} attempts")
    
    def reconnect(self, host: str = "127.0.0.1", port: int = PAPER_TRADING_PORT) -> bool:
        """
        Reconnect to IB Gateway (Edge Case #10).
        
        Called when connection is lost during operation.
        
        Returns:
            True if reconnected successfully
        """
        if self._reconnecting:
            return False
            
        self._reconnecting = True
        print("\n⚠️ Attempting to reconnect to IB Gateway...")
        
        try:
            self.disconnect()
            time.sleep(2)
            return self.connect_and_run(host, port)
        except Exception as e:
            print(f"❌ Reconnection failed: {e}")
            return False
        finally:
            self._reconnecting = False
    
    def ensure_connected(self, port: int = PAPER_TRADING_PORT) -> bool:
        """
        Ensure we have an active connection, reconnecting if necessary.
        
        Returns:
            True if connected (or reconnected successfully)
        """
        if self.connected.is_set() and not self._connection_lost.is_set():
            return True
        
        return self.reconnect(port=port)
        
    def get_account_info(self):
        """Request account information."""
        self.reqAccountSummary(9001, "All", "NetLiquidation")
        
        if not self.account_ready.wait(timeout=10):
            print("Warning: Account summary timeout, using provided capital")
            return
            
        print(f"✓ Account Value: ${self.account_value:,.2f}")
    
    def execute_monday_limit_orders(self, signals: List[dict], max_positions: int,
                                    total_capital: float,
                                    exclude_tickers: set = None,
                                    existing_tracking: Dict[str, dict] = None) -> Dict[str, dict]:
        """
        Execute Monday evening limit orders.
        
        Places limit orders at live_price * 1.001 (0.1% above market).
        Orders persist overnight as GTC.
        Saves incrementally after each order to survive mid-batch crashes.
        
        Edge Cases Handled:
        - #1: Skip if live_price >= limit_sell (overnight gap up risk)
        - #5: Skip if already holding position
        - #11: Skip if ticker already has a pending limit order (dedup)
        - #12: Incremental save — each order is persisted immediately
        
        Args:
            signals: List of signal dictionaries from trading bot
            max_positions: Maximum number of positions to open
            total_capital: Total capital to allocate (from IB account)
            exclude_tickers: Set of tickers to skip (already have pending orders)
            existing_tracking: Existing pending orders dict (for incremental merge+save)
            
        Returns:
            Dict mapping symbols to their NEW tracking info (does not include existing)
        """
        if exclude_tickers is None:
            exclude_tickers = set()
        if existing_tracking is None:
            existing_tracking = {}
        # Calculate capital per position
        capital_per_position = total_capital / max_positions
        
        print(f"\n{'='*60}")
        print(f"MONDAY LIMIT ORDER EXECUTION")
        print(f"{'='*60}")
        print(f"Total Capital: ${total_capital:,.2f} (from IB)")
        print(f"Max Positions: {max_positions}")
        print(f"Per Position:  ${capital_per_position:,.2f}")
        print(f"Limit Premium: {LIMIT_PREMIUM_PCT*100:.1f}% above live price")
        print(f"{'='*60}")
        sys.stdout.flush()
        
        # Get current positions (Edge Case #5)
        self.request_positions()
        held_symbols = set(sym for sym, qty in self.positions.items() if qty > 0)
        if held_symbols:
            print(f"\n⚠️ Already holding: {', '.join(held_symbols)}")
        
        # Filter and sort signals (prioritize by predicted return)
        valid_signals = [s for s in signals if s.get('direction', '').upper() == 'UP']
        valid_signals.sort(key=lambda x: x.get('predicted_return', 0), reverse=True)
        
        # Take top N signals
        signals_to_execute = valid_signals[:max_positions]
        
        if not signals_to_execute:
            print("No valid BUY signals to execute")
            return {}
        
        # Get live prices for all symbols
        symbols = [s.get('ticker') for s in signals_to_execute if s.get('ticker')]
        print(f"\n📡 Fetching live prices for {len(symbols)} symbols...")
        sys.stdout.flush()
        
        live_prices = self.get_live_prices_batch(symbols)
        
        print(f"\nExecuting {len(signals_to_execute)} limit orders:")
        
        tracking_map = {}
        skipped = []
        
        for signal in signals_to_execute:
            ticker = signal.get('ticker', '')
            index = signal.get('index', 'SPY')
            last_close = signal.get('last_close', 0)
            limit_sell = signal.get('limit_sell', 0)
            
            if not ticker or last_close <= 0 or limit_sell <= 0:
                print(f"  ⚠️ Skipping invalid signal: {signal}")
                skipped.append((ticker, "invalid signal data"))
                continue
            
            # Edge Case #11: Skip if already has pending limit order (dedup on retry)
            if ticker in exclude_tickers:
                print(f"  ⚠️ Skipping {ticker}: Already has pending limit order")
                skipped.append((ticker, "pending limit order exists"))
                continue
            
            # Edge Case #5: Skip if already holding
            if ticker in held_symbols:
                print(f"  ⚠️ Skipping {ticker}: Already holding position")
                skipped.append((ticker, "already holding"))
                continue
            
            # Get live price (fallback to last_close)
            live_price = live_prices.get(ticker, 0)
            if live_price <= 0:
                print(f"  ⚠️ {ticker}: No live price, using last_close ${last_close:.2f}")
                live_price = last_close
            else:
                print(f"  📊 {ticker}: Live price ${live_price:.2f} (close: ${last_close:.2f})")
            
            # Edge Case #1: Skip if live_price >= limit_sell (gap up overnight)
            if live_price >= limit_sell:
                print(f"  ⚠️ Skipping {ticker}: Live price ${live_price:.2f} >= limit_sell ${limit_sell:.2f}")
                skipped.append((ticker, f"live price exceeds target"))
                continue
            
            # Calculate limit entry price (0.1% above live price)
            entry_price = round(live_price * (1 + LIMIT_PREMIUM_PCT), 2)
            
            # Double-check entry doesn't exceed limit_sell
            if entry_price >= limit_sell:
                entry_price = round(limit_sell * 0.995, 2)  # 0.5% below target
                print(f"  ⚠️ {ticker}: Adjusted entry to ${entry_price:.2f} (below target)")
            
            order_ids, tracking_info = self.create_limit_order(
                symbol=ticker,
                index=index,
                entry_price=entry_price,
                limit_sell_price=limit_sell,
                capital_per_position=capital_per_position,
                tif="GTC"  # Overnight persistence
            )
            
            if order_ids:
                tracking_info['live_price'] = live_price
                tracking_info['last_close'] = last_close
                tracking_map[ticker] = tracking_info
                
                # Incremental save (Edge Case #12): persist immediately so a
                # mid-batch IB crash doesn't lose already-placed orders.
                all_tracking = {**existing_tracking, **tracking_map}
                save_pending_orders(all_tracking)
            
            time.sleep(0.1)  # Small delay between orders
        
        # Summary
        print(f"\n{'='*60}")
        print(f"MONDAY SUMMARY")
        print(f"{'='*60}")
        print(f"Limit orders placed: {len(tracking_map)}")
        if skipped:
            print(f"Skipped: {len(skipped)}")
            for sym, reason in skipped:
                print(f"  - {sym}: {reason}")
        print(f"{'='*60}")
        sys.stdout.flush()
        
        return tracking_map
    
    def execute_tuesday_market_fallback(self, pending_orders: Dict[str, dict],
                                        total_capital: float) -> Dict[str, dict]:
        """
        Execute Tuesday morning market order fallback.
        
        Checks Monday's limit orders:
        - If filled: Keep position, do nothing
        - If partially filled: Cancel remaining, place market for rest (Edge Case #7)
        - If unfilled: Cancel and place market order
        
        Args:
            pending_orders: Tracking info from Monday's limit orders
            total_capital: Total capital to allocate (for market orders)
            
        Returns:
            Dict of final order tracking info
        """
        print(f"\n{'='*60}")
        print(f"TUESDAY MARKET ORDER FALLBACK")
        print(f"{'='*60}")
        print(f"Checking {len(pending_orders)} pending orders from Monday...")
        sys.stdout.flush()
        
        capital_per_position = total_capital / MAX_POSITIONS
        final_tracking = {}
        
        # P0-FIX: Query current positions ONCE before processing orders.
        # reqOpenOrders() only returns OPEN orders — filled orders vanish from
        # the result set, causing get_order_status() to return 'Unknown'.
        # By checking actual held positions we detect overnight fills and avoid
        # placing duplicate market orders.
        self.request_positions()
        held_symbols = set(sym for sym, qty in self.positions.items() if qty > 0)
        if held_symbols:
            print(f"\n  📊 Currently held positions: {', '.join(sorted(held_symbols))}")
        else:
            print(f"\n  📊 No existing positions detected")
        sys.stdout.flush()
        
        for symbol, tracking_info in pending_orders.items():
            entry_order_id = tracking_info.get('entry_order_id')
            original_shares = tracking_info.get('shares', 0)
            
            # Get current order status
            status_info = self.get_order_status(entry_order_id)
            status = status_info.get('status', 'Unknown')
            filled = status_info.get('filled', 0)
            remaining = status_info.get('remaining', original_shares)
            
            # P0-FIX: If status is Unknown but we already hold this symbol,
            # the limit order filled overnight and disappeared from reqOpenOrders.
            # Treat as filled to prevent duplicate market order.
            if status == 'Unknown' and symbol in held_symbols:
                held_qty = int(abs(self.positions.get(symbol, 0)))
                print(f"\n  {symbol}: Status=Unknown but HOLDING {held_qty} shares — treating as filled")
                print(f"  ✅ {symbol}: Overnight fill detected via position check")
                tracking_info['final_status'] = 'filled_limit'
                tracking_info['detected_via'] = 'position_check'
                tracking_info['held_quantity'] = held_qty
                final_tracking[symbol] = tracking_info
                continue
            
            print(f"\n  {symbol}: Status={status}, Filled={filled}/{original_shares}")
            
            if status == "Filled" or filled == original_shares:
                # Fully filled - keep as is
                print(f"  ✅ {symbol}: Fully filled - keeping position")
                tracking_info['final_status'] = 'filled_limit'
                final_tracking[symbol] = tracking_info
                
            elif filled > 0 and remaining > 0:
                # Edge Case #7: Partial fill
                print(f"  ⚠️ {symbol}: Partial fill - handling remaining {remaining} shares")
                new_ids, new_tracking = self.handle_partial_fill(
                    symbol, tracking_info, capital_per_position
                )
                if new_ids:
                    new_tracking['original_filled'] = filled
                    new_tracking['final_status'] = 'partial_then_market'
                    final_tracking[symbol] = new_tracking
                else:
                    tracking_info['final_status'] = 'partial_kept'
                    final_tracking[symbol] = tracking_info
                    
            else:
                # Unfilled - cancel and place market order
                print(f"  ↩️ {symbol}: Unfilled - cancelling and placing market order")
                self.cancel_order(entry_order_id)
                time.sleep(1)
                
                # Check live price doesn't exceed target (Edge Case #1 for Tuesday)
                live_price = self.get_live_price(symbol, timeout=5)
                limit_sell = tracking_info.get('limit_sell', 0)
                
                if live_price and live_price >= limit_sell:
                    print(f"  ⚠️ {symbol}: Market price ${live_price:.2f} >= target ${limit_sell:.2f} - skipping")
                    tracking_info['final_status'] = 'skipped_gap_up'
                    final_tracking[symbol] = tracking_info
                    continue
                
                new_ids, new_tracking = self.create_market_order(
                    symbol=symbol,
                    index=tracking_info.get('index', 'SPY'),
                    limit_sell_price=limit_sell,
                    capital_per_position=capital_per_position,
                    current_price=live_price or tracking_info.get('last_close', 0)
                )
                
                if new_ids:
                    new_tracking['original_limit_price'] = tracking_info.get('entry_price')
                    new_tracking['final_status'] = 'converted_to_market'
                    final_tracking[symbol] = new_tracking
            
            time.sleep(0.1)
        
        # Summary
        print(f"\n{'='*60}")
        print(f"TUESDAY SUMMARY")
        print(f"{'='*60}")
        filled_limit = sum(1 for t in final_tracking.values() if t.get('final_status') == 'filled_limit')
        converted = sum(1 for t in final_tracking.values() if t.get('final_status') == 'converted_to_market')
        partial = sum(1 for t in final_tracking.values() if 'partial' in t.get('final_status', ''))
        skipped = sum(1 for t in final_tracking.values() if t.get('final_status') == 'skipped_gap_up')
        
        print(f"Filled via limit (Monday): {filled_limit}")
        print(f"Converted to market (Tuesday): {converted}")
        print(f"Partial fills handled: {partial}")
        print(f"Skipped (gap up): {skipped}")
        print(f"{'='*60}")
        sys.stdout.flush()
        
        return final_tracking
        
    def execute_signals(self, signals: List[dict], max_positions: int, 
                       total_capital: float) -> Dict[str, List[int]]:
        """
        Execute trading signals from the bot.
        
        Args:
            signals: List of signal dictionaries from trading bot
            max_positions: Maximum number of positions to open
            total_capital: Total capital to allocate
            
        Returns:
            Dict mapping symbols to their order IDs
        """
        # Calculate capital per position
        capital_per_position = total_capital / max_positions
        
        print(f"\n{'='*60}")
        print(f"EXECUTING TRADES")
        print(f"{'='*60}")
        print(f"Total Capital: ${total_capital:,.2f}")
        print(f"Max Positions: {max_positions}")
        print(f"Per Position:  ${capital_per_position:,.2f}")
        print(f"{'='*60}")
        
        # Filter and sort signals (prioritize by predicted return)
        valid_signals = [s for s in signals if s.get('direction', '').upper() == 'UP']
        valid_signals.sort(key=lambda x: x.get('predicted_return', 0), reverse=True)
        
        # Take top N signals
        signals_to_execute = valid_signals[:max_positions]
        
        if not signals_to_execute:
            print("No valid BUY signals to execute")
            return {}
            
        print(f"\nExecuting {len(signals_to_execute)} positions:")
        
        order_map = {}
        
        for signal in signals_to_execute:
            ticker = signal.get('ticker', '')
            index = signal.get('index', 'SPY')
            entry_price = signal.get('last_close', 0)
            limit_sell = signal.get('limit_sell', 0)
            
            if not ticker or entry_price <= 0 or limit_sell <= 0:
                print(f"  ⚠ Skipping invalid signal: {signal}")
                continue
                
            order_ids = self.create_bracket_order(
                symbol=ticker,
                index=index,
                entry_price=entry_price,
                limit_sell_price=limit_sell,
                capital_per_position=capital_per_position
            )
            
            order_map[ticker] = order_ids
            time.sleep(0.1)  # Small delay between orders
            
        return order_map


def preflight_checks() -> bool:
    """
    Run pre-flight system checks before trading.
    Returns True if all checks pass, False otherwise.
    """
    print(f"\n{'='*60}")
    print("PRE-FLIGHT SYSTEM CHECKS")
    print(f"{'='*60}")
    sys.stdout.flush()
    
    all_passed = True
    
    # Check 1: IB Gateway port is listening
    sock = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', PAPER_TRADING_PORT))
    sock.close()
    if result == 0:
        print(f"✓ IB Gateway port {PAPER_TRADING_PORT} is listening")
    else:
        print(f"❌ IB Gateway port {PAPER_TRADING_PORT} is NOT listening")
        all_passed = False
    
    # Check 2: Signals directory exists
    script_dir = os.path.dirname(os.path.abspath(__file__))
    signals_dir = os.path.join(script_dir, "signals")
    if os.path.exists(signals_dir):
        print(f"✓ Signals directory exists: {signals_dir}")
    else:
        print(f"⚠ Signals directory missing, creating: {signals_dir}")
        os.makedirs(signals_dir, exist_ok=True)
    
    # Check 3: Signals file status
    signals_path = os.path.join(script_dir, SIGNALS_FILE)
    if os.path.exists(signals_path):
        file_age_hours = (time.time() - os.path.getmtime(signals_path)) / 3600
        print(f"✓ Signals file exists (age: {file_age_hours:.1f} hours)")
        if file_age_hours > 48:
            print(f"⚠ WARNING: Signals file is {file_age_hours:.0f} hours old")
    else:
        print(f"⚠ No signals file yet at: {signals_path}")
        print(f"   Signal generation runs Monday {SIGNAL_GEN_HOUR}:{SIGNAL_GEN_MINUTE:02d} PM CST, orders at {LIMIT_ORDER_HOUR}:{LIMIT_ORDER_MINUTE:02d} CST")
    
    # Check 4: Memory available
    try:
        with open('/proc/meminfo', 'r') as f:
            meminfo = f.read()
            for line in meminfo.split('\n'):
                if 'MemAvailable' in line:
                    mem_kb = int(line.split()[1])
                    mem_gb = mem_kb / 1024 / 1024
                    if mem_gb < 0.5:
                        print(f"⚠ Low memory: {mem_gb:.2f} GB available")
                    else:
                        print(f"✓ Memory available: {mem_gb:.2f} GB")
                    break
    except:
        print("⚠ Could not check memory (non-Linux system)")
    
    print(f"{'='*60}")
    print(f"Pre-flight: {'PASSED' if all_passed else 'FAILED'}")
    print(f"{'='*60}\n")
    sys.stdout.flush()
    
    return all_passed


def load_signals(filepath: str) -> List[dict]:
    """Load signals from JSON file."""
    with open(filepath, 'r') as f:
        data = json.load(f)
    
    # Handle the trading bot's output format
    if isinstance(data, dict):
        if 'all_signals' in data:
            return data['all_signals']
        elif 'signals' in data:
            return data['signals']
        else:
            return [data]
    elif isinstance(data, list):
        return data
    else:
        return []


def get_cst_now() -> datetime:
    """Get current time in CST."""
    return datetime.now(CST_TIMEZONE)


def validate_signals_file(filepath: str, max_age_hours: int = 12) -> Tuple[bool, str]:
    """
    Validate that signals file exists and is fresh (Citadel-grade reliability).
    
    Args:
        filepath: Path to signals JSON file
        max_age_hours: Maximum acceptable age in hours (default 12)
        
    Returns:
        (is_valid, reason) tuple
        - is_valid: True if file exists and is fresh
        - reason: Human-readable explanation if invalid
    """
    if not os.path.exists(filepath):
        return False, "File does not exist"
    
    try:
        # Check file modification time
        file_mtime = os.path.getmtime(filepath)
        file_age_seconds = time.time() - file_mtime
        file_age_hours = file_age_seconds / 3600
        
        if file_age_hours > max_age_hours:
            file_time = datetime.fromtimestamp(file_mtime, tz=CST_TIMEZONE)
            return False, f"File too old ({file_age_hours:.1f}h, updated {file_time.strftime('%Y-%m-%d %H:%M:%S')} CST)"
        
        # Check file is not empty and has valid JSON
        with open(filepath, 'r') as f:
            data = json.load(f)
        
        # Check for expected structure
        if isinstance(data, dict):
            if 'all_signals' in data or 'signals' in data:
                signal_count = len(data.get('all_signals', data.get('signals', [])))
            elif 'timestamp' in data:
                signal_count = data.get('total_signals', 0)
            else:
                return False, "Invalid JSON structure (missing signals)"
        elif isinstance(data, list):
            signal_count = len(data)
        else:
            return False, "Invalid JSON structure"
        
        file_time = datetime.fromtimestamp(file_mtime, tz=CST_TIMEZONE)
        return True, f"Valid ({signal_count} signals, age {file_age_hours:.1f}h, updated {file_time.strftime('%H:%M:%S')} CST)"
        
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"
    except Exception as e:
        return False, f"Read error: {e}"


def save_pending_orders(tracking_map: Dict[str, dict], filepath: str = None):
    """
    Save pending order tracking info to JSON file.
    
    This persists Monday's orders so Tuesday can check their status.
    
    Args:
        tracking_map: Dict mapping symbols to tracking info
        filepath: Path to save file (default: signals/pending_orders.json)
    """
    if filepath is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        filepath = os.path.join(script_dir, ORDER_TRACKING_FILE)
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    data = {
        'created_at': get_cst_now().isoformat(),
        'orders': tracking_map
    }
    
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2, default=str)
    
    print(f"✓ Saved {len(tracking_map)} pending orders to {filepath}")


def load_pending_orders(filepath: str = None) -> Dict[str, dict]:
    """
    Load pending order tracking info from JSON file.
    
    Args:
        filepath: Path to load file (default: signals/pending_orders.json)
        
    Returns:
        Dict mapping symbols to tracking info
    """
    if filepath is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        filepath = os.path.join(script_dir, ORDER_TRACKING_FILE)
    
    if not os.path.exists(filepath):
        print(f"⚠️ No pending orders file found at {filepath}")
        return {}
    
    with open(filepath, 'r') as f:
        data = json.load(f)
    
    orders = data.get('orders', {})
    created_at = data.get('created_at', 'unknown')
    
    print(f"✓ Loaded {len(orders)} pending orders (created: {created_at})")
    return orders


def clear_pending_orders(filepath: str = None):
    """
    Clear/delete the pending orders file after Tuesday execution.
    
    Args:
        filepath: Path to file (default: signals/pending_orders.json)
    """
    if filepath is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        filepath = os.path.join(script_dir, ORDER_TRACKING_FILE)
    
    if os.path.exists(filepath):
        os.remove(filepath)
        print(f"✓ Cleared pending orders file")


def seconds_until(target_hour: int, target_minute: int = 0) -> float:
    """Calculate seconds until target time today in CST."""
    now = get_cst_now()
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    
    if target <= now:
        # Target time already passed today
        return -1
    
    return (target - now).total_seconds()


def run_trading_bot() -> str:
    """
    Run trading_bot.py to generate fresh signals.
    
    Returns:
        Path to the generated signals JSON file (always current_signals.json)
    """
    print(f"\n{'='*60}")
    print("RUNNING TRADING BOT")
    print(f"{'='*60}")
    sys.stdout.flush()
    
    # Get the directory where trade_executor.py is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    trading_bot_path = os.path.join(script_dir, "trading_bot.py")
    signals_path = os.path.join(script_dir, SIGNALS_FILE)
    
    # Verify trading_bot.py exists
    if not os.path.exists(trading_bot_path):
        print(f"❌ ERROR: trading_bot.py not found at {trading_bot_path}")
        sys.stdout.flush()
        return None
    
    # Run trading_bot.py with --indexes flag to analyze all indexes automatically
    print(f"Running: python {trading_bot_path} --indexes SPY NASDAQ SP400 SPSM")
    print(f"Working directory: {script_dir}")
    sys.stdout.flush()
    
    try:
        result = subprocess.run(
            [sys.executable, trading_bot_path, "--indexes", "SPY", "NASDAQ", "SP400", "SPSM"],
            cwd=script_dir,
            capture_output=True,
            text=True,
            timeout=10800  # 3 hour timeout for signal generation
        )
        
        if result.returncode != 0:
            print(f"❌ Error running trading bot (exit code {result.returncode}):")
            print(f"STDERR: {result.stderr[:2000] if result.stderr else 'None'}")
            print(f"STDOUT: {result.stdout[:2000] if result.stdout else 'None'}")
            sys.stdout.flush()
            return None
        
        print(f"Trading bot completed successfully")
        
    except subprocess.TimeoutExpired:
        print(f"❌ ERROR: Trading bot timed out after 3 hours")
        sys.stdout.flush()
        return None
    except Exception as e:
        print(f"❌ ERROR running trading bot: {e}")
        sys.stdout.flush()
        return None
    
    # Check if the fixed signals file exists
    if not os.path.exists(signals_path):
        print(f"❌ Signals file not found: {signals_path}")
        # List what's in the signals directory
        signals_dir = os.path.dirname(signals_path)
        if os.path.exists(signals_dir):
            print(f"Contents of {signals_dir}: {os.listdir(signals_dir)}")
        sys.stdout.flush()
        return None
    
    # Verify signals file has content
    file_size = os.path.getsize(signals_path)
    print(f"✓ Signals generated: {signals_path} ({file_size} bytes)")
    sys.stdout.flush()
    return signals_path


def run_scheduled_cycle(port: int = PAPER_TRADING_PORT):
    """
    Run the triple-schedule trading cycle v3.0:
    - Monday 4:00 PM CST:  Generate trading signals (run predictor)
    - Monday 4:30 PM CST:  Place LIMIT orders at live_price + 0.1%
                            Retries every 30 min until all orders placed (deadline 10 PM)
    - Tuesday 8:00 AM CST: Check fills, cancel unfilled → replace with MARKET orders
    
    Edge Cases Handled:
    - #1:  Skip if live_price >= limit_sell (gap up)
    - #5:  Skip if already holding position
    - #7:  Handle partial fills
    - #8:  Check for market holidays
    - #9:  Retry connection up to 3 times
    - #10: Handle IB Gateway disconnection
    - #11: Dedup — skip tickers that already have pending limit orders on retry
    
    Args:
        port: IB port (4002 paper, 4001 live for IB Gateway)
    """
    day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    print(f"\n{'='*60}")
    print("TRIPLE-SCHEDULE TRADING MODE v3.0")
    print(f"{'='*60}")
    print(f"Phase 0: {day_names[SIGNAL_GEN_DAY]} {SIGNAL_GEN_HOUR}:{SIGNAL_GEN_MINUTE:02d} CST")
    print(f"         → Generate trading signals (run predictor)")
    print(f"Phase 1: {day_names[LIMIT_ORDER_DAY]} {LIMIT_ORDER_HOUR}:{LIMIT_ORDER_MINUTE:02d} CST")
    print(f"         → Place LIMIT orders at live_price + 0.1%")
    print(f"         → Retry every {LIMIT_ORDER_RETRY_MINUTES} min until done (deadline {LIMIT_ORDER_DEADLINE_HOUR}:00)")
    print(f"Phase 2: {day_names[MARKET_ORDER_DAY]} {MARKET_ORDER_HOUR}:{MARKET_ORDER_MINUTE:02d}-{MARKET_ORDER_DEADLINE_HOUR}:00 CST")
    print(f"         → Cancel unfilled → Place MARKET orders")
    print(f"{'='*60}")
    print(f"  Edge Cases: Gap-up skip, Position check, Partial fills,")
    print(f"              Holiday detection, Connection retry, Reconnect,")
    print(f"              Order dedup on retry")
    print(f"{'='*60}")
    sys.stdout.flush()
    
    trader = None
    last_signal_gen_week = None    # Track signal generation by week
    last_limit_order_week = None   # Track Monday execution by week (Monday's ISO date)
    last_market_order_date = None  # Track Tuesday execution
    
    # Recover in-memory state from pending_orders.json on startup.
    # If the service restarts mid-week, pending_orders.json tells us Phase 1
    # already ran (at least partially) — used for dedup, not to skip entirely.
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        pending_path = os.path.join(script_dir, ORDER_TRACKING_FILE)
        if os.path.exists(pending_path):
            with open(pending_path, 'r') as _pf:
                pending_data = json.load(_pf)
            created_str = pending_data.get('created_at', '')
            pending_orders_data = pending_data.get('orders', {})
            if created_str:
                created_dt = datetime.fromisoformat(created_str)
                pending_monday = (created_dt.date() - timedelta(days=created_dt.weekday())).isoformat()
                now_boot = get_cst_now()
                current_monday = (now_boot.date() - timedelta(days=now_boot.weekday())).isoformat()
                if pending_monday == current_monday:
                    # Signal gen already happened if we have pending orders
                    last_signal_gen_week = current_monday
                    if pending_orders_data:
                        # Check if we should consider limit orders "done":
                        # If all expected signals have pending orders, mark done.
                        # Otherwise, leave as not-done so retry logic places remaining.
                        signals_path = os.path.join(script_dir, SIGNALS_FILE)
                        try:
                            signals = load_signals(signals_path)
                            valid = [s for s in signals if s.get('direction', '').upper() == 'UP']
                            expected = min(len(valid), MAX_POSITIONS)
                            if len(pending_orders_data) >= expected:
                                last_limit_order_week = current_monday
                                print(f"  ♻️  Recovered: All {len(pending_orders_data)} limit orders already placed this week")
                            else:
                                print(f"  ♻️  Recovered: {len(pending_orders_data)}/{expected} orders placed — will retry remaining")
                        except Exception:
                            # Can't validate — assume partially done, let retry logic handle it
                            print(f"  ♻️  Recovered: {len(pending_orders_data)} pending orders found — retry logic will dedup")
                    print(f"     (pending_orders.json from {created_str})")
                    sys.stdout.flush()
    except Exception as e:
        print(f"  ⚠️ Could not recover state from pending_orders.json: {e}")
        sys.stdout.flush()
    
    retry_interval = LIMIT_ORDER_RETRY_MINUTES * 60  # 30 min in seconds
    
    try:
        while True:
            now = get_cst_now()
            today = now.date()
            current_weekday = now.weekday()
            
            # Monday of the current trading week (stable across Mon night → Tue morning)
            monday_of_week = (today - timedelta(days=current_weekday)).isoformat()
            
            # ============================================================
            # PHASE 0: SIGNAL GENERATION (Monday 4:00 PM CST)
            # Runs predictor to generate fresh signals before limit orders.
            # ============================================================
            if current_weekday == SIGNAL_GEN_DAY and last_signal_gen_week != monday_of_week:
                if now.hour >= SIGNAL_GEN_HOUR:
                    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')} CST] 🤖 SIGNAL GENERATION TIME")
                    print(f"{'='*60}")
                    sys.stdout.flush()
                    
                    signals_path = run_trading_bot()
                    
                    if signals_path:
                        last_signal_gen_week = monday_of_week
                        print(f"\n[{get_cst_now().strftime('%Y-%m-%d %H:%M:%S')} CST] ✅ Signals generated successfully")
                        print(f"   Limit orders will execute at {LIMIT_ORDER_HOUR}:{LIMIT_ORDER_MINUTE:02d} CST")
                        sys.stdout.flush()
                        
                        # Wait until limit order time if we're early
                        secs_to_limit = seconds_until(LIMIT_ORDER_HOUR, LIMIT_ORDER_MINUTE)
                        if secs_to_limit > 0:
                            mins_left = secs_to_limit / 60
                            print(f"   ⏳ Waiting {mins_left:.0f} min for limit order window...")
                            sys.stdout.flush()
                            time.sleep(secs_to_limit + 5)
                        continue
                    else:
                        # Signal generation failed — retry in 30 min
                        deadline_secs = seconds_until(LIMIT_ORDER_DEADLINE_HOUR, 0)
                        if deadline_secs > retry_interval:
                            print(f"   ❌ Signal generation failed — retrying in {LIMIT_ORDER_RETRY_MINUTES} min")
                            sys.stdout.flush()
                            time.sleep(retry_interval)
                            continue
                        else:
                            print(f"   ❌ Signal generation failed and deadline approaching — giving up this week")
                            last_signal_gen_week = monday_of_week
                            last_limit_order_week = monday_of_week
                            sys.stdout.flush()
                            time.sleep(3600)
                            continue
                else:
                    # Before 4 PM — wait
                    secs_to_gen = seconds_until(SIGNAL_GEN_HOUR, SIGNAL_GEN_MINUTE)
                    if secs_to_gen > 0:
                        if secs_to_gen > 1800:
                            hours_left = secs_to_gen / 3600
                            print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')} CST] 🤖 {day_names[SIGNAL_GEN_DAY]}: {hours_left:.1f}h until signal generation ({SIGNAL_GEN_HOUR}:{SIGNAL_GEN_MINUTE:02d} CST)")
                            sys.stdout.flush()
                            time.sleep(1800)
                        else:
                            mins_left = secs_to_gen / 60
                            print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')} CST] 🤖 {day_names[SIGNAL_GEN_DAY]}: {mins_left:.1f} min until signal generation")
                            sys.stdout.flush()
                            time.sleep(secs_to_gen + 5)
                        continue
            
            # ============================================================
            # PHASE 1: LIMIT ORDER PLACEMENT (Monday 4:30 PM → 10 PM CST)
            # Retries every 30 min. Dedup via pending_orders.json.
            # Once all orders placed, stops retrying.
            # ============================================================
            if current_weekday == LIMIT_ORDER_DAY and last_limit_order_week != monday_of_week:
                # Need signals generated first
                if last_signal_gen_week != monday_of_week:
                    # Signal gen hasn't happened yet — let Phase 0 handle it
                    time.sleep(60)
                    continue
                
                # Check we're in the execution window
                now_minutes = now.hour * 60 + now.minute
                limit_start_minutes = LIMIT_ORDER_HOUR * 60 + LIMIT_ORDER_MINUTE
                
                if now_minutes >= limit_start_minutes:
                    # Check deadline
                    if now.hour >= LIMIT_ORDER_DEADLINE_HOUR:
                        print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')} CST] ⚠️ Limit order deadline PASSED ({LIMIT_ORDER_DEADLINE_HOUR}:00 CST)")
                        print(f"   Giving up on remaining limit orders this week")
                        last_limit_order_week = monday_of_week
                        sys.stdout.flush()
                        time.sleep(3600)
                        continue
                    
                    # === EXECUTE LIMIT ORDERS ===
                    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')} CST] 📊 LIMIT ORDER EXECUTION")
                    print(f"{'='*60}")
                    sys.stdout.flush()
                    
                    # Run pre-flight checks
                    if not preflight_checks():
                        print("❌ Pre-flight checks failed.")
                        print(f"   Will retry in {LIMIT_ORDER_RETRY_MINUTES} min")
                        sys.stdout.flush()
                        time.sleep(retry_interval)
                        continue
                    
                    # Connect to IB with retry (Edge Case #9)
                    try:
                        trader = IBAutoTrader()
                        trader.connect_and_run(port=port, max_retries=MAX_CONNECTION_RETRIES)
                        trader.get_account_info()
                    except Exception as e:
                        print(f"❌ Failed to connect to IB Gateway: {e}")
                        print(f"   Will retry in {LIMIT_ORDER_RETRY_MINUTES} min")
                        sys.stdout.flush()
                        time.sleep(retry_interval)
                        continue
                    
                    # Load signals with validation
                    script_dir = os.path.dirname(os.path.abspath(__file__))
                    signals_path = os.path.join(script_dir, SIGNALS_FILE)
                    
                    is_valid, validation_msg = validate_signals_file(signals_path, max_age_hours=12)
                    
                    if not is_valid:
                        print(f"⚠️ Signals file validation failed: {validation_msg}")
                        print(f"   Will retry in {LIMIT_ORDER_RETRY_MINUTES} min")
                        trader.disconnect()
                        sys.stdout.flush()
                        time.sleep(retry_interval)
                        continue
                    
                    print(f"✅ Signals file validated: {validation_msg}")
                    signals = load_signals(signals_path)
                    print(f"Loaded {len(signals)} signals from file")
                    
                    if len(signals) == 0:
                        print("⚠️ No signals available, skipping limit order cycle")
                        trader.disconnect()
                        last_limit_order_week = monday_of_week
                        time.sleep(3600)
                        continue
                    
                    # Get account value from IB (dynamic, not hardcoded!)
                    total_capital = trader.account_value
                    if total_capital <= 0:
                        print("❌ Could not get account value from IB")
                        print(f"   Will retry in {LIMIT_ORDER_RETRY_MINUTES} min")
                        trader.disconnect()
                        sys.stdout.flush()
                        time.sleep(retry_interval)
                        continue
                    
                    # Load existing pending orders for dedup (Edge Case #11)
                    existing_pending = load_pending_orders()
                    exclude_tickers = set(existing_pending.keys())
                    if exclude_tickers:
                        print(f"📋 Dedup: {len(exclude_tickers)} tickers already have pending orders: {', '.join(sorted(exclude_tickers))}")
                    
                    # Execute limit orders (excluding already-placed tickers)
                    # Incremental save: each order is persisted immediately inside
                    # execute_monday_limit_orders to survive mid-batch crashes.
                    tracking_map = trader.execute_monday_limit_orders(
                        signals, MAX_POSITIONS, total_capital,
                        exclude_tickers=exclude_tickers,
                        existing_tracking=existing_pending
                    )
                    
                    # Build final merged view (incremental saves already flushed to disk)
                    all_tracking = {**existing_pending, **tracking_map}
                    
                    # Check if all expected orders are now placed
                    valid_signals = [s for s in signals if s.get('direction', '').upper() == 'UP']
                    expected_tickers = set(s.get('ticker') for s in valid_signals[:MAX_POSITIONS])
                    placed_tickers = set(all_tracking.keys())
                    remaining = expected_tickers - placed_tickers
                    
                    # Also subtract tickers that were legitimately skipped (held, gap-up, invalid)
                    # If tracking_map is empty AND no new orders were placed, either everything
                    # was skipped/deduped or there was a problem. Mark done either way.
                    all_handled = (len(remaining) == 0) or (len(tracking_map) == 0 and len(exclude_tickers) > 0)
                    
                    if all_handled:
                        last_limit_order_week = monday_of_week
                        print(f"\n[{get_cst_now().strftime('%Y-%m-%d %H:%M:%S')} CST] ✅ All limit orders placed ({len(all_tracking)} total)")
                        print(f"   Orders will be checked Tuesday {MARKET_ORDER_HOUR}:{MARKET_ORDER_MINUTE:02d} CST")
                        sys.stdout.flush()
                    else:
                        print(f"\n[{get_cst_now().strftime('%Y-%m-%d %H:%M:%S')} CST] ⚠️ {len(remaining)} orders remaining: {', '.join(sorted(remaining))}")
                        print(f"   Will retry in {LIMIT_ORDER_RETRY_MINUTES} min")
                        sys.stdout.flush()
                    
                    time.sleep(30)
                    if trader:
                        trader.disconnect()
                        trader = None
                    
                    if not all_handled:
                        time.sleep(retry_interval - 30)  # Account for the 30s sleep above
                    else:
                        time.sleep(3600)
                    continue
                
                else:
                    # Before execution window — wait
                    secs_to_limit = seconds_until(LIMIT_ORDER_HOUR, LIMIT_ORDER_MINUTE)
                    if secs_to_limit > 0:
                        mins_left = secs_to_limit / 60
                        print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')} CST] 📊 {day_names[LIMIT_ORDER_DAY]}: {mins_left:.0f} min until limit orders ({LIMIT_ORDER_HOUR}:{LIMIT_ORDER_MINUTE:02d} CST)")
                        sys.stdout.flush()
                        time.sleep(min(secs_to_limit + 5, 1800))
                    else:
                        time.sleep(60)
                    continue
            
            # ============================================================
            # PHASE 2: MARKET ORDER FALLBACK (Tuesday 8 AM → 9 AM CST)
            # Handles unfilled limit orders from Monday.
            # Only processes tickers in pending_orders.json — no duplicates.
            # ============================================================
            if current_weekday == MARKET_ORDER_DAY and last_market_order_date != today:
                in_market_window = (
                    now.hour >= MARKET_ORDER_HOUR and
                    now.hour < MARKET_ORDER_DEADLINE_HOUR
                )
                
                if in_market_window:
                    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')} CST] 🔔 MARKET ORDER FALLBACK TIME")
                    print(f"{'='*60}")
                    sys.stdout.flush()
                    
                    deadline = now.replace(hour=MARKET_ORDER_DEADLINE_HOUR, minute=0, second=0, microsecond=0)
                    hours_left = (deadline - now).total_seconds() / 3600
                    
                    if not preflight_checks():
                        print("❌ Pre-flight checks failed.")
                        if hours_left > 0.25:
                            print(f"   ⏳ {hours_left:.1f}h until deadline — will retry in 15 min")
                            sys.stdout.flush()
                            time.sleep(900)
                        else:
                            print(f"   ❌ Deadline approaching — giving up on market orders")
                            last_market_order_date = today
                            sys.stdout.flush()
                            time.sleep(3600)
                        continue
                    
                    try:
                        trader = IBAutoTrader()
                        trader.connect_and_run(port=port, max_retries=MAX_CONNECTION_RETRIES)
                        trader.get_account_info()
                    except Exception as e:
                        print(f"❌ Failed to connect to IB Gateway: {e}")
                        if hours_left > 0.25:
                            print(f"   ⏳ Will retry in 15 min")
                            sys.stdout.flush()
                            time.sleep(900)
                        else:
                            print(f"   ❌ Deadline approaching — giving up on market orders")
                            last_market_order_date = today
                            sys.stdout.flush()
                            time.sleep(3600)
                        continue
                    
                    pending_orders = load_pending_orders()
                    
                    if not pending_orders:
                        print("⚠️ No pending orders from Monday")
                        print("   Nothing to check or convert to market orders")
                        trader.disconnect()
                        last_market_order_date = today
                        time.sleep(3600)
                        continue
                    
                    total_capital = trader.account_value
                    if total_capital <= 0:
                        print("❌ Could not get account value from IB")
                        trader.disconnect()
                        if hours_left > 0.25:
                            print(f"   ⏳ Will retry in 15 min")
                            sys.stdout.flush()
                            time.sleep(900)
                        else:
                            last_market_order_date = today
                            sys.stdout.flush()
                            time.sleep(3600)
                        continue
                    
                    final_tracking = trader.execute_tuesday_market_fallback(
                        pending_orders, total_capital
                    )
                    
                    clear_pending_orders()
                    
                    last_market_order_date = today
                    print(f"\n[{get_cst_now().strftime('%Y-%m-%d %H:%M:%S')} CST] ✅ Trade cycle complete")
                    sys.stdout.flush()
                    
                    time.sleep(30)
                    if trader:
                        trader.disconnect()
                        trader = None
                    
                    time.sleep(3600)
                    continue
                
                # Not in window yet
                secs_to_market = seconds_until(MARKET_ORDER_HOUR, MARKET_ORDER_MINUTE)
                
                if secs_to_market > 0:
                    if secs_to_market > 1800:
                        hours_left = secs_to_market / 3600
                        print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')} CST] 💰 {day_names[MARKET_ORDER_DAY]}: {hours_left:.1f}h until market fallback ({MARKET_ORDER_HOUR}:{MARKET_ORDER_MINUTE:02d} CST)")
                        sys.stdout.flush()
                        time.sleep(1800)
                    else:
                        mins_left = secs_to_market / 60
                        print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')} CST] 💰 {day_names[MARKET_ORDER_DAY]}: {mins_left:.1f} min until market fallback")
                        sys.stdout.flush()
                        time.sleep(secs_to_market + 5)
                    continue
                
                if now.hour >= MARKET_ORDER_DEADLINE_HOUR:
                    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')} CST] ⚠️ Market order deadline PASSED for today")
                    sys.stdout.flush()
                    last_market_order_date = today
                    continue
            
            # ============================================================
            # WAITING STATE: Calculate next scheduled event
            # ============================================================
            days_to_signal = (SIGNAL_GEN_DAY - current_weekday) % 7
            days_to_limit = (LIMIT_ORDER_DAY - current_weekday) % 7
            days_to_market = (MARKET_ORDER_DAY - current_weekday) % 7
            
            # If same week but already done, wait for next week
            if days_to_signal == 0 and last_signal_gen_week == monday_of_week:
                days_to_signal = 7
            if days_to_limit == 0 and last_limit_order_week == monday_of_week:
                days_to_limit = 7
            if days_to_market == 0 and last_market_order_date == today:
                days_to_market = 7
            
            # Determine next event (signal gen → limit orders → market fallback)
            events = [
                (days_to_signal, SIGNAL_GEN_HOUR, f"signal generation ({day_names[SIGNAL_GEN_DAY]} {SIGNAL_GEN_HOUR}:{SIGNAL_GEN_MINUTE:02d})"),
                (days_to_limit, LIMIT_ORDER_HOUR * 60 + LIMIT_ORDER_MINUTE, f"limit orders ({day_names[LIMIT_ORDER_DAY]} {LIMIT_ORDER_HOUR}:{LIMIT_ORDER_MINUTE:02d})"),
                (days_to_market, MARKET_ORDER_HOUR, f"market fallback ({day_names[MARKET_ORDER_DAY]} {MARKET_ORDER_HOUR}:{MARKET_ORDER_MINUTE:02d})"),
            ]
            events.sort(key=lambda x: (x[0], x[1]))
            next_event = events[0][2]
            days_until = events[0][0]
            
            if days_until == 0:
                print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')} CST] Waiting for {next_event}...")
            else:
                print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')} CST] Next: {next_event} in {days_until} day(s)")
            
            sys.stdout.flush()
            time.sleep(3600)  # Check every hour
            
    except KeyboardInterrupt:
        print("\n\nScheduler stopped by user")
    except Exception as e:
        print(f"\n❌ ERROR in scheduler: {e}")
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        raise
    finally:
        if trader:
            trader.disconnect()


def execute_once(signals_path: str, close_first: bool = True,
                 port: int = PAPER_TRADING_PORT, use_limit: bool = False):
    """
    Execute signals once (manual mode).
    
    Args:
        signals_path: Path to signals JSON file
        close_first: If True, close all positions before buying
        port: IB port (4002 paper, 4001 live)
        use_limit: If True, use limit orders; if False, use market orders
    """
    # Validate signals file with Citadel-grade checks
    is_valid, validation_msg = validate_signals_file(signals_path, max_age_hours=72)  # 72h for manual mode
    if not is_valid:
        print(f"❌ Signals file validation failed: {validation_msg}")
        print(f"   File: {signals_path}")
        sys.exit(1)
    
    # Load signals
    print(f"✅ Signals file validated: {validation_msg}")
    print(f"Loading signals from: {signals_path}")
    signals = load_signals(signals_path)
    print(f"Loaded {len(signals)} signals")
    
    # Create trader and connect with retry
    trader = IBAutoTrader()
    
    try:
        trader.connect_and_run(port=port, max_retries=MAX_CONNECTION_RETRIES)
        trader.get_account_info()
        
        # Close all existing positions first
        if close_first:
            print(f"\n--- Closing all existing positions ---")
            trader.sell_all_positions()
            time.sleep(30)  # Wait for sells to fill
            
            # Refresh account value
            trader.account_ready.clear()
            trader.reqAccountSummary(9002, "All", "NetLiquidation")
            trader.account_ready.wait(timeout=10)
        
        # Use account value from IB (dynamic, not hardcoded!)
        total_capital = trader.account_value
        if total_capital <= 0:
            print("❌ Could not get account value from IB, using $100,000 default")
            total_capital = 100000
        
        print(f"\n--- Opening new positions ---")
        print(f"Account Value: ${total_capital:,.2f} (from IB)")
        print(f"Per Position: ${total_capital/MAX_POSITIONS:,.2f} (1/{MAX_POSITIONS})")
        print(f"Order Type: {'LIMIT (+0.1%)' if use_limit else 'MARKET'}")
        
        # Execute trades using new methods
        if use_limit:
            order_map = trader.execute_monday_limit_orders(signals, MAX_POSITIONS, total_capital)
            # Incremental save happens inside the method; final save for completeness
            if order_map:
                save_pending_orders(order_map)
        else:
            # Legacy market order execution
            order_map = trader.execute_signals(signals, MAX_POSITIONS, total_capital)
        
        print(f"\n{'='*60}")
        print(f"EXECUTION COMPLETE")
        print(f"{'='*60}")
        print(f"Orders placed: {len(order_map)}")
        
        time.sleep(5)
        
    except KeyboardInterrupt:
        print("\nShutdown requested...")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        trader.disconnect()
        print("Disconnected from IB")


def sell_all(port: int = PAPER_TRADING_PORT):
    """Sell all positions immediately (manual mode)."""
    trader = IBAutoTrader()
    
    try:
        trader.connect_and_run(port=port, max_retries=MAX_CONNECTION_RETRIES)
        trader.get_account_info()
        trader.sell_all_positions()
        time.sleep(10)
    except Exception as e:
        print(f"Error: {e}")
    finally:
        trader.disconnect()
        print("Disconnected from IB")


def check_pending(port: int = PAPER_TRADING_PORT):
    """Check status of pending orders from Monday (manual diagnostic)."""
    trader = IBAutoTrader()
    
    try:
        trader.connect_and_run(port=port, max_retries=MAX_CONNECTION_RETRIES)
        
        # Load pending orders
        pending = load_pending_orders()
        
        if not pending:
            print("No pending orders to check")
            return
        
        print(f"\n{'='*60}")
        print("PENDING ORDER STATUS")
        print(f"{'='*60}")
        
        for symbol, info in pending.items():
            entry_id = info.get('entry_order_id')
            shares = info.get('shares', 0)
            entry_price = info.get('entry_price', 0)
            
            status = trader.get_order_status(entry_id)
            filled = status.get('filled', 0)
            remaining = status.get('remaining', shares)
            order_status = status.get('status', 'Unknown')
            
            fill_pct = (filled / shares * 100) if shares > 0 else 0
            
            print(f"\n{symbol}:")
            print(f"  Order ID: {entry_id}")
            print(f"  Status: {order_status}")
            print(f"  Entry Price: ${entry_price:.2f}")
            print(f"  Filled: {filled}/{shares} ({fill_pct:.1f}%)")
            
            if order_status == "Filled":
                print(f"  ✅ Fully filled")
            elif filled > 0:
                print(f"  ⚠️ Partially filled - {remaining} shares remaining")
            else:
                print(f"  ⏳ Not yet filled")
        
        print(f"\n{'='*60}")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        trader.disconnect()


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  Schedule mode:   python trade_executor.py schedule")
        print("  Manual market:   python trade_executor.py <signals_json>")
        print("  Manual limit:    python trade_executor.py <signals_json> --limit")
        print("  Sell all:        python trade_executor.py sell")
        print("  Check pending:   python trade_executor.py check")
        print("")
        print("Examples:")
        print("  python trade_executor.py schedule")
        print("  python trade_executor.py signals/current_signals.json")
        print("  python trade_executor.py signals/current_signals.json --limit")
        print("  python trade_executor.py sell")
        print("  python trade_executor.py check")
        print("")
        print("Schedule Mode:")
        print(f"  Monday {SIGNAL_GEN_HOUR}:{SIGNAL_GEN_MINUTE:02d} CST:  Generate trading signals (run predictor)")
        print(f"  Monday {LIMIT_ORDER_HOUR}:{LIMIT_ORDER_MINUTE:02d} CST:  Place LIMIT orders at live_price + 0.1%")
        print(f"                    Retries every {LIMIT_ORDER_RETRY_MINUTES} min until done (deadline {LIMIT_ORDER_DEADLINE_HOUR}:00)")
        print(f"  Tuesday {MARKET_ORDER_HOUR}:{MARKET_ORDER_MINUTE:02d} CST: Cancel unfilled → MARKET orders")
        print("")
        print("Notes:")
        print(f"  - Uses {MAX_POSITIONS} positions (account_value / {MAX_POSITIONS} each)")
        print("  - Account value fetched dynamically from IB (not hardcoded)")
        print("  - Edge cases: gap-up skip, position check, partial fills,")
        print("                holiday detection, connection retry, order dedup")
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    # Check for schedule mode
    if command == "schedule":
        run_scheduled_cycle()
        
    # Check for sell mode
    elif command == "sell":
        sell_all()
    
    # Check pending orders status
    elif command == "check":
        check_pending()
        
    # Manual execute mode
    else:
        signals_path = sys.argv[1]
        use_limit = "--limit" in sys.argv
        execute_once(signals_path, use_limit=use_limit)


if __name__ == "__main__":
    main()
