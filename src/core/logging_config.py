"""
Logging configuration for the trading bot.
"""
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from typing import Dict, Any, Optional

# Get configuration values from user_config
from .config import get_config_value

# Simple console logging for debugging purpose
print(f"Current working directory: {os.getcwd()}")
print(f"Current script path: {os.path.abspath(__file__)}")

# Determine project root more reliably
# Get the absolute path of the current file (logging_config.py)
current_file = os.path.abspath(__file__)
# Get src/core directory
core_dir = os.path.dirname(current_file)
# Get src directory
src_dir = os.path.dirname(core_dir)
# Get project root directory
project_root = os.path.dirname(src_dir)
# Logs directory is in the project root
LOG_DIR = os.path.join(project_root, "logs")

print(f"Logs directory path: {LOG_DIR}")
os.makedirs(LOG_DIR, exist_ok=True)

# Default logging settings
DEFAULT_LOG_LEVEL = "WARNING"
DEFAULT_MAX_SIZE_MB = 10  # 10 MB
DEFAULT_BACKUP_COUNT = 5
DEFAULT_ORDER_BACKUP_COUNT = 10
DEFAULT_APP_LOG_PATH = "app.log"
DEFAULT_ORDER_LOG_PATH = "orders.log"

# Configure main application logger
def configure_logging(log_level: Optional[str] = None) -> None:
    """
    Configure the root logger with console and rotating file handlers.
    
    Args:
        log_level: Optional logging level to override the config
    """
    # Get log settings from config
    config_log_level = get_config_value(['logging', 'level'], DEFAULT_LOG_LEVEL)
    max_size_mb = get_config_value(['logging', 'rotation', 'max_size_mb'], DEFAULT_MAX_SIZE_MB)
    backup_count = get_config_value(['logging', 'rotation', 'backup_count'], DEFAULT_BACKUP_COUNT)
    app_log_path = get_config_value(['logging', 'paths', 'general'], DEFAULT_APP_LOG_PATH)
    
    # Use override level if provided
    log_level = log_level or config_log_level
    
    # Convert string log level to logging constant
    numeric_level = getattr(logging, log_level.upper(), logging.WARNING)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    
    # Remove existing handlers to avoid duplicates during reconfiguration
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Add console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    root_logger.addHandler(console_handler)
    
    # Add rotating file handler for main log - ensure we're not double-joining "logs" directory
    # If app_log_path already includes 'logs/', strip it to avoid duplicates
    if app_log_path.startswith('logs/'):
        app_log_path = app_log_path[5:]  # Remove 'logs/' prefix
    
    main_log_file = os.path.join(LOG_DIR, app_log_path)
    file_handler = RotatingFileHandler(
        main_log_file,
        maxBytes=max_size_mb * 1024 * 1024,  # Convert MB to bytes
        backupCount=backup_count
    )
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    root_logger.addHandler(file_handler)
    
    # Set repositories logger to a lower level to reduce file writes
    # This should reduce the number of log entries causing file rotation and backups
    logging.getLogger("src.repositories").setLevel(logging.WARNING)  # Changed from INFO to WARNING
    
    # Set specific loggers to more verbose levels
    logging.getLogger("src.tasks").setLevel(logging.INFO)
    
    # Reduce verbosity for some loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    
    # Configure order logger
    configure_order_logger()

# Configure dedicated order logger
def configure_order_logger() -> None:
    """
    Configure a dedicated logger for order tracking.
    """
    # Get order log settings from config
    order_backup_count = get_config_value(['logging', 'rotation', 'order_backup_count'], DEFAULT_ORDER_BACKUP_COUNT)
    max_size_mb = get_config_value(['logging', 'rotation', 'max_size_mb'], DEFAULT_MAX_SIZE_MB)
    order_log_path = get_config_value(['logging', 'paths', 'orders'], DEFAULT_ORDER_LOG_PATH)
    
    # Create order logger
    order_logger = logging.getLogger("order_tracker")
    order_logger.setLevel(logging.INFO)
    
    # Remove existing handlers to avoid duplicates
    for handler in order_logger.handlers[:]:
        order_logger.removeHandler(handler)
    
    # If order_log_path already includes 'logs/', strip it to avoid duplicates
    if order_log_path.startswith('logs/'):
        order_log_path = order_log_path[5:]  # Remove 'logs/' prefix
    
    # Create a dedicated rotating file handler for orders
    order_log_file = os.path.join(LOG_DIR, order_log_path)
    order_handler = RotatingFileHandler(
        order_log_file,
        maxBytes=max_size_mb * 1024 * 1024,
        backupCount=order_backup_count
    )
    
    # Use a simpler format for order logs
    order_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    )
    order_handler.setFormatter(order_formatter)
    
    # Add the handler to the order logger
    order_logger.addHandler(order_handler)
    
    # Prevent order logs from propagating to the root logger
    order_logger.propagate = False

# Get the order logger
def get_order_logger() -> logging.Logger:
    """
    Get the dedicated order logger.
    
    Returns:
        Logger configured for order tracking
    """
    return logging.getLogger("order_tracker")

# Log an incoming signal
def log_incoming_signal(signal_data: Dict[str, Any]) -> None:
    """
    Log an incoming trading signal.
    
    Args:
        signal_data: The signal data received
    """
    order_logger = get_order_logger()
    
    # Extract key information
    command = signal_data.get("command", "UNKNOWN")
    asset = signal_data.get("asset", "UNKNOWN")
    bot = signal_data.get("bot", "UNKNOWN")
    interval = signal_data.get("interval", "UNKNOWN")
    
    # Log the signal
    order_logger.info(
        f"SIGNAL RECEIVED | Command: {command} | Asset: {asset} | "
        f"Strategy: {bot} | Interval: {interval}"
    )

# Log order execution
def log_order_execution(
    order_type: str,
    asset: str,
    direction: str,
    quantity: str,
    price: str,
    order_id: str,
    success: bool,
    details: Optional[str] = None,
    margin_details: Optional[str] = None
) -> None:
    """
    Log an order execution.
    
    Args:
        order_type: Type of order (ENTRY, TP, STOP, etc.)
        asset: Asset symbol
        direction: Order direction (BUY, SELL)
        quantity: Order quantity
        price: Order price
        order_id: Exchange order ID
        success: Whether the order was successful
        details: Additional details (optional)
        margin_details: Margin-specific info like sideEffectType (optional)
    """
    order_logger = get_order_logger()
    
    status = "SUCCESS" if success else "FAILED"
    log_message = (
        f"ORDER {status} | Type: {order_type} | Asset: {asset} | Direction: {direction} | "
        f"Quantity: {quantity} | Price: {price} | Order ID: {order_id}"
    )
    
    # Add margin details if present
    if margin_details:
        log_message += f" | Margin: {margin_details}"
    
    if details:
        log_message += f" | Details: {details}"
    
    log_level = logging.INFO if success else logging.ERROR
    order_logger.log(log_level, log_message)

# Log position update
def log_position_update(
    position_id: str,
    asset: str,
    direction: str,
    status: str,
    update_type: str,
    details: Optional[str] = None,
    leverage: Optional[str] = None,
    margin_type: Optional[str] = None
) -> None:
    """
    Log a position update.
    
    Args:
        position_id: Position ID
        asset: Asset symbol
        direction: Position direction (LONG, SHORT)
        status: Position status
        update_type: Type of update (OPEN, CLOSE, TP, etc.)
        details: Additional details (optional)
        leverage: Leverage used (e.g., "3x") (optional)
        margin_type: Margin type used (e.g., "CROSSED") (optional)
    """
    order_logger = get_order_logger()
    
    log_message = (
        f"POSITION UPDATE | Type: {update_type} | ID: {position_id} | "
        f"Asset: {asset} | Direction: {direction} | Status: {status}"
    )
    
    # Add leverage and margin type if present
    if leverage and leverage != '1':  # Don't log leverage=1x
        log_message += f" | Leverage: {leverage}x"  # Add 'x' suffix
    if margin_type:
        log_message += f" | Margin: {margin_type}"
    
    if details:
        log_message += f" | Details: {details}"
    
    order_logger.info(log_message) 