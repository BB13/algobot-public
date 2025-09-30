"""
Binance exchange adapter implementation supporting both spot and futures trading.
"""
import math
import logging
import asyncio
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional, Any, Tuple, Union
from datetime import datetime, timedelta
from functools import wraps
import json
from enum import Enum
from abc import ABC, abstractmethod

from binance.client import Client
from binance.exceptions import BinanceAPIException

from .utils.rate_limiter import RateLimiter
from .utils.error_handler import api_request, ErrorCategory
from .utils.connection_manager import BinanceConnectionManager

from ..core.asset import Asset
from ..core.position import Position, PositionDirection
from ..core.exchange_adapter import ExchangeAdapter
from ..core.config import BINANCE_SPOT_API_KEY, BINANCE_SPOT_API_SECRET


logger = logging.getLogger(__name__)


class BinanceTradeMode(str, Enum):
    """Represents the trading mode for Binance."""
    SPOT = "spot"
    FUTURES = "futures"


class BinanceAdapter(ExchangeAdapter, ABC):
    """
    Base Binance adapter implementing common functionality for both spot and futures trading.
    """
    
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None, testnet: bool = False):
        """
        Initialize the Binance adapter.
        
        Args:
            api_key: Binance API key (defaults to environment variable)
            api_secret: Binance API secret (defaults to environment variable)
            testnet: Whether to use testnet
        """
        self.api_key = api_key or BINANCE_SPOT_API_KEY
        self.api_secret = api_secret or BINANCE_SPOT_API_SECRET
        self.testnet = testnet
        
        if not self.api_key or not self.api_secret:
            raise ValueError("Binance API credentials not provided")
        
        # Initialize connection manager
        self.connection_manager = BinanceConnectionManager(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=self.testnet
        )
        
        # Initialize rate limiter
        self.rate_limiter = RateLimiter()
        
        # Initialize client - each subclass must set the appropriate client
        self.client = None
        
        # Initialize caches
        self._asset_info_cache = {}  # Cache for asset information
        self._price_cache = {}       # Cache for prices
        self._price_cache_time = {}  # Cache for when prices were last updated
        self._price_cache_lock = asyncio.Lock()  # Lock for thread-safe cache access
        
        logger.info(f"{self.__class__.__name__} initialized")
    
    async def initialize(self):
        """Initialize async components like the connection manager."""
        await self.connection_manager.start()
        logger.info(f"{self.__class__.__name__} connection manager started")
    
    async def shutdown(self):
        """Shutdown async components and clean up resources."""
        await self.connection_manager.stop()
        logger.info(f"{self.__class__.__name__} connection manager stopped")
        
    async def _execute_request(self, func_name: str, *args, **kwargs):
        """
        Execute a request using the connection manager.
        
        Args:
            func_name: Name of the client method to call
            *args: Positional arguments for the method
            **kwargs: Keyword arguments for the method
            
        Returns:
            Result of the client method call
        """
        client = await self.connection_manager.get_client()
        try:
            func = getattr(client, func_name)
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: func(*args, **kwargs)
            )
            return result
        finally:
            await self.connection_manager.release_client(client)
    
    def _calculate_precision(self, step_size_str: str) -> int:
        """
        Calculate decimal precision from step size.
        
        Args:
            step_size_str: Step size as string
            
        Returns:
            Number of decimal places
        """
        step_size = Decimal(step_size_str)
        if step_size == 0:
            return 0
            
        return int(round(-math.log(float(step_size), 10), 0))
    
    @api_request(endpoint="market_data", weight=2)
    async def get_multiple_prices(self, symbols: List[str]) -> Dict[str, Decimal]:
        """
        Get current market prices for multiple assets.
        
        Args:
            symbols: List of asset symbols (e.g., ["BTCUSDT", "ETHUSDT"])
            
        Returns:
            Dictionary mapping symbol to its current price as a Decimal
            
        Raises:
            ValueError: If symbols list is empty
            Exception: If API call fails
        """
        if not symbols:
            raise ValueError("Symbols list cannot be empty for get_multiple_prices")
            
        # Check cache for any already available recent prices
        prices_from_cache = {}
        symbols_to_fetch = []
        current_time = datetime.now()
        
        async with self._price_cache_lock:
            for symbol in symbols:
                cache_time = self._price_cache_time.get(symbol)
                # Use cached price if available and recent (e.g., < 5 seconds old)
                if cache_time and (current_time - cache_time) < timedelta(seconds=5):
                    prices_from_cache[symbol] = self._price_cache.get(symbol)
                else:
                    symbols_to_fetch.append(symbol)
                    
        # Fetch prices for symbols not found in cache
        prices_from_api = {}
        if symbols_to_fetch:
            try:
                # Format symbols for API request: JSON array string
                symbols_param = json.dumps(symbols_to_fetch)
                
                # Use lower-level _get request to bypass potential issues in get_symbol_ticker
                ticker_info = await self.client._get("ticker/price", data={'symbols': symbols_param}, version=self.client.PRIVATE_API_VERSION)

                # Process the list of tickers returned by the API
                for ticker in ticker_info:
                    symbol = ticker['symbol']
                    price = Decimal(ticker['price'])
                    prices_from_api[symbol] = price
                    
                    # Update cache
                    async with self._price_cache_lock:
                         self._price_cache[symbol] = price
                         self._price_cache_time[symbol] = datetime.now()
                         
            except BinanceAPIException as e:
                logger.error(f"Binance API error in get_multiple_prices: {str(e)} - Symbols: {symbols_to_fetch}")
                raise
            except Exception as e:
                logger.error(f"Error in get_multiple_prices: {str(e)} - Symbols: {symbols_to_fetch}")
                raise
        
        # Combine cached and fetched prices
        all_prices = {**prices_from_cache, **prices_from_api}
        
        # Log if any requested symbols were not found in the final result
        missing_symbols = [s for s in symbols if s not in all_prices]
        if missing_symbols:
             logger.warning(f"Could not retrieve prices for some symbols: {missing_symbols}")
             
        return all_prices
    
    # Abstract methods to be implemented by subclasses
    @abstractmethod
    async def get_asset_info(self, symbol: str) -> Asset:
        pass
    
    @abstractmethod
    async def get_balance(self, asset: str) -> Decimal:
        pass
    
    @abstractmethod
    async def get_current_price(self, asset: Asset) -> Decimal:
        pass
    
    @abstractmethod
    async def place_market_order(
        self, 
        asset: Asset, 
        direction: PositionDirection, 
        quantity: Decimal
    ) -> Dict[str, Any]:
        pass
    
    @abstractmethod
    async def place_limit_order(
        self, 
        asset: Asset, 
        direction: PositionDirection, 
        quantity: Decimal, 
        price: Decimal
    ) -> Dict[str, Any]:
        pass
    
    @abstractmethod
    async def get_open_positions(self) -> List[Position]:
        pass
    
    @abstractmethod
    async def get_order_book(self, asset: Asset, depth: int = 5) -> Dict[str, List[Tuple[Decimal, Decimal]]]:
        pass
    
    @abstractmethod
    async def calculate_optimal_quantity(
        self, 
        asset: Asset, 
        amount: Decimal, 
        direction: PositionDirection
    ) -> Decimal:
        pass
    
    @abstractmethod
    async def check_order_status(self, asset: Asset, order_id: str) -> Dict[str, Any]:
        pass
    
    @abstractmethod
    async def cancel_order(self, asset: Asset, order_id: str) -> Dict[str, Any]:
        pass
    
    @abstractmethod
    async def get_recent_trades(self, asset: Asset, limit: int = 10) -> List[Dict[str, Any]]:
        pass
    
    @abstractmethod
    async def get_historical_klines(
        self, 
        asset: Asset, 
        interval: str, 
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        pass

    @property
    @abstractmethod
    def trading_mode(self) -> BinanceTradeMode:
        """
        Returns the trading mode of this adapter.
        
        Returns:
            BinanceTradeMode enum value (SPOT or FUTURES)
        """
        pass

# Factory function to create the appropriate Binance adapter
def create_binance_adapter(
    mode: BinanceTradeMode = BinanceTradeMode.SPOT,
    api_key: Optional[str] = None, 
    api_secret: Optional[str] = None,
    testnet: bool = False
) -> BinanceAdapter:
    """
    Create a new Binance adapter instance based on the trading mode.
    
    Args:
        mode: Trading mode (SPOT or FUTURES)
        api_key: Binance API key (defaults to environment variable)
        api_secret: Binance API secret (defaults to environment variable)
        testnet: Whether to use testnet
        
    Returns:
        Configured BinanceAdapter instance (either spot or futures)
    """
    # Move imports inside the function to break circular dependency
    from .binance_spot_adapter import BinanceSpotAdapter
    
    if mode == BinanceTradeMode.FUTURES:
        return BinanceFuturesAdapter(api_key, api_secret, testnet)
    else:
        return BinanceSpotAdapter(api_key, api_secret, testnet)