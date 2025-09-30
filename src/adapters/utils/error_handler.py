"""
Error handling utility for API requests.

This module provides error handling capabilities for API requests, categorizing
errors and implementing automatic retry logic for transient errors.
"""
import asyncio
import logging
import random
import time
import functools
from typing import Callable, Any, Dict, List, Optional, Type, Union, Tuple
from binance.exceptions import BinanceAPIException

logger = logging.getLogger(__name__)

# Error categories
class ErrorCategory:
    """Categories of errors for different handling strategies."""
    NETWORK = "network"          # Network connectivity issues
    RATE_LIMIT = "rate_limit"    # Rate limiting errors
    AUTH = "authentication"      # Authentication/authorization errors
    VALIDATION = "validation"    # Input validation errors
    SERVER = "server"            # Server-side errors
    UNKNOWN = "unknown"          # Uncategorized errors


# Map Binance error codes to categories
BINANCE_ERROR_CATEGORIES = {
    # Rate limit errors
    429: ErrorCategory.RATE_LIMIT,
    -1003: ErrorCategory.RATE_LIMIT,  # Too many requests
    -1015: ErrorCategory.RATE_LIMIT,  # Too many requests, IP banned
    
    # Authentication errors
    -2008: ErrorCategory.AUTH,  # API key invalid
    -2014: ErrorCategory.AUTH,  # API key expired
    -2015: ErrorCategory.AUTH,  # Invalid API key format
    
    # Validation errors
    -1010: ErrorCategory.VALIDATION,  # Error in parameter
    -1021: ErrorCategory.VALIDATION,  # Timestamp outside acceptable range
    -1022: ErrorCategory.VALIDATION,  # Invalid signature
    -1100: ErrorCategory.VALIDATION,  # Illegal characters
    -1101: ErrorCategory.VALIDATION,  # Too many parameters
    -1102: ErrorCategory.VALIDATION,  # Mandatory parameter missing
    -1104: ErrorCategory.VALIDATION,  # Not all sent parameters recognized
    -1105: ErrorCategory.VALIDATION,  # Parameter empty
    -1106: ErrorCategory.VALIDATION,  # Parameter not required
    -1112: ErrorCategory.VALIDATION,  # No depth provided
    -1116: ErrorCategory.VALIDATION,  # Invalid order type
    -1117: ErrorCategory.VALIDATION,  # Invalid side
    -1118: ErrorCategory.VALIDATION,  # New client order ID empty
    -1119: ErrorCategory.VALIDATION,  # Original client order ID empty
    -1120: ErrorCategory.VALIDATION,  # Invalid interval
    -1121: ErrorCategory.VALIDATION,  # Invalid symbol
    -1125: ErrorCategory.VALIDATION,  # Invalid listen key
    -1127: ErrorCategory.VALIDATION,  # More than XX hours
    -1128: ErrorCategory.VALIDATION,  # Combination of params invalid
    -1130: ErrorCategory.VALIDATION,  # Invalid data sent
    -2010: ErrorCategory.VALIDATION,  # New order rejected
    -2011: ErrorCategory.VALIDATION,  # Cancel rejected
    -2013: ErrorCategory.VALIDATION,  # No such order
    -2019: ErrorCategory.VALIDATION,  # Margin not enough
    
    # Server errors
    -1000: ErrorCategory.SERVER,  # Unknown error
    -1001: ErrorCategory.SERVER,  # Disconnected, try again
    -1002: ErrorCategory.SERVER,  # Unauthorized
    -1006: ErrorCategory.SERVER,  # Unexpected response from server
    -1007: ErrorCategory.SERVER,  # Timeout
    -1016: ErrorCategory.SERVER,  # Service shutting down
    -1020: ErrorCategory.SERVER,  # Unsupported operation
    -1041: ErrorCategory.SERVER,  # Current network is unstable
    -1131: ErrorCategory.SERVER,  # Recvwindow is too large
}

# Retry configuration per error category
RETRY_CONFIG = {
    ErrorCategory.NETWORK: {
        "max_retries": 5,
        "base_delay": 1.0,
        "max_delay": 30.0,
        "jitter": 0.25,
    },
    ErrorCategory.RATE_LIMIT: {
        "max_retries": 3,
        "base_delay": 2.0,
        "max_delay": 60.0,
        "jitter": 0.1,
    },
    ErrorCategory.SERVER: {
        "max_retries": 3,
        "base_delay": 2.0,
        "max_delay": 15.0,
        "jitter": 0.2,
    },
    ErrorCategory.AUTH: {
        "max_retries": 0,  # Don't retry auth errors
        "base_delay": 0,
        "max_delay": 0,
        "jitter": 0,
    },
    ErrorCategory.VALIDATION: {
        "max_retries": 0,  # Don't retry validation errors
        "base_delay": 0,
        "max_delay": 0,
        "jitter": 0,
    },
    ErrorCategory.UNKNOWN: {
        "max_retries": 1,
        "base_delay": 2.0,
        "max_delay": 5.0,
        "jitter": 0.1,
    },
}


def categorize_error(error: Exception) -> str:
    """
    Categorize an error based on its type and attributes.
    
    Args:
        error: The exception to categorize
        
    Returns:
        Error category string
    """
    # Network errors
    if isinstance(error, (ConnectionError, TimeoutError)):
        return ErrorCategory.NETWORK
        
    # Binance API errors
    if isinstance(error, BinanceAPIException):
        # Get error code
        error_code = getattr(error, 'code', 0)
        
        # Look up category or default to UNKNOWN
        return BINANCE_ERROR_CATEGORIES.get(error_code, ErrorCategory.UNKNOWN)
    
    # Other errors are considered unknown
    return ErrorCategory.UNKNOWN


def calculate_retry_delay(retry_count: int, config: Dict[str, Any]) -> float:
    """
    Calculate delay before retry using exponential backoff with jitter.
    
    Args:
        retry_count: The current retry attempt (0-based)
        config: Retry configuration dictionary
        
    Returns:
        Delay in seconds
    """
    base_delay = config["base_delay"]
    max_delay = config["max_delay"]
    jitter = config["jitter"]
    
    # Calculate exponential backoff
    delay = min(max_delay, base_delay * (2 ** retry_count))
    
    # Add jitter
    jitter_amount = delay * jitter
    delay = delay + random.uniform(-jitter_amount, jitter_amount)
    
    return max(0, delay)  # Ensure positive delay


def log_error(error: Exception, retry_count: Optional[int] = None, delay: Optional[float] = None) -> None:
    """
    Log error with appropriate level and context.
    
    Args:
        error: The exception that occurred
        retry_count: Current retry count if retrying
        delay: Delay before retry
    """
    category = categorize_error(error)
    
    # Build log message
    message = f"Error ({category}): {str(error)}"
    
    if retry_count is not None:
        retry_msg = f"Retry {retry_count + 1}"
        if delay is not None:
            retry_msg += f" in {delay:.2f}s"
        message += f" - {retry_msg}"
    
    # Choose log level based on error category and retry status
    if category in (ErrorCategory.VALIDATION, ErrorCategory.AUTH):
        logger.error(message)
    elif category == ErrorCategory.RATE_LIMIT:
        logger.warning(message)
    elif retry_count is not None:
        # For retries, use info level
        logger.info(message)
    else:
        # For other errors without retry, use warning
        logger.warning(message)
    
    # Log additional details for certain error types
    if isinstance(error, BinanceAPIException):
        logger.debug(f"Binance API Error Details - Code: {getattr(error, 'code', 'unknown')}, "
                    f"Message: {getattr(error, 'message', 'unknown')}")


def with_retries(
    retry_for: Optional[List[Union[str, Type[Exception]]]] = None,
    exclude: Optional[List[Union[str, Type[Exception]]]] = None
):
    """
    Decorator to add retry capability to a method.
    
    Args:
        retry_for: List of error categories or exception types to retry
        exclude: List of error categories or exception types to exclude from retry
        
    Returns:
        Decorated method with retry logic
    """
    if retry_for is None:
        # Default to retrying network, rate limit, and server errors
        retry_for = [ErrorCategory.NETWORK, ErrorCategory.RATE_LIMIT, ErrorCategory.SERVER]
    
    if exclude is None:
        exclude = []
        
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            retry_count = 0
            
            while True:
                try:
                    return await func(*args, **kwargs)
                except Exception as error:
                    category = categorize_error(error)
                    config = RETRY_CONFIG.get(category, RETRY_CONFIG[ErrorCategory.UNKNOWN])
                    
                    # Check if we should retry this error
                    should_retry = False
                    
                    # Check if error type or category is in retry_for list
                    if category in retry_for or type(error) in retry_for:
                        should_retry = True
                    
                    # Check if error type or category is in exclude list
                    if category in exclude or type(error) in exclude:
                        should_retry = False
                    
                    # Check if we've exceeded max retries
                    if retry_count >= config["max_retries"]:
                        should_retry = False
                    
                    if should_retry:
                        # Calculate delay
                        delay = calculate_retry_delay(retry_count, config)
                        
                        # Log the error and retry attempt
                        log_error(error, retry_count, delay)
                        
                        # Wait before retry
                        await asyncio.sleep(delay)
                        
                        # Increment retry counter
                        retry_count += 1
                    else:
                        # Log error without retry
                        log_error(error)
                        raise
        
        return wrapper
    
    return decorator


# Combined decorator that applies both retries and rate limiting
def api_request(
    endpoint: Optional[str] = None,
    weight: int = 1,
    retry_for: Optional[List[Union[str, Type[Exception]]]] = None,
    exclude: Optional[List[Union[str, Type[Exception]]]] = None
):
    """
    Combined decorator for API requests with rate limiting and retries.
    
    This is a convenience decorator that combines rate_limited and with_retries.
    
    Args:
        endpoint: API endpoint category for rate limiting
        weight: Request weight for rate limiting
        retry_for: List of error categories or exception types to retry
        exclude: List of error categories or exception types to exclude from retry
        
    Returns:
        Decorated method
    """
    # Import here to avoid circular import
    from .rate_limiter import rate_limited
    
    def decorator(func):
        # Apply retries first, then rate limiting
        @rate_limited(endpoint=endpoint, weight=weight)
        @with_retries(retry_for=retry_for, exclude=exclude)
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        return wrapper
    
    return decorator
