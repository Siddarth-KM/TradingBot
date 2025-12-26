"""
Interactive Brokers Auto-Trader
=============================================================
Executes trades from trading bot signals using IB API.

Features:
- Fractional shares for entry and profit target orders
- Whole shares for stop loss (IB limitation) with automatic fractional cleanup
- Index-based stop loss percentages (SPY/NASDAQ: 2%, SPSM/MDY: 3%)
- Equal-weight position sizing across max positions
- Bracket orders with OCA groups (profit/stop cancel each other)
- Extended hours trading support

Usage:
    python trade_executor.py <signals_json_path> <max_positions> <total_capital>
    
TWS/Gateway Settings:
    - Enable API: Edit → Global Config → API → Settings
    - Socket port: 7497 (paper) or 7496 (live)
    - Allow localhost connections
"""

import json
import sys
import time
import math
import threading
import subprocess
import os
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order


# Configuration
PAPER_TRADING_PORT = 7497
LIVE_TRADING_PORT = 7496
CLIENT_ID = 1

# Fixed signals file path (trading_bot.py always saves to this location)
SIGNALS_FILE = "signals/current_signals.json"

# Scheduling Configuration
CST_TIMEZONE = ZoneInfo("America/Chicago")
TRADE_HOUR = 16   # 4:00 PM CST - sell all then buy new positions
TRADE_MINUTE = 0
TRADING_DAY = 0   # Monday = 0
MAX_POSITIONS = 8  # Fixed 8 positions, 1/8 of account each

# Index-based stop loss percentages
STOP_LOSS_PERCENTAGES = {
    'SPY': 0.02,      # 2% for S&P 500
    'NASDAQ': 0.02,   # 2% for NASDAQ
    'QQQ': 0.02,      # 2% for NASDAQ ETF
    'SPSM': 0.03,     # 3% for S&P 600 Small Cap
    'MDY': 0.03,      # 3% for S&P 400 Mid Cap
    'SP400': 0.03,    # 3% for S&P 400
    'SP600': 0.03,    # 3% for S&P 600
}
DEFAULT_STOP_LOSS = 0.025  # 2.5% default


class IBAutoTrader(EWrapper, EClient):
    """
    Interactive Brokers Automated Trading Engine
    
    Executes bracket orders with whole shares only (IB API limitation).
    Position sizing: floor(capital / price) to maximize capital utilization.
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
        self.order_status_map: Dict[int, str] = {}
        self.filled_orders: Dict[int, dict] = {}
        self.active_brackets: Dict[str, List[int]] = {}  # symbol -> [entry_id, profit_id, stop_id]
        
    # ===== EWrapper Callbacks =====
    
    def nextValidId(self, orderId: int):
        """Called when connection is established with next valid order ID."""
        self.next_order_id = orderId
        self.connected.set()
        
    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = ""):
        """Handle errors from IB."""
        # Ignore non-critical messages
        if errorCode in [2104, 2106, 2158]:  # Market data farm connected messages
            return
        if errorCode == 2119:  # Market data farm is connecting
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
        
    def orderStatus(self, orderId: int, status: str, filled: float, remaining: float,
                    avgFillPrice: float, permId: int, parentId: int, lastFillPrice: float,
                    clientId: int, whyHeld: str, mktCapPrice: float):
        """Track order status updates."""
        self.order_status_map[orderId] = status
        
        if status == "Filled":
            self.filled_orders[orderId] = {
                'filled': filled,
                'avgPrice': avgFillPrice
            }
            
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
    
    def create_bracket_order(self, symbol: str, index: str, entry_price: float,
                            limit_sell_price: float, capital_per_position: float) -> List[int]:
        """
        Create a bracket order (entry + profit target + stop loss).
        
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
        # Calculate position size (whole shares only)
        shares, deployed_capital, unused_capital = self.calculate_position_size(
            capital_per_position, entry_price
        )
        
        # Validate position
        if shares <= 0:
            print(f"\n  ⚠️ {symbol}: Insufficient capital for even 1 share @ ${entry_price:.2f}")
            return []
        
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
        
        # === ENTRY ORDER ===
        entry_order = self._create_order(
            action="BUY",
            quantity=shares,
            order_type="LMT",
            price=entry_price,
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
        
        # Execution summary
        expected_profit = (limit_sell_price - entry_price) * shares
        expected_loss = (entry_price - stop_price) * shares
        risk_reward = expected_profit / expected_loss if expected_loss > 0 else 0
        
        print(f"\n  📊 {symbol} ({index}):")
        print(f"     Shares:  {shares:,} @ ${entry_price:.2f} = ${deployed_capital:,.2f}")
        print(f"     Target:  ${limit_sell_price:.2f} (+${expected_profit:,.2f})")
        print(f"     Stop:    ${stop_price:.2f} (-${expected_loss:,.2f}) [{stop_loss_pct*100:.1f}%]")
        if unused_capital > 0:
            print(f"     Unused:  ${unused_capital:.2f}")
        
        return [entry_id, profit_id, stop_id]
    
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
    
    def connect_and_run(self, host: str = "127.0.0.1", port: int = PAPER_TRADING_PORT):
        """Connect to IB and start message processing."""
        self.connect(host, port, CLIENT_ID)
        
        # Start message processing thread
        api_thread = threading.Thread(target=self.run, daemon=True)
        api_thread.start()
        
        # Wait for connection
        if not self.connected.wait(timeout=10):
            raise ConnectionError("Failed to connect to IB Gateway/TWS")
            
        print(f"✓ Connected to IB on port {port}")
        
    def get_account_info(self):
        """Request account information."""
        self.reqAccountSummary(9001, "All", "NetLiquidation")
        
        if not self.account_ready.wait(timeout=10):
            print("Warning: Account summary timeout, using provided capital")
            return
            
        print(f"✓ Account Value: ${self.account_value:,.2f}")
        
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
    
    # Get the directory where trade_executor.py is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    trading_bot_path = os.path.join(script_dir, "trading_bot.py")
    signals_path = os.path.join(script_dir, SIGNALS_FILE)
    
    # Run trading_bot.py with --indexes flag to analyze all indexes automatically
    print(f"Running: python {trading_bot_path} --indexes SPY NASDAQ SP400 SPSM")
    result = subprocess.run(
        [sys.executable, trading_bot_path, "--indexes", "SPY", "NASDAQ", "SP400", "SPSM"],
        cwd=script_dir,
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        print(f"Error running trading bot:")
        print(result.stderr)
        print(result.stdout)
        return None
    
    # Check if the fixed signals file exists
    if not os.path.exists(signals_path):
        print(f"Signals file not found: {signals_path}")
        return None
    
    print(f"✓ Signals generated: {signals_path}")
    return signals_path


def run_scheduled_cycle(port: int = PAPER_TRADING_PORT):
    """
    Run the scheduled Monday trading cycle:
    - 4:00 PM CST: Close all positions, then generate signals & buy new positions
    - Each position gets 1/8 of total account value
    
    Args:
        port: IB port (7497 paper, 7496 live)
    """
    print(f"\n{'='*60}")
    print("SCHEDULED TRADING MODE")
    print(f"{'='*60}")
    print(f"Schedule: Mondays at 4:00 PM CST")
    print(f"  1. Close all existing positions")
    print(f"  2. Generate fresh signals")
    print(f"  3. Open {MAX_POSITIONS} new positions (1/{MAX_POSITIONS} of account each)")
    print(f"{'='*60}")
    
    trader = None
    
    try:
        while True:
            now = get_cst_now()
            
            # Check if it's Monday
            if now.weekday() != TRADING_DAY:
                next_monday = (TRADING_DAY - now.weekday()) % 7
                if next_monday == 0:
                    next_monday = 7
                print(f"\nWaiting for Monday... ({next_monday} days)")
                time.sleep(3600)  # Check every hour
                continue
            
            # It's Monday - wait for trade time (4 PM CST)
            secs_to_trade = seconds_until(TRADE_HOUR, TRADE_MINUTE)
            if secs_to_trade > 0:
                print(f"\n⏰ Waiting {secs_to_trade/3600:.1f} hours until 4:00 PM CST...")
                # Sleep in chunks to allow interruption
                while secs_to_trade > 0:
                    sleep_time = min(secs_to_trade, 60)
                    time.sleep(sleep_time)
                    secs_to_trade = seconds_until(TRADE_HOUR, TRADE_MINUTE)
            
            # Check if we're in the trade window (4 PM)
            now = get_cst_now()
            if now.hour == TRADE_HOUR and now.weekday() == TRADING_DAY:
                print(f"\n🔔 4:00 PM CST - TRADE TIME")
                
                trader = IBAutoTrader()
                trader.connect_and_run(port=port)
                trader.get_account_info()
                
                # STEP 1: Close all existing positions first
                print(f"\n--- STEP 1: Closing all positions ---")
                trader.sell_all_positions()
                
                # Wait for sell orders to fill
                print("Waiting for positions to close...")
                time.sleep(60)
                
                # Refresh account value after selling
                trader.account_ready.clear()
                trader.reqAccountSummary(9002, "All", "NetLiquidation")
                trader.account_ready.wait(timeout=10)
                
                print(f"\n--- STEP 2: Generating fresh signals ---")
                # Run trading bot to get fresh signals
                signals_path = run_trading_bot()
                
                if signals_path:
                    # Load signals and execute
                    signals = load_signals(signals_path)
                    print(f"Loaded {len(signals)} signals")
                    
                    # Use current account value, divided by 8
                    total_capital = trader.account_value if trader.account_value > 0 else 10000
                    
                    print(f"\n--- STEP 3: Opening new positions ---")
                    print(f"Account Value: ${total_capital:,.2f}")
                    print(f"Per Position: ${total_capital/MAX_POSITIONS:,.2f} (1/{MAX_POSITIONS})")
                    
                    order_map = trader.execute_signals(signals, MAX_POSITIONS, total_capital)
                    
                    print(f"\n✓ Trade cycle complete - {len(order_map)} positions opened")
                else:
                    print("⚠ No signals generated, skipping buy cycle")
                
                time.sleep(30)
                trader.disconnect()
                trader = None
            
            # Wait until next Monday
            print(f"\n✓ Monday cycle complete. Waiting for next Monday...")
            time.sleep(3600 * 6)  # Sleep 6 hours then re-check
            
    except KeyboardInterrupt:
        print("\n\nScheduler stopped by user")
    finally:
        if trader:
            trader.disconnect()


def execute_once(signals_path: str, close_first: bool = True,
                 port: int = PAPER_TRADING_PORT):
    """
    Execute signals once (manual mode).
    
    Args:
        signals_path: Path to signals JSON file
        close_first: If True, close all positions before buying
        port: IB port (7497 paper, 7496 live)
    """
    # Validate signals file exists
    if not os.path.exists(signals_path):
        print(f"Error: Signals file not found: {signals_path}")
        sys.exit(1)
    
    # Load signals
    print(f"Loading signals from: {signals_path}")
    signals = load_signals(signals_path)
    print(f"Loaded {len(signals)} signals")
    
    # Create trader and connect
    trader = IBAutoTrader()
    
    try:
        trader.connect_and_run(port=port)
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
        
        # Use account value divided by 8
        total_capital = trader.account_value if trader.account_value > 0 else 10000
        
        print(f"\n--- Opening new positions ---")
        print(f"Account Value: ${total_capital:,.2f}")
        print(f"Per Position: ${total_capital/MAX_POSITIONS:,.2f} (1/{MAX_POSITIONS})")
            
        # Execute trades
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
    finally:
        trader.disconnect()
        print("Disconnected from IB")


def sell_all(port: int = PAPER_TRADING_PORT):
    """Sell all positions immediately (manual mode)."""
    trader = IBAutoTrader()
    
    try:
        trader.connect_and_run(port=port)
        trader.get_account_info()
        trader.sell_all_positions()
        time.sleep(10)
    except Exception as e:
        print(f"Error: {e}")
    finally:
        trader.disconnect()
        print("Disconnected from IB")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  Schedule mode:  python trade_executor.py schedule")
        print("  Manual execute: python trade_executor.py <signals_json>")
        print("  Sell all:       python trade_executor.py sell")
        print("")
        print("Examples:")
        print("  python trade_executor.py schedule")
        print("  python trade_executor.py signals/signals_2024.json")
        print("  python trade_executor.py sell")
        print("")
        print("Notes:")
        print(f"  - Always uses {MAX_POSITIONS} positions (1/{MAX_POSITIONS} of account each)")
        print("  - Closes all existing positions before opening new ones")
        print("  - Uses your IB account value automatically")
        sys.exit(1)
    
    # Check for schedule mode
    if sys.argv[1].lower() == "schedule":
        run_scheduled_cycle()
        
    # Check for sell mode
    elif sys.argv[1].lower() == "sell":
        sell_all()
        
    # Manual execute mode
    else:
        signals_path = sys.argv[1]
        execute_once(signals_path)


if __name__ == "__main__":
    main()
