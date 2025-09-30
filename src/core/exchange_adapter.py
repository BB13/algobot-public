"""
Exchange adapter interface for interacting with cryptocurrency exchanges.
"""
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Dict, List, Optional, Any, Tuple

from .asset import Asset
from .position import Position, PositionDirection


class ExchangeAdapter(ABC):
    """
    Interface for all exchange-specific implementations.
    This provides a consistent API for interacting with different exchanges.
    """
    
    @abstractmethod
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
        pass
        
    @abstractmethod
    async def get_balance(self, asset: str) -> Decimal:
        """
        Get available balance for a specific asset.
        
        Args:
            asset: Asset symbol or asset name (e.g., "BTC" or "USDT")
            
        Returns:
            Available balance as a Decimal
        """
        pass
    
    @abstractmethod
    async def get_current_price(self, asset: Asset) -> Decimal:
        """
        Get current market price for an asset.
        
        Args:
            asset: Asset to get price for
            
        Returns:
            Current price as a Decimal
        """
        pass
        
    @abstractmethod
    async def place_market_order(
        self, 
        asset: Asset, 
        direction: PositionDirection, 
        quantity: Decimal,
        # Margin trading parameters (optional)
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
            side_effect_type: Binance margin param (e.g., 'MARGIN_BUY', 'AUTO_REPAY', 'AUTO_BORROW_REPAY')
            
        Returns:
            Exchange order details
            
        Raises:
            Exception: If order placement fails
        """
        pass
        
    @abstractmethod
    async def place_limit_order(
        self, 
        asset: Asset, 
        direction: PositionDirection, 
        quantity: Decimal, 
        price: Decimal,
        # Margin trading parameters (optional)
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
            side_effect_type: Binance margin param (e.g., 'MARGIN_BUY', 'AUTO_REPAY', 'AUTO_BORROW_REPAY')
            
        Returns:
            Exchange order details
            
        Raises:
            Exception: If order placement fails
        """
        pass
        
    @abstractmethod
    async def get_open_positions(self) -> List[Position]:
        """
        Get all open positions from the exchange.
        
        Returns:
            List of open positions
        """
        pass
        
    @abstractmethod
    async def get_order_book(self, asset: Asset, depth: int = 5) -> Dict[str, List[Tuple[Decimal, Decimal]]]:
        """
        Get order book for a specific asset.
        
        Args:
            asset: Asset to get order book for
            depth: Depth of order book to retrieve
            
        Returns:
            Dictionary with 'bids' and 'asks' arrays of [price, quantity] tuples
        """
        pass
        
    @abstractmethod
    async def calculate_optimal_quantity(
        self, 
        asset: Asset, 
        amount: Decimal, 
        direction: PositionDirection
    ) -> Decimal:
        """
        Calculate the optimal quantity that can be traded given an amount of quote currency.
        
        This accounts for exchange-specific requirements like minimum quantities,
        step size, etc. to avoid order rejection.
        
        Args:
            asset: Asset to calculate quantity for
            amount: Amount of quote currency to spend/receive
            direction: Order direction
            
        Returns:
            Optimal quantity that meets exchange requirements
        """
        pass
