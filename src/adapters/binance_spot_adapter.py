"""
Binance spot exchange adapter implementation supporting both spot and futures trading.
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

from binance.client import Client
from binance.exceptions import BinanceAPIException

from .utils.rate_limiter import RateLimiter
from .utils.error_handler import api_request, ErrorCategory
from .utils.connection_manager import BinanceConnectionManager

from ..core.asset import Asset
from ..core.position import Position, PositionDirection
from ..core.exchange_adapter import ExchangeAdapter
from ..core.config import BINANCE_SPOT_API_KEY, BINANCE_SPOT_API_SECRET

from .binance_adapter import BinanceAdapter, BinanceTradeMode

logger = logging.getLogger(__name__)

class BinanceSpotAdapter(BinanceAdapter):
    """
    Binance-specific implementation of the exchange adapter interface
    for spot trading.
    """
    
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None, testnet: bool = False):
        """Initialize the Binance Spot adapter."""
        super().__init__(api_key, api_secret, testnet)
        
        # Initialize spot client
        self.client = Client(self.api_key, self.api_secret, testnet=self.testnet)
    
    @property
    def trading_mode(self) -> BinanceTradeMode:
        """
        Returns the trading mode of this adapter.
        
        Returns:
            BinanceTradeMode.SPOT
        """
        return BinanceTradeMode.SPOT
    
    @api_request(endpoint="market_data", weight=1)
    async def get_asset_info(self, symbol: str) -> Asset:
        """
        Get asset information for a specific symbol.
        
        Args:
            symbol: The asset symbol (e.g., "BTCUSDT")
            
        Returns:
            Asset object with exchange-specific details
            
        Raises:
            ValueError: If the asset doesn't exist or can't be traded
        """
        # Check cache first
        if symbol in self._asset_info_cache:
            return self._asset_info_cache[symbol]
        
        # Fetch symbol info from Binance
        try:
            symbol_info = await self._execute_request('get_symbol_info', symbol)
        except Exception as e:
            logger.error(f"Failed to get symbol info for {symbol}: {str(e)}")
            raise ValueError(f"Failed to get asset info for {symbol}")
        
        if not symbol_info:
            raise ValueError(f"Asset {symbol} not found on Binance")
        
        # Check if trading is enabled
        if symbol_info['status'] != 'TRADING':
            raise ValueError(f"Asset {symbol} is not available for trading")
        
        # Extract lot size filter
        lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
        price_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER'), None)
        
        min_qty = Decimal(lot_size_filter['minQty']) if lot_size_filter else None
        max_qty = Decimal(lot_size_filter['maxQty']) if lot_size_filter else None
        step_size = Decimal(lot_size_filter['stepSize']) if lot_size_filter else None
        
        # Calculate precision
        price_precision = 0
        if price_filter:
            price_precision = self._calculate_precision(price_filter['tickSize'])
        
        quote_precision = 0
        if step_size:
            quote_precision = self._calculate_precision(str(step_size))
        
        # Create asset object
        asset = Asset(
            symbol=symbol,
            asset_type="crypto",
            exchange_id="binance",
            min_quantity=min_qty,
            max_quantity=max_qty,
            step_size=step_size,
            price_precision=price_precision,
            quote_precision=quote_precision
        )
        
        # Cache for future use
        self._asset_info_cache[symbol] = asset
        
        return asset
    
    @api_request(endpoint="account", weight=5)
    async def get_balance(self, asset: str) -> Decimal:
        """
        Get available balance for a specific asset.
        
        Args:
            asset: Asset symbol or asset name (e.g., "BTC" or "USDT")
            
        Returns:
            Available balance as a Decimal
        """
        try:
            # Remove USDT suffix if present for balance check
            asset_name = asset
            if asset.endswith('USDT'):
                asset_name = asset[:-4]
            
            balance = await self._execute_request('get_asset_balance', asset=asset_name)
            
            if balance is None:
                logger.warning(f"No balance found for {asset_name}")
                return Decimal('0')
            
            return Decimal(balance['free'])
        except BinanceAPIException as e:
            logger.error(f"Binance API error in get_balance: {str(e)}")
            return Decimal('0')
        except Exception as e:
            logger.error(f"Error in get_balance: {str(e)}")
            return Decimal('0')
    
    @api_request(endpoint="market_data", weight=1)
    async def get_current_price(self, asset: Asset) -> Decimal:
        """
        Get current market price for an asset.
        
        Args:
            asset: Asset to get price for
            
        Returns:
            Current price as a Decimal
        """
        symbol = asset.symbol
        
        # Check cache first (with a short expiry time)
        async with self._price_cache_lock:
            cache_time = self._price_cache_time.get(symbol)
            current_time = datetime.now()
            
            # If we have a cached price and it's less than 5 seconds old, use it
            if cache_time and (current_time - cache_time) < timedelta(seconds=5):
                return self._price_cache.get(symbol, Decimal('0'))
        
        # Fetch current price from Binance
        try:
            ticker = await self._execute_request('get_symbol_ticker', symbol=symbol)
            price = Decimal(ticker['price'])
            
            # Update cache
            async with self._price_cache_lock:
                self._price_cache[symbol] = price
                self._price_cache_time[symbol] = datetime.now()
            
            return price
        except BinanceAPIException as e:
            logger.error(f"Binance API error in get_current_price: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Error in get_current_price: {str(e)}")
            raise
    
    @api_request(endpoint="order", weight=10, retry_for=[ErrorCategory.NETWORK, ErrorCategory.SERVER])
    async def place_market_order(
        self, 
        asset: Asset, 
        direction: PositionDirection, 
        quantity: Decimal,
        # Inherited margin parameters
        is_isolated: Optional[bool] = None,
        side_effect_type: Optional[str] = None 
    ) -> Dict[str, Any]:
        """
        Place a market order on the exchange (spot or margin).
        
        Args:
            asset: Asset to trade
            direction: Order direction (LONG for buy, SHORT for sell)
            quantity: Quantity to buy/sell
            is_isolated: Specify True for ISOLATED margin, False/None for CROSSED
            side_effect_type: If provided, places a margin order (e.g., 'MARGIN_BUY', 'AUTO_REPAY')
            
        Returns:
            Exchange order details
            
        Raises:
            Exception: If order placement fails
        """
        try:
            # Format quantity properly
            formatted_quantity = self._format_quantity(asset, quantity)
            
            # Check if quantity is too small after formatting
            if Decimal(formatted_quantity) <= 0:
                logger.warning(f"Quantity {quantity} for {asset.symbol} is too small after formatting. Minimum: {asset.min_quantity}")
                raise ValueError(f"Quantity {quantity} is too small for {asset.symbol} (min: {asset.min_quantity})")
            
            order_side = "BUY" if direction == PositionDirection.LONG else "SELL"
            
            # --- Check if it's a Margin Order --- 
            if side_effect_type:
                logger.info(f"Placing MARGIN {direction.value} market order for {asset.symbol}: {formatted_quantity}")
                logger.info(f"  -> Margin Params: Isolated={is_isolated}, SideEffect={side_effect_type}")
                
                params = {
                    'symbol': asset.symbol,
                    'side': order_side,
                    'type': 'MARKET',
                    'quantity': formatted_quantity,
                    'sideEffectType': side_effect_type
                }
                # Add isIsolated only if it's explicitly True or False (not None)
                if is_isolated is not None:
                    params['isIsolated'] = "TRUE" if is_isolated else "FALSE"
                    
                # Use the specific margin order creation method
                order = await self._execute_request(
                    'create_margin_order', 
                    **params
                )
                logger.info(f"Placed MARGIN {direction.value} market order OK. Details: {order}")

            # --- Regular Spot Order --- 
            else:
                logger.info(f"Placing SPOT {direction.value} market order for {asset.symbol}: {formatted_quantity}")
                
                # Determine which spot order function to call
                if direction == PositionDirection.LONG:
                    order = await self._execute_request(
                        'order_market_buy',
                        symbol=asset.symbol,
                        quantity=formatted_quantity
                    )
                else:  # SHORT
                    order = await self._execute_request(
                        'order_market_sell',
                        symbol=asset.symbol,
                        quantity=formatted_quantity
                    )
                logger.info(f"Placed SPOT {direction.value} market order OK. Details: {order}")
            
            # Return the order details from either path
            return order
        except BinanceAPIException as e:
            logger.error(f"Binance API error in place_market_order: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Error in place_market_order: {str(e)}")
            raise
    
    @api_request(endpoint="order", weight=10, retry_for=[ErrorCategory.NETWORK, ErrorCategory.SERVER])
    async def place_limit_order(
        self, 
        asset: Asset, 
        direction: PositionDirection, 
        quantity: Decimal, 
        price: Decimal,
        # Inherited margin parameters
        is_isolated: Optional[bool] = None,
        side_effect_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Place a limit order on the exchange (spot or margin).
        
        Args:
            asset: Asset to trade
            direction: Order direction (LONG for buy, SHORT for sell)
            quantity: Quantity to buy/sell
            price: Limit order price
            is_isolated: Specify True for ISOLATED margin, False/None for CROSSED
            side_effect_type: If provided, places a margin order (e.g., 'MARGIN_BUY', 'AUTO_REPAY')
            
        Returns:
            Exchange order details
            
        Raises:
            Exception: If order placement fails
        """
        try:
            # Format quantity and price properly
            formatted_quantity = self._format_quantity(asset, quantity)
            formatted_price = self._format_price(asset, price)
            
            # Check if quantity is too small after formatting
            if Decimal(formatted_quantity) <= 0:
                logger.warning(f"Quantity {quantity} for {asset.symbol} is too small after formatting. Minimum: {asset.min_quantity}")
                raise ValueError(f"Quantity {quantity} is too small for {asset.symbol} (min: {asset.min_quantity})")
            
            order_side = "BUY" if direction == PositionDirection.LONG else "SELL"
            
            # --- Check if it's a Margin Order --- 
            if side_effect_type:
                logger.info(f"Placing MARGIN {direction.value} limit order for {asset.symbol}: {formatted_quantity} @ {formatted_price}")
                logger.info(f"  -> Margin Params: Isolated={is_isolated}, SideEffect={side_effect_type}")
                
                params = {
                    'symbol': asset.symbol,
                    'side': order_side,
                    'type': 'LIMIT',
                    'quantity': formatted_quantity,
                    'price': formatted_price,
                    'timeInForce': 'GTC', # Good Til Canceled is common for limit orders
                    'sideEffectType': side_effect_type
                }
                # Add isIsolated only if it's explicitly True or False (not None)
                if is_isolated is not None:
                    params['isIsolated'] = "TRUE" if is_isolated else "FALSE"
                    
                # Use the specific margin order creation method
                order = await self._execute_request(
                    'create_margin_order', 
                    **params
                )
                logger.info(f"Placed MARGIN {direction.value} limit order OK. Details: {order}")

            # --- Regular Spot Order --- 
            else:
                logger.info(f"Placing SPOT {direction.value} limit order for {asset.symbol}: {formatted_quantity} @ {formatted_price}")
                
                # Determine which spot order function to call
                if direction == PositionDirection.LONG:
                    order = await self._execute_request(
                        'order_limit_buy',
                        symbol=asset.symbol,
                        quantity=formatted_quantity,
                        price=formatted_price
                    )
                else:  # SHORT
                    order = await self._execute_request(
                        'order_limit_sell',
                        symbol=asset.symbol,
                        quantity=formatted_quantity,
                        price=formatted_price
                    )
                logger.info(f"Placed SPOT {direction.value} limit order OK. Details: {order}")
            
            # Return the order details from either path
            return order
        except BinanceAPIException as e:
            logger.error(f"Binance API error in place_limit_order: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Error in place_limit_order: {str(e)}")
            raise
    
    @api_request(endpoint="account", weight=1)
    async def get_open_positions(self) -> List[Position]:
        """
        Get all open positions from the exchange.
        NOTE: For spot trading, Binance doesn't track positions, so this is a stub.
        
        Returns:
            Empty list as Binance spot doesn't track positions
        """
        # Binance spot doesn't track positions, so this is a stub
        logger.warning("get_open_positions called, but Binance spot doesn't track positions")
        return []
    
    @api_request(endpoint="market_data", weight=5)
    async def get_order_book(self, asset: Asset, depth: int = 5) -> Dict[str, List[Tuple[Decimal, Decimal]]]:
        """
        Get order book for a specific asset.
        
        Args:
            asset: Asset to get order book for
            depth: Depth of order book to retrieve
            
        Returns:
            Dictionary with 'bids' and 'asks' arrays of [price, quantity] tuples
        """
        try:
            order_book = await self._execute_request(
                'get_order_book',
                symbol=asset.symbol,
                limit=depth
            )
            
            # Convert to Decimal
            bids = [(Decimal(price), Decimal(qty)) for price, qty in order_book['bids']]
            asks = [(Decimal(price), Decimal(qty)) for price, qty in order_book['asks']]
            
            return {
                'bids': bids,
                'asks': asks
            }
        except BinanceAPIException as e:
            logger.error(f"Binance API error in get_order_book: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Error in get_order_book: {str(e)}")
            raise
    
    @api_request(endpoint="market_data", weight=1)
    async def calculate_optimal_quantity(
        self, 
        asset: Asset, 
        amount: Decimal, 
        direction: PositionDirection
    ) -> Decimal:
        """
        Calculate the optimal quantity that can be traded given an amount of quote currency.
        
        Args:
            asset: Asset to calculate quantity for
            amount: Amount of quote currency to spend/receive
            direction: Order direction
            
        Returns:
            Optimal quantity that meets exchange requirements
        """
        try:
            # Get current price
            current_price = await self.get_current_price(asset)
            
            # Calculate raw quantity (amount to spend divided by price)
            quantity = amount / current_price
            
            # Adjust quantity based on asset constraints
            adjusted_quantity = asset.ensure_valid_quantity(quantity)
            
            logger.info(
                f"Calculated optimal quantity for {asset.symbol}: "
                f"Amount={amount}, Price={current_price}, Raw={quantity}, Adjusted={adjusted_quantity}"
            )
            
            return adjusted_quantity
        except Exception as e:
            logger.error(f"Error calculating optimal quantity: {str(e)}")
            raise
    
    @api_request(endpoint="order", weight=2)
    async def check_order_status(self, asset: Asset, order_id: str) -> Dict[str, Any]:
        """
        Check the status of a specific order.
        
        Args:
            asset: Asset the order is for
            order_id: Exchange order ID
            
        Returns:
            Order details including status
        """
        try:
            order = await self._execute_request(
                'get_order',
                symbol=asset.symbol,
                orderId=order_id
            )
            return order
        except BinanceAPIException as e:
            logger.error(f"Binance API error in check_order_status: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Error in check_order_status: {str(e)}")
            raise
    
    @api_request(endpoint="order", weight=5, retry_for=[ErrorCategory.NETWORK, ErrorCategory.SERVER])
    async def cancel_order(self, asset: Asset, order_id: str) -> Dict[str, Any]:
        """
        Cancel an open order.
        
        Args:
            asset: Asset the order is for
            order_id: Exchange order ID
            
        Returns:
            Cancellation details
        """
        try:
            result = await self._execute_request(
                'cancel_order',
                symbol=asset.symbol,
                orderId=order_id
            )
            logger.info(f"Cancelled order {order_id} for {asset.symbol}")
            return result
        except BinanceAPIException as e:
            logger.error(f"Binance API error in cancel_order: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Error in cancel_order: {str(e)}")
            raise
    
    @api_request(endpoint="market_data", weight=5)
    async def get_recent_trades(self, asset: Asset, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent trades for an asset.
        
        Args:
            asset: Asset to get trades for
            limit: Number of trades to retrieve
            
        Returns:
            List of recent trades
        """
        try:
            trades = await self._execute_request(
                'get_recent_trades',
                symbol=asset.symbol,
                limit=limit
            )
            return trades
        except BinanceAPIException as e:
            logger.error(f"Binance API error in get_recent_trades: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Error in get_recent_trades: {str(e)}")
            raise
    
    @api_request(endpoint="market_data", weight=10)
    async def get_historical_klines(
        self, 
        asset: Asset, 
        interval: str, 
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        """
        Get historical klines (candlesticks) for an asset.
        
        Args:
            asset: Asset to get klines for
            interval: Kline interval (e.g., "1m", "5m", "1h", "1d")
            start_time: Start time (defaults to 500 intervals ago)
            end_time: End time (defaults to now)
            limit: Number of klines to retrieve
            
        Returns:
            List of klines with OHLCV data
        """
        try:
            # Convert datetime to milliseconds timestamp if provided
            start_str = int(start_time.timestamp() * 1000) if start_time else None
            end_str = int(end_time.timestamp() * 1000) if end_time else None
            
            # Get klines from Binance
            klines = await self._execute_request(
                'get_klines',
                symbol=asset.symbol,
                interval=interval,
                startTime=start_str,
                endTime=end_str,
                limit=limit
            )
            
            # Convert to more readable format
            processed_klines = []
            for k in klines:
                processed_klines.append({
                    'open_time': datetime.fromtimestamp(k[0] / 1000),
                    'open': Decimal(str(k[1])),
                    'high': Decimal(str(k[2])),
                    'low': Decimal(str(k[3])),
                    'close': Decimal(str(k[4])),
                    'volume': Decimal(str(k[5])),
                    'close_time': datetime.fromtimestamp(k[6] / 1000),
                    'quote_asset_volume': Decimal(str(k[7])),
                    'number_of_trades': k[8],
                    'taker_buy_base_volume': Decimal(str(k[9])),
                    'taker_buy_quote_volume': Decimal(str(k[10]))
                })
            
            return processed_klines
        except BinanceAPIException as e:
            logger.error(f"Binance API error in get_historical_klines: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Error in get_historical_klines: {str(e)}")
            raise
    
    def _format_quantity(self, asset: Asset, quantity: Decimal) -> str:
        """
        Format quantity according to asset's precision requirements.
        
        Args:
            asset: Asset with precision info
            quantity: Quantity to format
            
        Returns:
            Formatted quantity string
        """
        # First adjust the quantity to meet lot size requirements
        if asset.min_quantity is not None:
            quantity = max(asset.min_quantity, quantity)
        if asset.max_quantity is not None:
            quantity = min(quantity, asset.max_quantity)
            
        # Apply step size truncation with proper rounding
        if asset.step_size is not None and asset.step_size > 0:
            # Calculate precision from step size
            precision = int(round(-math.log10(float(asset.step_size)), 0))
            
            # Ensure quantity is a multiple of step_size
            # Using the formula: (quantity - minQty) % stepSize == 0
            # We implement this by truncating to step size multiples using ROUND_DOWN
            quantity = (quantity // asset.step_size * asset.step_size).quantize(asset.step_size, rounding=ROUND_DOWN)
            
        # Check if quantity is below the minimum after adjustment
        if asset.min_quantity is not None and quantity < asset.min_quantity:
            logger.warning(f"Adjusted quantity {quantity} is below minimum {asset.min_quantity} for {asset.symbol}")
            quantity = Decimal('0')  # Set to zero to indicate invalid quantity
            
        # Format with proper precision - use calculated precision if available
        if quantity > 0:
            if asset.step_size is not None and asset.step_size > 0:
                precision = int(round(-math.log10(float(asset.step_size)), 0))
                return f"{quantity:.{precision}f}"
            elif asset.quote_precision is not None:
                return f"{quantity:.{asset.quote_precision}f}"
        
        # Always return as string
        return str(quantity)
    
    def _format_price(self, asset: Asset, price: Decimal) -> str:
        """
        Format price according to asset's precision requirements.
        
        Args:
            asset: Asset with precision info
            price: Price to format
            
        Returns:
            Formatted price string
        """
        if asset.price_precision is not None:
            return f"{price:.{asset.price_precision}f}"
        return str(price)