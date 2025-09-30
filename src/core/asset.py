"""
Asset domain model representing a tradable asset with associated properties.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class Asset:
    """
    Represents a tradable asset with its properties.
    
    Attributes:
        symbol: Asset symbol (e.g., BTCUSDT)
        asset_type: Type of asset (e.g., "crypto", "stock", etc.)
        exchange_id: Identifier for the exchange where the asset is traded
        min_quantity: Minimum tradable quantity
        max_quantity: Maximum tradable quantity 
        step_size: Size of quantity increments
        price_precision: Number of decimal places for price
        quote_precision: Number of decimal places for quote currency
    """
    symbol: str
    asset_type: str  # "crypto", "stock", etc.
    exchange_id: str
    min_quantity: Optional[Decimal] = None
    max_quantity: Optional[Decimal] = None
    step_size: Optional[Decimal] = None
    price_precision: Optional[int] = None
    quote_precision: Optional[int] = None

    def __post_init__(self):
        """Convert numeric string values to Decimal if they're strings"""
        for attr in ['min_quantity', 'max_quantity', 'step_size']:
            val = getattr(self, attr)
            if isinstance(val, str):
                setattr(self, attr, Decimal(val))

    def ensure_valid_quantity(self, quantity: Decimal) -> Decimal:
        """
        Adjusts a quantity to ensure it meets the exchange requirements
        
        Args:
            quantity: The desired quantity
            
        Returns:
            An adjusted quantity that meets all exchange requirements
        """
        # Enforce min and max constraints
        if self.min_quantity is not None:
            quantity = max(self.min_quantity, quantity)
        if self.max_quantity is not None:
            quantity = min(self.max_quantity, quantity)
            
        # Apply step size if available
        if self.step_size is not None and self.step_size > 0:
            # Truncate to the step size using ROUND_DOWN for Binance API
            from decimal import ROUND_DOWN
            quantity = (quantity // self.step_size * self.step_size).quantize(self.step_size, rounding=ROUND_DOWN)
            
            # Check again if we're below minimum after applying step size
            if self.min_quantity is not None and quantity < self.min_quantity:
                # This can happen due to rounding down
                return Decimal('0')  # Return zero to indicate invalid quantity
            
        return quantity
