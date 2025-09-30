"""
Configuration settings for the trading bot.
"""
import os
import json
import yaml
from decimal import Decimal
from typing import Dict, Any, Optional, List

import logging
logger = logging.getLogger(__name__)

# --- Load Environment Variables --- 
# Load .env file if present
from dotenv import load_dotenv
load_dotenv()

# --- Load User Config ---
# Path to user_config.yaml
USER_CONFIG_PATH = os.path.join(os.getcwd(), "user_config.yaml")

# --- Load Default Config ---
# Default configuration values
_DEFAULT_CONFIG = {
    "trading_parameters": {
        "default_trade_amount": 1000,
        "max_trade_amount": 1000,
        "take_profits": {
            "three_level": {1: 33, 2: 50, 3: 100},
            "four_level": {1: 25, 2: 33, 3: 50, 4: 100}
        },
        "stop_loss": {
            "percentage": 3,
            "max_percentage": 10,
            "long_term_trade_hrs": 72
        },
        "safety": {
            "check_interval": 60
        }
    },
    "shutdown": {
        "close_positions": False,
        "close_method": "virtual"
    }
}

# Load user configuration from YAML file
_user_config = {}
try:
    if os.path.exists(USER_CONFIG_PATH):
        with open(USER_CONFIG_PATH, 'r') as f:
            _user_config = yaml.safe_load(f)
            logger.info(f"Loaded user configuration from {USER_CONFIG_PATH}")
    else:
        logger.warning(f"User config file not found at {USER_CONFIG_PATH}, using defaults")
except Exception as e:
    logger.error(f"Error loading user config: {str(e)}")

# Helper function to get value from user config with fallback
def get_config_value(keys_path, default_value):
    """
    Get a value from user config with fallback to default.
    
    Args:
        keys_path: List of keys to navigate to the value
        default_value: Default value if not found in user config
        
    Returns:
        The value from user config or default
    """
    current = _user_config
    for key in keys_path:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default_value
    return current

# Core Secrets (Required - loaded from environment with empty defaults)
BINANCE_SPOT_API_KEY = os.getenv('BINANCE_SPOT_API_KEY', '')
BINANCE_SPOT_API_SECRET = os.getenv('BINANCE_SPOT_API_SECRET', '')
TELEGRAM_SECRET = os.getenv('TELEGRAM_SECRET', '')
WEBHOOK_API_KEY = os.getenv('WEBHOOK_API_KEY', '')

APPROVED_CHAT_IDS = ['7122758518']
# Default trading parameters - load from user config with fallbacks
DEFAULT_TRADE_AMOUNT = Decimal(str(get_config_value(
    ['trading_parameters', 'default_trade_amount'],
    _DEFAULT_CONFIG['trading_parameters']['default_trade_amount']
)))

MAX_TRADE_AMOUNT = Decimal(str(get_config_value(
    ['trading_parameters', 'max_trade_amount'],
    _DEFAULT_CONFIG['trading_parameters']['max_trade_amount']
)))

# Default take profit configurations - load from user config with fallbacks
DEFAULT_TP_PERCENTAGES_3 = get_config_value(
    ['trading_parameters', 'take_profits', 'three_level'],
    _DEFAULT_CONFIG['trading_parameters']['take_profits']['three_level']
)

DEFAULT_TP_PERCENTAGES_4 = get_config_value(
    ['trading_parameters', 'take_profits', 'four_level'],
    _DEFAULT_CONFIG['trading_parameters']['take_profits']['four_level']
)

# Stop loss settings - load from user config with fallbacks
STOP_LOSS_PERCENTAGE = get_config_value(
    ['trading_parameters', 'stop_loss', 'percentage'],
    _DEFAULT_CONFIG['trading_parameters']['stop_loss']['percentage']
)

MAX_STOP_LOSS_PERCENTAGE = get_config_value(
    ['trading_parameters', 'stop_loss', 'max_percentage'],
    _DEFAULT_CONFIG['trading_parameters']['stop_loss']['max_percentage']
)

LONG_TERM_TRADE_HRS = get_config_value(
    ['trading_parameters', 'stop_loss', 'long_term_trade_hrs'],
    _DEFAULT_CONFIG['trading_parameters']['stop_loss']['long_term_trade_hrs']
)

# Safety measures interval - load from user config with fallbacks
SAFETY_MEASURES_INTERVAL = get_config_value(
    ['trading_parameters', 'safety', 'check_interval'],
    _DEFAULT_CONFIG['trading_parameters']['safety']['check_interval']
)

# Shutdown behavior settings
CLOSE_POSITIONS_ON_SHUTDOWN = get_config_value(
    ['shutdown', 'close_positions'],
    _DEFAULT_CONFIG['shutdown']['close_positions']
)

SHUTDOWN_CLOSE_METHOD = get_config_value(
    ['shutdown', 'close_method'],
    _DEFAULT_CONFIG['shutdown']['close_method']
)

# --- Load Chart Presets ---
CHART_PRESETS = get_config_value(['chart_presets'], {}) # Load chart presets, default to empty dict
if not isinstance(CHART_PRESETS, dict):
    logger.warning(f"Invalid format for chart_presets in user_config.yaml. Expected a dictionary, got {type(CHART_PRESETS)}. Using empty presets.")
    CHART_PRESETS = {}
else:
    logger.info(f"Loaded {len(CHART_PRESETS)} chart presets from user config.")

# File paths (relative to project root assumed)
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
POSITIONS_FILE = os.path.join(DATA_DIR, "open_positions.json")
CLOSED_POSITIONS_FILE = os.path.join(DATA_DIR, "closed_positions.json")
TRADE_OUTCOMES_FILE = os.path.join(DATA_DIR, "trade_outcomes.csv")
TELEGRAM_USERS_FILE = os.path.join(DATA_DIR, "telegram_users.json") # Still used by telegram_users.py?

# Telegram settings
TELEGRAM_BOT_TOKEN = TELEGRAM_SECRET # Use the variable loaded from env
TELEGRAM_ADMIN_CHAT_ID = APPROVED_CHAT_IDS[0] if APPROVED_CHAT_IDS else None
TELEGRAM_NOTIFICATION_ENABLED = True # Can be overridden by env var if needed
TELEGRAM_INLINE_BUTTONS = True

# Chart integration settings (loaded from environment with defaults)
ENABLE_CHART_SNAPSHOTS = os.getenv("ENABLE_CHART_SNAPSHOTS", "True").lower() == "true"
SEND_CHART_ON_NEW_POSITION = os.getenv("SEND_CHART_ON_NEW_POSITION", "False").lower() == "true"
MULTI_COIN_CHARTS_URL_TEMPLATE = os.getenv(
    "MULTI_COIN_CHARTS_URL_TEMPLATE", 
    "https://www.multicoincharts.com/?c={ASSETS}&p=1&s=1#nav"
)

# --- REMOVED TradingView specific configs ---

# Asset Short Name Mapping (fixed for now)
ASSET_SHORTNAME_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "LINK": "LINKUSDT",
    "XRP": "XRPUSDT",
    "ALGO": "ALGOUSDT",
    "GMT": "GMTUSDT",
    "LUNA": "LUNAUSDT",
    "DOGE": "DOGEUSDT",
}

# Log directory setup
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Server settings
HOST = "0.0.0.0"
# HOST = "127.0.0.1"
PORT = 5000

# Create data directory if it doesn't exist
os.makedirs(DATA_DIR, exist_ok=True)

# --- Simple function to get Binance creds --- 
def get_binance_credentials() -> Dict[str, Optional[str]]:
    """Returns the loaded Binance API credentials."""
    return {
        'api_key': BINANCE_SPOT_API_KEY,
        'api_secret': BINANCE_SPOT_API_SECRET
    }

# --- Get adapter settings from user config ---
def get_adapter_settings(adapter_id: str = None) -> Dict[str, Any]:
    """
    Get settings for a specific adapter or the default adapter.
    
    Args:
        adapter_id: Adapter identifier (e.g., 'binance_spot')
        
    Returns:
        Dictionary of adapter settings
    """
    adapters_config = get_config_value(['adapters'], {})
    
    # If no adapter specified, get the default
    if adapter_id is None:
        default_adapter = adapters_config.get('default', 'binance_spot')
        adapter_id = default_adapter
    
    # Get settings for the specified adapter
    adapter_settings = adapters_config.get(adapter_id, {})
    
    return adapter_settings

# --- Validate required secrets --- 
if not BINANCE_SPOT_API_KEY or not BINANCE_SPOT_API_SECRET:
    logger.warning("Binance API Key or Secret not found in environment variables.")
if not TELEGRAM_SECRET:
    logger.warning("Telegram Secret not found in environment variables.")


class TradingConfig:
    """
    Configuration for trading parameters.
    
    This class loads and manages trading configuration from a config file
    and provides access to exchange-specific and strategy-specific settings.
    """
    
    def __init__(self, config_file: Optional[str] = None):
        """
        Initialize trading configuration.
        
        Args:
            config_file: Path to configuration file (JSON)
        """
        self.config = {}
        
        if config_file and os.path.exists(config_file):
            with open(config_file, 'r') as f:
                self.config = json.load(f)
        
    def get_exchange_config(self, exchange_id: str) -> Dict[str, Any]:
        """
        Get configuration for a specific exchange.
        
        Args:
            exchange_id: Exchange identifier
            
        Returns:
            Exchange-specific configuration settings
        """
        return self.config.get("exchanges", {}).get(exchange_id, {})
    
    def get_strategy_config(self, bot_strategy: str) -> Dict[str, Any]:
        """
        Get configuration for a specific strategy.
        
        Args:
            bot_strategy: Strategy identifier
            
        Returns:
            Strategy-specific configuration settings
        """
        return self.config.get("strategies", {}).get(bot_strategy, {})
    
    def get_take_profit_config(self, bot_strategy: str, max_tp: int = 3) -> Dict[int, int]:
        """
        Get take profit configuration for a specific strategy.
        
        Args:
            bot_strategy: Strategy identifier
            max_tp: Maximum number of take profits
            
        Returns:
            Take profit configuration mapping levels to percentages
        """
        strategy = self.get_strategy_config(bot_strategy)
        tp_config = strategy.get("take_profits", {})
        
        # If no strategy-specific config found, use default
        if not tp_config:
            return DEFAULT_TP_PERCENTAGES_4 if max_tp == 4 else DEFAULT_TP_PERCENTAGES_3
        
        return tp_config
    
    def get_api_keys(self, exchange_id: str) -> Dict[str, str]:
        """
        Get API keys for a specific exchange.
        
        Args:
            exchange_id: Exchange identifier
            
        Returns:
            API key configuration with 'api_key' and 'api_secret'
        """
        exchange_config = self.get_exchange_config(exchange_id)
        
        # First try to get from config
        api_key = exchange_config.get("api_key")
        api_secret = exchange_config.get("api_secret")
        
        # If not found, try environment variables
        if not api_key:
            api_key = os.environ.get(f"{exchange_id.upper()}_API_KEY")
        
        if not api_secret:
            api_secret = os.environ.get(f"{exchange_id.upper()}_API_SECRET")
        
        return {
            "api_key": api_key,
            "api_secret": api_secret
        }
    
    def save(self, config_file: str) -> None:
        """
        Save the current configuration to a file.
        
        Args:
            config_file: Path to save the configuration
        """
        with open(config_file, 'w') as f:
            json.dump(self.config, f, indent=2)
    
    def update_strategy_config(self, bot_strategy: str, config: Dict[str, Any]) -> None:
        """
        Update configuration for a specific strategy.
        
        Args:
            bot_strategy: Strategy identifier
            config: New configuration settings
        """
        if "strategies" not in self.config:
            self.config["strategies"] = {}
        
        self.config["strategies"][bot_strategy] = config
    
    def update_exchange_config(self, exchange_id: str, config: Dict[str, Any]) -> None:
        """
        Update configuration for a specific exchange.
        
        Args:
            exchange_id: Exchange identifier
            config: New configuration settings
        """
        if "exchanges" not in self.config:
            self.config["exchanges"] = {}
        
        self.config["exchanges"][exchange_id] = config


# Default configuration instance
trading_config = TradingConfig()


