"""
Binance futures exchange adapter implementation supporting both spot and futures trading.
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

from .binance_adapter import BinanceAdapter

logger = logging.getLogger(__name__)

class BinanceFuturesAdapter(BinanceAdapter):
    """
    Binance-specific implementation of the exchange adapter interface
    for futures trading.
    """
    
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None, testnet: bool = False):
        """Initialize the Binance Futures adapter."""
        super().__init__(api_key, api_secret, testnet)
        
        # Initialize futures client
        self.client = Client(self.api_key, self.api_secret)
        
        if testnet:
            self.client.FUTURES_URL = 'https://testnet.binancefuture.com'
            
        # Get symbol info for later use with formatting quantities
        self.symbol_info = self._get_symbol_info()

    def _get_symbol_info(self):
        exchange_info = self.client.futures_exchange_info()
        symbol_info = {}
        for item in exchange_info['symbols']:
            filters = {f['filterType']: f for f in item['filters']}
            symbol_info[item['symbol']] = {
                'stepSize': float(filters['LOT_SIZE']['stepSize']),
                'tickSize': float(filters['PRICE_FILTER']['tickSize']),
            }
        return symbol_info

    def format_quantity(self, symbol, quantity):
        step_size = self.symbol_info[symbol]['stepSize']
        precision = int(round(-math.log(step_size, 10), 0))
        return round(quantity, precision)

    def get_balance(self, asset):
        try:
            balances = self.client.futures_account_balance()
            for balance in balances:
                if balance['asset'] == asset:
                    return float(balance['availableBalance'])
            return 0.0
        except BinanceAPIException as e:
            print(f"Error fetching balance: {e}")
            return None

    @api_request(endpoint="order", weight=10, retry_for=[ErrorCategory.NETWORK, ErrorCategory.SERVER])
    async def place_order(
        self, 
        asset: Asset, 
        side: str, 
        quantity: Decimal,
        order_type: str = 'MARKET',
        price: Optional[Decimal] = None,
        position_side: str = 'BOTH',
        time_in_force: str = 'GTC',
        reduce_only: bool = False,
        close_position: bool = False,
        config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Place a futures order with proper formatting and configuration.
        
        Args:
            asset: Asset to trade
            side: Order side ('BUY' or 'SELL')
            quantity: Quantity to trade
            order_type: Order type ('MARKET', 'LIMIT', etc.)
            price: Price for limit orders
            position_side: Position side ('BOTH', 'LONG', or 'SHORT')
            time_in_force: Time in force ('GTC', 'IOC', 'FOK')
            reduce_only: Whether the order should only reduce position
            close_position: Whether to close the position (true for market close)
            config: Configuration options
            
        Returns:
            Exchange order details
            
        Raises:
            Exception: If order placement fails
        """
        try:
            # Initialize trading params if config is provided
            if config:
                symbol_config = config.get('symbols', {}).get(asset.symbol, {})
                # Merge symbol config with global config
                merged_config = {**config, **symbol_config}
                await self.initialize_trading_params(asset, merged_config)
            
            # Format quantity properly using our improved method
            formatted_quantity = self._format_quantity(asset, quantity)
            
            # Validate side
            side = side.upper()
            if side not in ['BUY', 'SELL']:
                raise ValueError(f"Invalid side: {side}. Must be 'BUY' or 'SELL'")
            
            # Validate position side if hedge mode
            position_side = position_side.upper()
            if position_side not in ['BOTH', 'LONG', 'SHORT']:
                raise ValueError(f"Invalid position side: {position_side}. Must be 'BOTH', 'LONG', or 'SHORT'")
            
            # Prepare order parameters
            params = {
                'symbol': asset.symbol,
                'side': side,
                'type': order_type,
            }
            
            # For close_position, we don't specify quantity
            if close_position:
                params['closePosition'] = 'true'
            else:
                # Only add quantity if not closing position
                # Check if quantity is too small after formatting
                if Decimal(formatted_quantity) <= 0:
                    logger.warning(f"Quantity {quantity} for {asset.symbol} is too small after formatting. Minimum: {asset.min_quantity}")
                    raise ValueError(f"Quantity {quantity} is too small for {asset.symbol} (min: {asset.min_quantity})")
                params['quantity'] = formatted_quantity
            
            # Add position side for hedge mode
            if position_side != 'BOTH':
                params['positionSide'] = position_side
            
            # Add reduce_only flag if specified
            if reduce_only:
                params['reduceOnly'] = 'true'
            
            # Add price and time_in_force for limit orders
            if order_type == 'LIMIT' and price is not None:
                params['price'] = self._format_price(asset, price)
                params['timeInForce'] = time_in_force
            
            logger.info(f"Placing futures {order_type} order for {asset.symbol}: {params}")
            
            # Execute the order
            order = await self._execute_request('futures_create_order', **params)
            
            logger.info(f"Successfully placed futures order for {asset.symbol}: {order}")
            return order
        except BinanceAPIException as e:
            logger.error(f"Binance API error in place_order: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Error in place_order: {str(e)}")
            raise

    def set_leverage(self, symbol, leverage):
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
        except BinanceAPIException as e:
            print(f"Error setting leverage: {e}")
            
    async def _get_leverage_brackets(self, symbol: str) -> List[Dict]:
        """
        Get leverage brackets for a futures symbol.
        
        Args:
            symbol: Futures symbol
            
        Returns:
            List of leverage bracket information
        """
        try:
            brackets = await self._execute_request('futures_leverage_bracket', symbol=symbol)
            if isinstance(brackets, list):
                for item in brackets:
                    if item['symbol'] == symbol:
                        return item['brackets']
                return []
            else:
                return brackets.get('brackets', [])
        except Exception as e:
            logger.error(f"Error getting leverage brackets for {symbol}: {str(e)}")
            return []
    
    @api_request(endpoint="account", weight=5)
    async def get_balance(self, asset: str) -> Decimal:
        """
        Get available USDT balance for futures account.
        
        Args:
            asset: Asset symbol (for futures, this is typically USDT)
            
        Returns:
            Available balance as a Decimal
        """
        try:
            # For futures, we typically check USDT balance
            asset_name = "USDT"
            if asset.endswith('USDT'):
                # For consistency with spot implementation
                pass
            
            # Get futures account information
            account = await self._execute_request('futures_account')
            
            # Find the asset in the assets list
            for balance in account['assets']:
                if balance['asset'] == asset_name:
                    return Decimal(balance['availableBalance'])
            
            logger.warning(f"No balance found for {asset_name} in futures account")
            return Decimal('0')
        except BinanceAPIException as e:
            logger.error(f"Binance API error in get_balance: {str(e)}")
            return Decimal('0')
        except Exception as e:
            logger.error(f"Error in get_balance: {str(e)}")
            return Decimal('0')
    
    @api_request(endpoint="market_data", weight=1)
    async def get_current_price(self, asset: Asset) -> Decimal:
        """
        Get current market price for a futures asset.
        
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
        
        # Fetch current price from Binance Futures
        try:
            ticker = await self._execute_request('futures_symbol_ticker', symbol=symbol)
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
    
    @api_request(endpoint="account", weight=1)
    async def set_leverage(self, asset: Asset, leverage: int) -> Dict[str, Any]:
        """
        Set leverage for a futures symbol.
        
        Args:
            asset: Asset to set leverage for
            leverage: Leverage value (e.g., 1, 2, 5, 10, etc.)
            
        Returns:
            Response from the API
        """
        try:
            result = await self._execute_request(
                'futures_change_leverage',
                symbol=asset.symbol,
                leverage=leverage
            )
            logger.info(f"Set leverage for {asset.symbol} to {leverage}x")
            return result
        except BinanceAPIException as e:
            logger.error(f"Binance API error in set_leverage: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Error in set_leverage: {str(e)}")
            raise
    
    @api_request(endpoint="order", weight=10, retry_for=[ErrorCategory.NETWORK, ErrorCategory.SERVER])
    async def place_market_order(
        self, 
        asset: Asset, 
        direction: PositionDirection, 
        quantity: Decimal,
        reduce_only: bool = False
    ) -> Dict[str, Any]:
        """
        Place a futures market order.
        
        Args:
            asset: Asset to trade
            direction: Order direction (LONG for buy, SHORT for sell)
            quantity: Quantity to buy/sell
            reduce_only: Whether the order is reduce-only (closes position only)
            
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
            
            # For futures, we use LONG/SHORT directly rather than BUY/SELL
            side = "BUY" if direction == PositionDirection.LONG else "SELL"
            position_side = "LONG" if direction == PositionDirection.LONG else "SHORT"
            
            logger.info(f"Placing {direction.value} futures market order for {asset.symbol}: {formatted_quantity}")
            
            # Prepare parameters
            params = {
                'symbol': asset.symbol,
                'side': side,
                'type': 'MARKET',
                'quantity': formatted_quantity
            }
            
            # Add reduce_only if specified
            if reduce_only:
                params['reduceOnly'] = 'true'
            
            # Execute order
            order = await self._execute_request('futures_create_order', **params)
            
            logger.info(f"Placed {direction.value} futures market order for {asset.symbol}: {formatted_quantity}")
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
        reduce_only: bool = False
    ) -> Dict[str, Any]:
        """
        Place a futures limit order.
        
        Args:
            asset: Asset to trade
            direction: Order direction (LONG for buy, SHORT for sell)
            quantity: Quantity to buy/sell
            price: Limit order price
            reduce_only: Whether the order is reduce-only (closes position only)
            
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
            
            # For futures, we use LONG/SHORT directly rather than BUY/SELL
            side = "BUY" if direction == PositionDirection.LONG else "SELL"
            position_side = "LONG" if direction == PositionDirection.LONG else "SHORT"
            
            logger.info(f"Placing {direction.value} futures limit order for {asset.symbol}: {formatted_quantity} @ {formatted_price}")
            
            # Prepare parameters
            params = {
                'symbol': asset.symbol,
                'side': side,
                'type': 'LIMIT',
                'timeInForce': 'GTC',  # Good Till Cancelled
                'quantity': formatted_quantity,
                'price': formatted_price
            }
            
            # Add reduce_only if specified
            if reduce_only:
                params['reduceOnly'] = 'true'
            
            # Execute order
            order = await self._execute_request('futures_create_order', **params)
            
            logger.info(f"Placed {direction.value} futures limit order for {asset.symbol} at {price}: {order}")
            return order
        except BinanceAPIException as e:
            logger.error(f"Binance API error in place_limit_order: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Error in place_limit_order: {str(e)}")
            raise
    
    @api_request(endpoint="account", weight=5)
    async def get_open_positions(self) -> List[Position]:
        """
        Get all open futures positions from the exchange.
        
        Returns:
            List of open positions
        """
        try:
            # Get futures position information
            positions = await self._execute_request('futures_position_information')
            
            # Filter out positions with zero amount
            active_positions = [p for p in positions if Decimal(p['positionAmt']) != 0]
            
            # Convert to Position objects
            result = []
            for pos in active_positions:
                symbol = pos['symbol']
                
                # Get asset info
                asset = await self.get_asset_info(symbol)
                
                # Determine direction
                amt = Decimal(pos['positionAmt'])
                direction = PositionDirection.LONG if amt > 0 else PositionDirection.SHORT
                
                # Create position object
                position = Position(
                    asset=asset,
                    direction=direction,
                    initial_quantity=abs(amt),
                    entry_price=Decimal(pos['entryPrice']),
                    bot_strategy="manual",  # Default for positions from exchange
                    timeframe="unknown",    # Default for positions from exchange
                    leverage=Decimal(pos['leverage']),
                    remaining_quantity=abs(amt),
                    external_id=f"binance_futures_{symbol}_{direction.value}"
                )
                
                result.append(position)
            
            return result
        except BinanceAPIException as e:
            logger.error(f"Binance API error in get_open_positions: {str(e)}")
            return []
        except Exception as e:
            logger.error(f"Error in get_open_positions: {str(e)}")
            return []
    
    @api_request(endpoint="market_data", weight=5)
    async def get_order_book(self, asset: Asset, depth: int = 5) -> Dict[str, List[Tuple[Decimal, Decimal]]]:
        """
        Get futures order book for a specific asset.
        
        Args:
            asset: Asset to get order book for
            depth: Depth of order book to retrieve
            
        Returns:
            Dictionary with 'bids' and 'asks' arrays of [price, quantity] tuples
        """
        try:
            order_book = await self._execute_request(
                'futures_order_book',
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
        direction: PositionDirection,
        leverage: int = 1
    ) -> Decimal:
        """
        Calculate the optimal quantity for a futures order.
        
        Args:
            asset: Asset to calculate quantity for
            amount: Amount of quote currency (USDT) to use as margin
            direction: Order direction
            leverage: Leverage to use (default 1x)
            
        Returns:
            Optimal quantity that meets exchange requirements
        """
        try:
            # Get current price
            current_price = await self.get_current_price(asset)
            
            # For futures, we need to account for leverage
            quantity = (amount * leverage) / current_price
            
            # Adjust quantity based on asset constraints
            adjusted_quantity = asset.ensure_valid_quantity(quantity)
            
            logger.info(
                f"Calculated optimal futures quantity for {asset.symbol}: "
                f"Amount={amount}, Leverage={leverage}x, Price={current_price}, "
                f"Raw={quantity}, Adjusted={adjusted_quantity}"
            )
            
            return adjusted_quantity
        except Exception as e:
            logger.error(f"Error calculating optimal futures quantity: {str(e)}")
            raise
    
    @api_request(endpoint="order", weight=2)
    async def check_order_status(self, asset: Asset, order_id: str) -> Dict[str, Any]:
        """
        Check the status of a specific futures order.
        
        Args:
            asset: Asset the order is for
            order_id: Exchange order ID
            
        Returns:
            Order details including status
        """
        try:
            order = await self._execute_request(
                'futures_get_order',
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
        Cancel an open futures order.
        
        Args:
            asset: Asset the order is for
            order_id: Exchange order ID
            
        Returns:
            Cancellation details
        """
        try:
            result = await self._execute_request(
                'futures_cancel_order',
                symbol=asset.symbol,
                orderId=order_id
            )
            logger.info(f"Cancelled futures order {order_id} for {asset.symbol}")
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
        Get recent futures trades for an asset.
        
        Args:
            asset: Asset to get trades for
            limit: Number of trades to retrieve
            
        Returns:
            List of recent trades
        """
        try:
            trades = await self._execute_request(
                'futures_recent_trades',
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
        Get historical futures klines (candlesticks) for an asset.
        
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
            
            # Get klines from Binance Futures
            klines = await self._execute_request(
                'futures_klines',
                symbol=asset.symbol,
                interval=interval,
                startTime=start_str,
                endTime=end_str,
                limit=limit
            )
            
            # Convert to more readable format (same format as spot klines)
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
        Format quantity according to asset's precision requirements for futures.
        
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
        Format price according to asset's precision requirements for futures.
        
        Args:
            asset: Asset with precision info
            price: Price to format
            
        Returns:
            Formatted price string
        """
        if asset.price_precision is not None:
            return f"{price:.{asset.price_precision}f}"
        return str(price)

    @api_request(endpoint="account", weight=1)
    async def set_margin_type(self, asset: Asset, margin_type: str) -> Dict[str, Any]:
        """
        Set margin type for a futures symbol.
        
        Args:
            asset: Asset to set margin type for
            margin_type: Margin type ('ISOLATED' or 'CROSSED')
            
        Returns:
            Response from the API
        """
        try:
            # Ensure margin_type is uppercase and valid
            margin_type = margin_type.upper()
            if margin_type not in ['ISOLATED', 'CROSSED']:
                raise ValueError(f"Invalid margin type: {margin_type}. Must be 'ISOLATED' or 'CROSSED'")
            
            result = await self._execute_request(
                'futures_change_margin_type',
                symbol=asset.symbol,
                marginType=margin_type
            )
            logger.info(f"Set margin type for {asset.symbol} to {margin_type}")
            return result
        except BinanceAPIException as e:
            # Handle the case where margin type is already set
            if "No need to change margin type" in str(e):
                logger.info(f"Margin type for {asset.symbol} is already {margin_type}")
                return {"code": 200, "msg": f"Margin type for {asset.symbol} is already {margin_type}"}
            else:
                logger.error(f"Binance API error in set_margin_type: {str(e)}")
                raise
        except Exception as e:
            logger.error(f"Error in set_margin_type: {str(e)}")
            raise

    @api_request(endpoint="account", weight=5)
    async def initialize_trading_params(self, asset: Asset, config: Dict[str, Any]) -> None:
        """
        Initialize trading parameters for a futures symbol based on configuration.
        This includes setting leverage and margin type.
        
        Args:
            asset: Asset to initialize parameters for
            config: Configuration dictionary containing trading parameters
            
        Returns:
            None
        """
        try:
            # Set leverage if specified in config
            leverage = config.get('default_leverage', 1)
            max_leverage = config.get('max_leverage', 20)
            
            # Ensure leverage is within allowed range
            leverage = min(max(leverage, 1), max_leverage)
            
            # Set leverage
            await self.set_leverage(asset, leverage)
            
            # Set margin type if specified in config
            margin_type = config.get('margin_type', 'CROSSED')
            await self.set_margin_type(asset, margin_type)
            
            # Set position mode if specified in config
            position_mode = config.get('position_mode', 'hedge')
            if position_mode.lower() == 'hedge':
                # Try to set hedge mode (dual positions)
                try:
                    await self._execute_request('futures_change_position_mode', dualSidePosition=True)
                    logger.info(f"Set position mode to hedge (dual position) for {asset.symbol}")
                except BinanceAPIException as e:
                    if "No need to change position side" in str(e):
                        logger.info("Position mode is already set to hedge mode")
                    else:
                        logger.error(f"Error setting position mode: {str(e)}")
            elif position_mode.lower() == 'one-way':
                # Try to set one-way mode
                try:
                    await self._execute_request('futures_change_position_mode', dualSidePosition=False)
                    logger.info(f"Set position mode to one-way for {asset.symbol}")
                except BinanceAPIException as e:
                    if "No need to change position side" in str(e):
                        logger.info("Position mode is already set to one-way mode")
                    else:
                        logger.error(f"Error setting position mode: {str(e)}")
            
            logger.info(f"Initialized trading parameters for {asset.symbol}")
        except Exception as e:
            logger.error(f"Error initializing trading parameters for {asset.symbol}: {str(e)}")
            raise

    @api_request(endpoint="order", weight=10, retry_for=[ErrorCategory.NETWORK, ErrorCategory.SERVER])
    async def place_market_long(
        self, 
        asset: Asset, 
        quantity: Decimal,
        reduce_only: bool = False,
        config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Place a market order to open a long position.
        
        Args:
            asset: Asset to trade
            quantity: Quantity to trade
            reduce_only: Whether the order should only reduce position
            config: Configuration options
            
        Returns:
            Exchange order details
        """
        return await self.place_order(
            asset=asset,
            side='BUY',
            quantity=quantity,
            order_type='MARKET',
            position_side='LONG',
            reduce_only=reduce_only,
            config=config
        )
    
    @api_request(endpoint="order", weight=10, retry_for=[ErrorCategory.NETWORK, ErrorCategory.SERVER])
    async def place_market_short(
        self, 
        asset: Asset, 
        quantity: Decimal,
        reduce_only: bool = False,
        config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Place a market order to open a short position.
        
        Args:
            asset: Asset to trade
            quantity: Quantity to trade
            reduce_only: Whether the order should only reduce position
            config: Configuration options
            
        Returns:
            Exchange order details
        """
        return await self.place_order(
            asset=asset,
            side='SELL',
            quantity=quantity,
            order_type='MARKET',
            position_side='SHORT',
            reduce_only=reduce_only,
            config=config
        )
    
    @api_request(endpoint="order", weight=10, retry_for=[ErrorCategory.NETWORK, ErrorCategory.SERVER])
    async def place_limit_long(
        self, 
        asset: Asset, 
        quantity: Decimal,
        price: Decimal,
        time_in_force: str = 'GTC',
        reduce_only: bool = False,
        config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Place a limit order to open a long position.
        
        Args:
            asset: Asset to trade
            quantity: Quantity to trade
            price: Limit price
            time_in_force: Time in force ('GTC', 'IOC', 'FOK')
            reduce_only: Whether the order should only reduce position
            config: Configuration options
            
        Returns:
            Exchange order details
        """
        return await self.place_order(
            asset=asset,
            side='BUY',
            quantity=quantity,
            order_type='LIMIT',
            price=price,
            position_side='LONG',
            time_in_force=time_in_force,
            reduce_only=reduce_only,
            config=config
        )
    
    @api_request(endpoint="order", weight=10, retry_for=[ErrorCategory.NETWORK, ErrorCategory.SERVER])
    async def place_limit_short(
        self, 
        asset: Asset, 
        quantity: Decimal,
        price: Decimal,
        time_in_force: str = 'GTC',
        reduce_only: bool = False,
        config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Place a limit order to open a short position.
        
        Args:
            asset: Asset to trade
            quantity: Quantity to trade
            price: Limit price
            time_in_force: Time in force ('GTC', 'IOC', 'FOK')
            reduce_only: Whether the order should only reduce position
            config: Configuration options
            
        Returns:
            Exchange order details
        """
        return await self.place_order(
            asset=asset,
            side='SELL',
            quantity=quantity,
            order_type='LIMIT',
            price=price,
            position_side='SHORT',
            time_in_force=time_in_force,
            reduce_only=reduce_only,
            config=config
        )
    
    @api_request(endpoint="order", weight=10, retry_for=[ErrorCategory.NETWORK, ErrorCategory.SERVER])
    async def close_position(
        self, 
        asset: Asset,
        position_side: str,
        config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Close an entire position immediately at market price.
        
        Args:
            asset: Asset to trade
            position_side: Position side to close ('LONG' or 'SHORT')
            config: Configuration options
            
        Returns:
            Exchange order details
        """
        # For closing a position, we use the opposite side
        side = 'SELL' if position_side == 'LONG' else 'BUY'
        
        return await self.place_order(
            asset=asset,
            side=side,
            quantity=Decimal('0'),  # Not used when close_position=True
            position_side=position_side,
            close_position=True,
            config=config
        )

    @property
    def trading_mode(self) -> BinanceTradeMode:
        """
        Returns the trading mode of this adapter.
        
        Returns:
            BinanceTradeMode.FUTURES
        """
        return BinanceTradeMode.FUTURES
