import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np

from bot_config import *
from bot_utils import *
from bot_core import *


def select_top_2_per_index(all_index_results):
    """
    Select top 2 stocks from each index based on:
    1. Positive prediction (pred > 0)
    2. UP direction from CatBoost
    3. Highest prediction value
    
    Args:
        all_index_results: dict {index_name: [list of predictions]}
        
        
    Returns:
        dict: {index_name: [top 2 stocks]}
    """
    final_selections = {}
    
    for index_name, predictions in all_index_results.items():
        # Filter already done by filter_positive_predictions
        # But double-check for safety
        positive_stocks = [
            s for s in predictions
            if s.get('pred', 0) > 0 and s.get('direction', '') == 'up'
        ]
        
        # Sort by prediction value (highest first)
        positive_stocks.sort(key=lambda x: x['pred'], reverse=True)
        
        # Take top 2
        top_2 = positive_stocks[:2]
        
        # print(f"\n📊 {index_name}:")
        # print(f"   Available: {len(positive_stocks)} positive UP stocks")
        # print(f"   Selected: {len(top_2)} stocks")
        
        # for i, stock in enumerate(top_2, 1):
        #     print(f"   {i}. {stock['ticker']}: "
        #           f"{stock['pred']*100:.2f}% "
        #           f"({stock.get('direction', 'N/A').upper()} "
        #           f"{stock.get('direction_probability', 0):.1f}%)")
        
        final_selections[index_name] = top_2
    
    return final_selections


def format_trading_signals(selections, prediction_window=5):
    """
    Format top selections as trading signals.
    
    Args:
        selections: dict {index_name: [top stocks]}
        prediction_window: int
        
    Returns:
        dict with structured signals
    """
    all_signals = []
    signals_by_index = {}
    
    for index_name, stocks in selections.items():
        index_signals = []
        
        for stock in stocks:
            # Extract values
            predicted_return = stock['pred']
            direction_probability = stock.get('direction_probability', 0)
            last_close = stock.get('close')
            
            # Calculate limit sell: (1 + predicted_return * direction_probability/100) * last_close
            limit_sell = round((1 + predicted_return * direction_probability / 100) * last_close, 2) if last_close else None
            
            signal = {
                'ticker': stock['ticker'],
                'index': index_name,
                'predicted_return': round(predicted_return, 5),
                'direction': stock.get('direction', 'up'),
                'direction_probability': round(direction_probability, 2),
                'last_close': round(last_close, 2) if last_close else None,
                'limit_sell': limit_sell,
                'timestamp': datetime.now().isoformat()
            }
            
            index_signals.append(signal)
            all_signals.append(signal)
        
        signals_by_index[index_name] = index_signals
    
    # Sort all signals by predicted return (highest first)
    all_signals.sort(key=lambda x: x['predicted_return'], reverse=True)
    
    # Calculate summary statistics
    returns = [s['predicted_return'] for s in all_signals]
    avg_return = np.mean(returns) if returns else 0
    
    return {
        'timestamp': datetime.now().isoformat(),
        'prediction_window': prediction_window,
        'total_signals': len(all_signals),
        'signals_by_index': signals_by_index,
        'all_signals': all_signals,
        'summary': {
            'indexes_analyzed': list(selections.keys()),
            'signals_per_index': {k: len(v) for k, v in selections.items()},
            'avg_predicted_return': round(avg_return * 100, 2),
            'best_signal': all_signals[0]['ticker'] if all_signals else None,
            'best_return': all_signals[0]['predicted_return'] if all_signals else 0
        }
    }

def save_signals_to_json(results, output_dir=OUTPUT_DIR):
    """Save trading signals to JSON file - uses fixed filename that overwrites each time"""
    os.makedirs(output_dir, exist_ok=True)
    
    # Fixed filename - always overwrites (this is what trade_executor.py looks for)
    filename = f"{output_dir}/current_signals.json"
    
    with open(filename, 'w') as f:
        json.dump(sanitize_for_json(results), f, indent=2, default=str)
    
    # Also save a timestamped backup for history
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_filename = f"{output_dir}/signals_{timestamp}.json"
    with open(backup_filename, 'w') as f:
        json.dump(sanitize_for_json(results), f, indent=2, default=str)
    
    return filename





def generate_trading_signals(
    indexes_to_analyze=None,
    prediction_window=PREDICTION_WINDOW,
    start_date=None,
    end_date=None,
    force_refresh=False
):
    """
    Generate trading signals for all indexes.
    
    Args:
        indexes_to_analyze: List of index names to process
        prediction_window: Days ahead to predict (default: 5)
        start_date: Historical data start date (default: 18 months ago)
        end_date: Historical data end date (default: today) - for backtesting
        force_refresh: Force data re-download (default: False)
        
    Returns:
        dict: Trading signals with metadata
    """
    
    if indexes_to_analyze is None:
        indexes_to_analyze = INDEXES_TO_ANALYZE
    
    # Calculate default start date (18 months ago)
    if start_date is None:
        start_date = get_default_start_date()
    
    # print(f"\n🤖 TRADING BOT STARTED")
    # print(f"📅 Analysis Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    # print(f"📊 Indexes: {indexes_to_analyze}")
    # print(f"⏰ Prediction Window: {prediction_window} days")
    # print(f"📆 Historical Data: {start_date} to present")
    # print("="*70)
    
    # Download market data once (shared across all indexes)
    # print("\n📥 Downloading market reference data...")
    market_data_cache = download_market_data_cache(start_date, end_date, force_refresh)
    
    all_index_results = {}
    
    # Cross-index ticker cache: avoids re-downloading overlapping tickers
    # (e.g., AAPL appears in both SPY and NASDAQ — download once, reuse).
    # Each call to download_index_data() reads from and writes back to this dict.
    shared_stock_cache = {}
    
    # Loop through each index
    for index_name in indexes_to_analyze:
        # print(f"\n{'='*70}")
        # print(f"📊 Processing {index_name}")
        # print(f"{'='*70}")
        
        try:
            # Step 1: Download index constituents (with cross-index dedup)
            # print(f"📥 Downloading {index_name} constituent data...")
            result = download_index_data(index_name, start_date, end_date, force_refresh,
                                         shared_stock_cache=shared_stock_cache)
            
            if isinstance(result, tuple):
                stock_data, fallback_used, successful_downloads, failed_downloads = result
            else:
                stock_data = result
                successful_downloads = len(stock_data)
            
            if successful_downloads == 0:
                # print(f"⚠️ No data for {index_name}, skipping...")
                all_index_results[index_name] = []
                continue
            
            # print(f"✅ Downloaded {successful_downloads} stocks")
            
            # Step 2: Add features
            # print(f"⚙️ Adding features...")
            processed_data = add_features_parallel(
                stock_data, 
                prediction_window, 
                market_data_cache
            )
            
            if not processed_data:
                # print(f"⚠️ Feature processing failed for {index_name}")
                all_index_results[index_name] = []
                continue
            
            # print(f"✅ Features added to {len(processed_data)} stocks")
            
            # Step 3: Detect market regime
            etf_ticker = INDEX_ETF_TICKERS.get(index_name)
            etf_df = stock_data.get(etf_ticker)
            
            if etf_df is not None:
                market_condition, market_strength = detect_market_regime(etf_df)
            else:
                first_stock = list(processed_data.values())[0]
                market_condition, market_strength = detect_market_regime(first_stock)
            
            # print(f"📈 Market: {market_condition} (strength: {market_strength:.2f})")
            
            # Step 4: Select models
            selected_models = select_models_for_market(market_condition, False)
            # print(f"🎯 Models: {selected_models}")
            
            # Step 5: Train models
            # print(f"🤖 Training models...")
            trained_models = train_models_parallel(
                processed_data,
                selected_models,
                market_condition,
                market_strength,
                prediction_window
            )
            
            if not trained_models:
                # print(f"⚠️ Model training failed for {index_name}")
                all_index_results[index_name] = []
                continue
            
            # print(f"✅ Trained models for {len(trained_models)} stocks")
            
            # Step 6: Get market sentiment
            # print(f"📰 Analyzing sentiment...")
            market_sentiment_score, _ = analyze_ticker_sentiment(index_name)
            # print(f"✅ Sentiment: {market_sentiment_score:.1f}")
            
            # Step 7: Create predictions
            stock_predictions = []
            for ticker, model_preds in trained_models.items():
                if etf_ticker and ticker == etf_ticker:
                    continue
                
                # Extract prediction
                if isinstance(model_preds, dict) and 'prediction' in model_preds:
                    pred = model_preds['prediction']
                elif isinstance(model_preds, dict):
                    pred = np.mean(list(model_preds.values())) if model_preds else 0.0
                else:
                    pred = float(model_preds) if model_preds is not None else 0.0
                
                # Apply sentiment
                adjusted_pred = apply_sentiment_adjustment(
                    pred, 
                    market_sentiment_score, 
                    prediction_window
                )
                
                stock_df = stock_data.get(ticker)
                last_close = float(stock_df['Close'].iloc[-1]) if stock_df is not None and len(stock_df) > 0 else None
                
                stock_predictions.append({
                    'ticker': ticker,
                    'pred': adjusted_pred,
                    'close': last_close
                })
            
            # Step 8: Apply directional confidence
            # print(f"🔍 Adding directional confidence...")
            stock_predictions = apply_direction_confidence_parallel(
                stock_predictions,
                processed_data,
                prediction_window
            )
            
            # Step 9: Filter for positive UP predictions
            # print(f"✅ Filtering for positive UP predictions...")
            stock_predictions = filter_positive_predictions(stock_predictions)
            
            # Store results
            all_index_results[index_name] = stock_predictions
            
        except Exception as e:
            # print(f"❌ Error processing {index_name}: {e}")
            import traceback
            # traceback.print_exc()
            all_index_results[index_name] = []
    
    # Select top 2 from each index
    # print(f"\n{'='*70}")
    # print("🎯 SELECTING TOP 2 STOCKS PER INDEX")
    # print(f"{'='*70}")
    
    final_selections = select_top_2_per_index(all_index_results)
    
    # Format output
    results = format_trading_signals(final_selections, prediction_window)
    
    return results


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Trading Bot - Generate Stock Signals')
    parser.add_argument('--indexes', nargs='+', 
                       help='Indexes to analyze (e.g., SPY NASDAQ SP400 SPSM)')
    parser.add_argument('--window', type=int, default=PREDICTION_WINDOW,
                       help=f'Prediction window in days (default: {PREDICTION_WINDOW})')
    parser.add_argument('--refresh', action='store_true',
                       help='Force refresh of cached data')
    parser.add_argument('--start-date', type=str,
                       help='Historical data start date (YYYY-MM-DD, default: 18 months ago)')
    parser.add_argument('--end-date', type=str,
                       help='Historical data end date (YYYY-MM-DD, default: today) - for backtesting')
    
    args = parser.parse_args()
    
    print("="*70)
    print("🤖 TRADING BOT")
    print("="*70)
    
    # If no indexes specified via command line, ask user interactively
    if not args.indexes:
        print("\n📊 Available Indexes:")
        for i, idx in enumerate(INDEXES_TO_ANALYZE, 1):
            print(f"  {i}. {idx}")
        print(f"  {len(INDEXES_TO_ANALYZE) + 1}. All indexes")
        
        while True:
            try:
                choice = input(f"\nSelect index (1-{len(INDEXES_TO_ANALYZE) + 1}): ").strip()
                choice_num = int(choice)
                
                if 1 <= choice_num <= len(INDEXES_TO_ANALYZE):
                    args.indexes = [INDEXES_TO_ANALYZE[choice_num - 1]]
                    break
                elif choice_num == len(INDEXES_TO_ANALYZE) + 1:
                    args.indexes = INDEXES_TO_ANALYZE
                    break
                else:
                    print(f"❌ Please enter a number between 1 and {len(INDEXES_TO_ANALYZE) + 1}")
            except ValueError:
                print("❌ Please enter a valid number")
            except KeyboardInterrupt:
                print("\n\n❌ Cancelled by user")
                sys.exit(0)
    
    results = generate_trading_signals(
        indexes_to_analyze=args.indexes,
        prediction_window=args.window,
        start_date=args.start_date,
        end_date=args.end_date,
        force_refresh=args.refresh
    )
    
    # Save results
    json_file = save_signals_to_json(results)
    
    print("\n" + "-"*70)
    print("✅ Trading bot execution complete!")
    print("-"*70)
    
    # Display clickable link to JSON file
    if json_file:
        print(f"\n📄 JSON Report: file:///{json_file.replace(chr(92), '/')}")

if __name__ == '__main__':
    main()
