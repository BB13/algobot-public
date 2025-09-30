#!/usr/bin/env python3
"""
AlgoBot Configuration Viewer

This script displays the current user configuration settings from user_config.yaml
in a readable, organized format. It helps quickly check and verify the current
settings without having to open and parse the YAML file manually.

Usage:
    python config_viewer.py [--section SECTION]

Options:
    --section SECTION    Only display a specific configuration section 
                         (e.g., adapters, logging, trading_parameters)
"""

import os
import yaml
import argparse
import sys
from typing import Dict, Any, Optional, List


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Display AlgoBot configuration settings")
    parser.add_argument(
        "--section",
        type=str,
        help="Only display a specific section of the configuration"
    )
    return parser.parse_args()


def load_config() -> Dict[str, Any]:
    """
    Load the user configuration from user_config.yaml.
    
    Returns:
        Dictionary containing the configuration settings
    """
    config_file = "user_config.yaml"
    
    if not os.path.exists(config_file):
        print(f"Error: Configuration file not found at {config_file}")
        print("Make sure you're running this script from the AlgoBot directory.")
        return {}
    
    try:
        with open(config_file, 'r') as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"Error loading configuration: {str(e)}")
        return {}


def format_value(value: Any, indent: int = 0) -> str:
    """
    Format a configuration value for display.
    
    Args:
        value: The value to format
        indent: Current indentation level
        
    Returns:
        Formatted string representation of the value
    """
    indent_str = "  " * indent
    
    if isinstance(value, dict):
        if not value:
            return "{}"
        
        lines = []
        for k, v in value.items():
            formatted_v = format_value(v, indent + 1)
            if "\n" in formatted_v:
                lines.append(f"{indent_str}  {k}:\n{formatted_v}")
            else:
                lines.append(f"{indent_str}  {k}: {formatted_v}")
        
        return "\n".join(lines)
    
    elif isinstance(value, list):
        if not value:
            return "[]"
        
        if all(isinstance(item, str) for item in value) and len(str(value)) < 70:
            # For simple string lists, show on one line
            return str(value)
        else:
            # For complex or long lists, show item by item
            lines = []
            for item in value:
                formatted_item = format_value(item, indent + 1)
                lines.append(f"{indent_str}  - {formatted_item}")
            return "\n".join(lines)
    
    elif isinstance(value, bool):
        return "Yes" if value else "No"
    
    else:
        return str(value)


def display_section(config: Dict[str, Any], section: str) -> None:
    """
    Display a specific section of the configuration.
    
    Args:
        config: Configuration dictionary
        section: Section name to display
    """
    if section not in config:
        print(f"Section '{section}' not found in configuration.")
        print(f"Available sections: {', '.join(config.keys())}")
        return
    
    print(f"\n===== {section.upper()} =====")
    print(format_value(config[section]))


def display_config(config: Dict[str, Any]) -> None:
    """
    Display the entire configuration in a readable format.
    
    Args:
        config: Configuration dictionary
    """
    if not config:
        print("No configuration data to display.")
        return
    
    # Display main sections in a specific order for better readability
    # Define sections that should be displayed first and in this order
    priority_sections = [
        "adapters", 
        "trading_parameters", 
        "shutdown", 
        "logging", 
        "chart_presets",
        "risk_management",
        "safety"
    ]
    
    # First display priority sections in order
    for section in priority_sections:
        if section in config:
            display_section(config, section)
    
    # Then display any remaining sections
    for section in config:
        if section not in priority_sections:
            display_section(config, section)


def summarize_config(config: Dict[str, Any]) -> None:
    """
    Display a summary of key configuration settings.
    
    Args:
        config: Configuration dictionary
    """
    print("\n===== CONFIGURATION SUMMARY =====")
    
    # Exchange adapter summary
    try:
        adapters = config.get("adapters", {})
        default_adapter = adapters.get("default", "none")
        print(f"Default Exchange: {default_adapter}")
        
        # Binance settings summary
        binance = adapters.get("binance_spot", {})
        if binance:
            enabled = "Enabled" if binance.get("enabled", False) else "Disabled"
            testnet = "Yes (Testing mode)" if binance.get("testnet", True) else "No (Live trading)"
            margin = "Yes" if binance.get("use_margin_for_longs", False) else "No"
            
            print(f"Binance Spot: {enabled}")
            print(f"Testnet: {testnet}")
            print(f"Using Margin: {margin}")
            
            # Trading directions
            directions = binance.get("directions", {})
            if directions:
                allowed_dirs = []
                if directions.get("allow_long", True):
                    allowed_dirs.append("LONG")
                if directions.get("allow_short", False):
                    allowed_dirs.append("SHORT")
                
                print(f"Allowed Directions: {', '.join(allowed_dirs)}")
    except Exception:
        pass
    
    # Trade parameters summary
    try:
        trading = config.get("trading_parameters", {})
        if trading:
            print(f"Default Trade Amount: ${trading.get('default_trade_amount', 'N/A')}")
            print(f"Max Trade Amount: ${trading.get('max_trade_amount', 'N/A')}")
            
            # Stop loss settings
            stop_loss = trading.get("stop_loss", {})
            if stop_loss:
                print(f"Default Stop Loss: {stop_loss.get('percentage', 'N/A')}%")
    except Exception:
        pass
    
    # Shutdown behavior
    try:
        shutdown = config.get("shutdown", {})
        if shutdown:
            close_positions = "Yes" if shutdown.get("close_positions", False) else "No"
            print(f"Close Positions on Shutdown: {close_positions}")
            if close_positions == "Yes":
                print(f"Close Method: {shutdown.get('close_method', 'N/A')}")
    except Exception:
        pass
    
    # Logging level
    try:
        logging = config.get("logging", {})
        if logging:
            print(f"Logging Level: {logging.get('level', 'N/A')}")
    except Exception:
        pass


def main():
    """Main function to run the script."""
    args = parse_arguments()
    
    # Load configuration
    config = load_config()
    if not config:
        return 1
    
    # Display header
    print("\n=== ALGOBOT CONFIGURATION VIEWER ===")
    print(f"Configuration File: {os.path.abspath('user_config.yaml')}")
    
    # Display configuration based on arguments
    if args.section:
        display_section(config, args.section)
    else:
        # Show a summary of key settings first
        summarize_config(config)
        
        # Then show the full configuration
        print("\n===== FULL CONFIGURATION =====")
        print("Use --section to view specific sections")
        display_config(config)
    
    return 0


if __name__ == "__main__":
    sys.exit(main()) 