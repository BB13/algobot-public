#!/usr/bin/env python3
"""
Trade Summary Tool for AlgoBot

This script displays recent trade outcomes and current open positions
from the algobot trading system. It's designed to be run from inside
the algobot directory.

Usage:
    python trade_summary.py [--outcomes COUNT] [--sort COLUMN]

Options:
    --outcomes COUNT       Number of recent outcomes to show (default: 10)
    --sort COLUMN          Sort outcomes by: 'date', 'profit', 'percentage' (default: date)
"""

import os
import json
import csv
import argparse
from datetime import datetime
import sys
from typing import Dict, List, Any, Optional, Tuple
from decimal import Decimal


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Display trade outcomes and open positions")
    parser.add_argument(
        "--outcomes", 
        type=int, 
        default=50,
        help="Number of recent outcomes to show (default: 10)"
    )
    parser.add_argument(
        "--sort", 
        choices=["date", "profit", "percentage"],
        default="date",
        help="Sort outcomes by: 'date', 'profit', 'percentage' (default: date)"
    )
    return parser.parse_args()


def load_trade_outcomes(limit: int, sort_by: str) -> List[Dict[str, Any]]:
    """
    Load recent trade outcomes from the CSV file.
    
    Args:
        limit: Maximum number of outcomes to return
        sort_by: Field to sort by ('date', 'profit', 'percentage')
        
    Returns:
        List of trade outcome dictionaries
    """
    outcomes_file = os.path.join("src", "data", "trade_outcomes.csv")
    if not os.path.exists(outcomes_file):
        print(f"Error: Trade outcomes file not found at {outcomes_file}")
        return []
    
    try:
        outcomes = []
        with open(outcomes_file, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Convert types for better display
                if 'timestamp' in row:
                    try:
                        row['timestamp'] = datetime.fromisoformat(row['timestamp'])
                    except ValueError:
                        pass  # Keep as string if parsing fails
                
                if 'profit' in row:
                    try:
                        row['profit_num'] = float(row['profit'])
                    except ValueError:
                        row['profit_num'] = 0
                
                if 'profit_percentage' in row:
                    try:
                        # Strip the % sign if present
                        row['percentage_num'] = float(row['profit_percentage'].rstrip('%'))
                    except ValueError:
                        row['percentage_num'] = 0
                
                outcomes.append(row)
        
        # Sort based on the requested field
        if sort_by == 'date':
            outcomes.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        elif sort_by == 'profit':
            outcomes.sort(key=lambda x: x.get('profit_num', 0), reverse=True)
        elif sort_by == 'percentage':
            outcomes.sort(key=lambda x: x.get('percentage_num', 0), reverse=True)
        
        # Return only the requested number of outcomes
        return outcomes[:limit]
    
    except Exception as e:
        print(f"Error loading trade outcomes: {str(e)}")
        return []


def load_open_positions() -> Dict[str, List[Dict[str, Any]]]:
    """
    Load current open positions from the JSON file.
    
    Returns:
        Dictionary of open positions by strategy/symbol
    """
    positions_file = os.path.join("src", "data", "open_positions.json")
    if not os.path.exists(positions_file):
        print(f"Error: Open positions file not found at {positions_file}")
        return {}
    
    try:
        with open(positions_file, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading open positions: {str(e)}")
        return {}


def calculate_summary_stats(outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate summary statistics from trade outcomes.
    
    Args:
        outcomes: List of trade outcome dictionaries
        
    Returns:
        Dictionary of summary statistics
    """
    if not outcomes:
        return {
            "count": 0,
            "win_count": 0,
            "win_rate": 0,
            "total_profit": 0,
            "avg_profit": 0,
            "max_profit": 0,
            "max_loss": 0
        }
    
    # Initialize counters
    total_profit = 0
    win_count = 0
    max_profit = float('-inf')
    max_loss = float('inf')
    
    # Process each outcome
    for outcome in outcomes:
        profit = outcome.get('profit_num', 0)
        total_profit += profit
        
        if profit > 0:
            win_count += 1
        
        max_profit = max(max_profit, profit)
        max_loss = min(max_loss, profit)
    
    # Calculate derived statistics
    count = len(outcomes)
    win_rate = (win_count / count) * 100 if count > 0 else 0
    avg_profit = total_profit / count if count > 0 else 0
    
    # Ensure max_loss makes sense if no losses
    if max_loss == float('inf'):
        max_loss = 0
    
    # Ensure max_profit makes sense if no profits
    if max_profit == float('-inf'):
        max_profit = 0
    
    return {
        "count": count,
        "win_count": win_count,
        "win_rate": win_rate,
        "total_profit": total_profit,
        "avg_profit": avg_profit,
        "max_profit": max_profit,
        "max_loss": max_loss
    }


def format_currency(value: float) -> str:
    """Format a value as currency with 2 decimal places."""
    return f"${value:.2f}"


def display_outcomes(outcomes: List[Dict[str, Any]], stats: Dict[str, Any]) -> None:
    """
    Display trade outcomes in a formatted table.
    
    Args:
        outcomes: List of trade outcome dictionaries
        stats: Summary statistics
    """
    if not outcomes:
        print("No trade outcomes found.")
        return

    # Print summary stats
    print("\n===== TRADE SUMMARY =====")
    print(f"Total Trades: {stats['count']}")
    print(f"Win Rate: {stats['win_rate']:.1f}% ({stats['win_count']}/{stats['count']})")
    print(f"Total Profit: {format_currency(stats['total_profit'])}")
    print(f"Average Profit: {format_currency(stats['avg_profit'])}")
    print(f"Best Trade: {format_currency(stats['max_profit'])}")
    print(f"Worst Trade: {format_currency(stats['max_loss'])}")
    
    # Print table of recent trades
    print("\n===== RECENT TRADES =====")
    
    # Define headers and column widths
    headers = ["Date", "Asset", "Direction", "Strategy", "Timeframe", "Profit", "Profit %"]
    col_widths = [19, 10, 8, 8, 9, 10, 10]
    
    # Print header row
    header_row = "".join(f"{headers[i]:<{col_widths[i]}}" for i in range(len(headers)))
    print(header_row)
    print("-" * sum(col_widths))
    
    # Print each outcome row
    for outcome in outcomes:
        date_str = outcome['timestamp'].strftime('%Y-%m-%d %H:%M') if isinstance(outcome['timestamp'], datetime) else str(outcome['timestamp'])[:16]
        
        # Format profit with color indicators (+ for profit, - for loss)
        profit_value = outcome.get('profit_num', 0)
        profit_str = format_currency(profit_value)
        
        # Get other fields with fallbacks
        asset = outcome.get('asset', 'UNKNOWN')
        direction = outcome.get('direction', 'UNKNOWN')
        strategy = outcome.get('bot_strategy', 'UNKNOWN')
        timeframe = outcome.get('timeframe', 'UNKNOWN')
        percentage = outcome.get('profit_percentage', '0%')
        
        # Build and print the row
        row = (
            f"{date_str:<{col_widths[0]}}"
            f"{asset:<{col_widths[1]}}"
            f"{direction:<{col_widths[2]}}"
            f"{strategy:<{col_widths[3]}}"
            f"{timeframe:<{col_widths[4]}}"
            f"{profit_str:<{col_widths[5]}}"
            f"{percentage:<{col_widths[6]}}"
        )
        print(row)


def display_positions(positions: Dict[str, List[Dict[str, Any]]]) -> None:
    """
    Display open positions in a formatted table.
    
    Args:
        positions: Dictionary of open positions by strategy/symbol
    """
    if not positions:
        print("No open positions found.")
        return
    
    total_count = sum(len(pos_list) for pos_list in positions.values())
    total_value = 0
    
    # Collect all positions in a flat list for easier processing
    all_positions = []
    for key, pos_list in positions.items():
        for pos in pos_list:
            # Calculate position value
            try:
                initial_qty = float(pos.get('initial_quantity', '0'))
                remaining_qty = float(pos.get('remaining_quantity', '0'))
                entry_price = float(pos.get('entry_price', '0'))
                
                # Calculate current position value
                position_value = remaining_qty * entry_price
                total_value += position_value
                
                # Extract strategy, settings and timeframe from the key
                key_parts = key.split('_')
                if len(key_parts) >= 3:
                    pos['strategy'] = key_parts[0]
                    pos['settings'] = key_parts[1]
                    pos['timeframe'] = key_parts[2]
                
                # Add position value to the position data
                pos['position_value'] = position_value
                
                all_positions.append(pos)
            except (ValueError, TypeError):
                # Skip positions with invalid data
                continue
    
    # Print summary
    print("\n===== OPEN POSITIONS =====")
    print(f"Total Positions: {total_count}")
    print(f"Total Value: {format_currency(total_value)}")
    
    if not all_positions:
        return
    
    # Define headers and column widths
    headers = ["Asset", "Direction", "Strategy", "Timeframe", "Entry Price", "Quantity", "Value"]
    col_widths = [10, 9, 8, 9, 12, 10, 12]
    
    # Print header row
    header_row = "".join(f"{headers[i]:<{col_widths[i]}}" for i in range(len(headers)))
    print("\n" + header_row)
    print("-" * sum(col_widths))
    
    # Print each position row
    for pos in all_positions:
        asset = pos.get('asset', 'UNKNOWN')
        direction = pos.get('direction', 'UNKNOWN')
        strategy = pos.get('strategy', 'UNKNOWN')
        timeframe = pos.get('timeframe', 'UNKNOWN')
        
        # Format numeric values
        entry_price = f"${float(pos.get('entry_price', '0')):.2f}"
        remaining_qty = float(pos.get('remaining_quantity', '0'))
        position_value = pos.get('position_value', 0)
        
        # Build and print the row
        row = (
            f"{asset:<{col_widths[0]}}"
            f"{direction:<{col_widths[1]}}"
            f"{strategy:<{col_widths[2]}}"
            f"{timeframe:<{col_widths[3]}}"
            f"{entry_price:<{col_widths[4]}}"
            f"{remaining_qty:<{col_widths[5]}.4f}"
            f"{format_currency(position_value):<{col_widths[6]}}"
        )
        print(row)


def main():
    """Main function to run the script."""
    args = parse_arguments()
    
    # Verify we're in the AlgoBot directory by checking for expected directories
    if not os.path.exists("src") or not os.path.exists("src/data"):
        print("Error: This script must be run from the AlgoBot directory.")
        print("Current directory:", os.getcwd())
        print("Please change to the AlgoBot directory and try again.")
        return 1
    
    # Load data
    outcomes = load_trade_outcomes(args.outcomes, args.sort)
    positions = load_open_positions()
    
    # Calculate summary statistics
    stats = calculate_summary_stats(outcomes)
    
    # Display results
    print("\n=== ALGOBOT TRADE SUMMARY ===")
    print(f"Working Directory: {os.getcwd()}")
    print(f"Report Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    display_outcomes(outcomes, stats)
    display_positions(positions)
    
    return 0


if __name__ == "__main__":
    sys.exit(main()) 