"""
Position domain model representing a trading position.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Dict, Optional, Union

from .asset import Asset


class PositionDirection(str, Enum):
    """Represents the direction of a trading position."""
    LONG = "LONG"
    SHORT = "SHORT"


class PositionStatus(str, Enum):
    """Represents the status of a trading position."""
    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass
class TakeProfit:
    """
    Represents a take profit execution for a position.
    
    Attributes:
        level: The take profit level (1, 2, 3, etc.)
        price: The price at which the take profit was executed
        quantity: The quantity that was sold/bought for this take profit
        timestamp: When the take profit was executed
    """
    level: int
    price: Decimal
    quantity: Decimal
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def value(self) -> Decimal:
        """Calculate the value of this take profit execution."""
        return self.price * self.quantity


@dataclass
class Position:
    """
    Represents a trading position, agnostic of the exchange.
    
    Attributes:
        id: Unique identifier for the position
        asset: The asset being traded
        direction: Direction of the position (LONG or SHORT)
        initial_quantity: Initial quantity at position opening
        entry_price: Entry price of the position
        bot_strategy: Strategy identifier
        timeframe: Timeframe of the trading strategy
        leverage: Leverage used (1 for spot, >1 for margin/futures)
        margin_type: Margin mode used ('CROSSED' or 'ISOLATED', None if not applicable)
        timestamp: When the position was opened
        take_profits: List of executed take profits
        status: Current status of the position (OPEN or CLOSED)
        remaining_quantity: Quantity remaining in the position
        bot_settings: Additional bot-specific settings
        take_profit_max: Maximum number of take profits for this position
        external_id: External reference (e.g., exchange order ID)
        close_data: Data about position closure (if closed)
    """
    asset: Asset
    direction: PositionDirection
    initial_quantity: Decimal
    entry_price: Decimal
    bot_strategy: str
    timeframe: str
    bot_settings: str = "default"
    leverage: Decimal = Decimal("1")
    margin_type: Optional[str] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.now)
    take_profits: List[TakeProfit] = field(default_factory=list)
    status: PositionStatus = PositionStatus.OPEN
    remaining_quantity: Optional[Decimal] = None
    take_profit_max: int = 3
    external_id: Optional[str] = None
    close_data: Optional[Dict] = None
    
    def __post_init__(self):
        """Initialize derived fields and convert types."""
        # Ensure PositionDirection is correct type
        if isinstance(self.direction, str):
            self.direction = PositionDirection(self.direction.upper())
        
        # Ensure PositionStatus is correct type
        if isinstance(self.status, str):
            self.status = PositionStatus(self.status.upper())
            
        # Set default remaining_quantity if not provided
        if self.remaining_quantity is None:
            self.remaining_quantity = self.initial_quantity
    
    @property
    def initial_value(self) -> Decimal:
        """Calculate the initial value of the position."""
        return self.entry_price * self.initial_quantity
    
    @property
    def take_profit_count(self) -> int:
        """Get the number of take profits executed."""
        return len(self.take_profits)
    
    @property
    def last_tp_level(self) -> int:
        """Get the highest take profit level executed so far, or 0 if none."""
        if not self.take_profits:
            return 0
        return max(tp.level for tp in self.take_profits)
    
    @property
    def is_closed(self) -> bool:
        """Check if the position is closed."""
        return self.status == PositionStatus.CLOSED
    
    def add_take_profit(self, price: Decimal, quantity: Decimal, level: int) -> TakeProfit:
        """
        Add a take profit execution to the position.
        
        Args:
            price: The price at which the take profit was executed
            quantity: The quantity that was sold/bought for this take profit
            level: The take profit level (1, 2, 3, etc.)
            
        Returns:
            The created TakeProfit instance
        """
        # Don't allow adding take profits to closed positions
        if self.is_closed:
            raise ValueError("Cannot add take profit to closed position")
        
        # Don't allow take profit levels greater than max
        if level > self.take_profit_max:
            raise ValueError(f"Take profit level {level} exceeds maximum {self.take_profit_max}")
        
        # Don't allow adding the same take profit level twice
        if any(tp.level == level for tp in self.take_profits):
            raise ValueError(f"Take profit level {level} already executed")
        
        # Ensure we're not selling more than we have
        adjusted_quantity = min(quantity, self.remaining_quantity)
        if adjusted_quantity <= 0:
            raise ValueError("No quantity available for take profit")
        
        take_profit = TakeProfit(
            level=level,
            price=price,
            quantity=adjusted_quantity,
            timestamp=datetime.now()
        )
        
        self.take_profits.append(take_profit)
        self.remaining_quantity -= adjusted_quantity
        
        # Auto-close if this was the final take profit or no quantity remains
        if level == self.take_profit_max or self.remaining_quantity <= 0:
            self.close(price, Decimal("0"))
            
        return take_profit
    
    def close(self, price: Decimal, quantity: Decimal, reason: str = "Manual close", external_id: Optional[str] = None) -> None:
        """
        Close the position (or a portion of it).
        
        Args:
            price: The closing price
            quantity: The quantity to close (if < remaining, it's a partial close)
            reason: Reason for closing the position
            external_id: External reference (e.g., exchange order ID)
        """
        # Don't allow closing already closed positions
        if self.is_closed:
            raise ValueError("Position already closed")
        
        # Adjust closing quantity to avoid closing more than we have
        adjusted_quantity = min(quantity, self.remaining_quantity)
        
        self.close_data = {
            'timestamp': datetime.now().isoformat(),
            'price': str(price),
            'quantity': str(adjusted_quantity),
            'value': str(price * adjusted_quantity),
            'reason': reason
        }
        
        # Store external ID if provided
        if external_id:
            self.close_data['external_id'] = external_id
        
        self.remaining_quantity -= adjusted_quantity
        
        # Mark as closed if no quantity remains
        if self.remaining_quantity <= 0:
            self.status = PositionStatus.CLOSED
            self.remaining_quantity = Decimal('0')  # Ensure it's exactly zero
    
    def get_unrealized_pnl(self, current_price: Decimal) -> Decimal:
        """
        Calculate unrealized profit/loss for the position at the given price.
        
        Args:
            current_price: Current market price for the asset
            
        Returns:
            Unrealized PnL in quote currency
        """
        if self.is_closed or self.remaining_quantity <= 0:
            return Decimal("0")
            
        if self.direction == PositionDirection.LONG:
            return (current_price - self.entry_price) * self.remaining_quantity
        else:  # SHORT
            return (self.entry_price - current_price) * self.remaining_quantity
    
    def get_realized_pnl(self) -> Decimal:
        """
        Calculate realized profit/loss from take profits and closing.
        
        Returns:
            Realized PnL in quote currency
        """
        tp_value = sum(tp.price * tp.quantity for tp in self.take_profits)
        tp_quantity = sum(tp.quantity for tp in self.take_profits)
        
        close_value = Decimal("0")
        close_quantity = Decimal("0")
        if self.close_data:
            close_value = Decimal(self.close_data['price']) * Decimal(self.close_data['quantity'])
            close_quantity = Decimal(self.close_data['quantity'])
        
        total_value = tp_value + close_value
        total_quantity = tp_quantity + close_quantity
        
        if self.direction == PositionDirection.LONG:
            return total_value - (self.entry_price * total_quantity)
        else:  # SHORT
            return (self.entry_price * total_quantity) - total_value
    
    def get_total_pnl(self, current_price: Decimal) -> Decimal:
        """
        Calculate total profit/loss (realized + unrealized).
        
        Args:
            current_price: Current market price for the asset
            
        Returns:
            Total PnL in quote currency
        """
        return self.get_realized_pnl() + self.get_unrealized_pnl(current_price)
    
    def get_pnl_percentage(self, current_price: Decimal) -> Decimal:
        """
        Calculate profit/loss as a percentage of initial value.
        
        Args:
            current_price: Current market price for the asset
            
        Returns:
            PnL percentage (e.g., 5.25 for 5.25%)
        """
        if self.initial_value <= 0:
            return Decimal("0")
            
        return (self.get_total_pnl(current_price) / self.initial_value) * Decimal("100")
    
    def to_dict(self) -> Dict:
        """
        Convert position to a dictionary for serialization.
        
        Returns:
            Dictionary representation of the position
        """
        return {
            'id': self.id,
            'external_id': self.external_id,
            'asset': self.asset.symbol,
            'direction': self.direction.value,
            'initial_quantity': str(self.initial_quantity),
            'remaining_quantity': str(self.remaining_quantity),
            'entry_price': str(self.entry_price),
            'bot_strategy': self.bot_strategy,
            'bot_settings': self.bot_settings,
            'timeframe': self.timeframe,
            'leverage': str(self.leverage),
            'margin_type': self.margin_type,
            'timestamp': self.timestamp.isoformat(),
            'status': self.status.value,
            'take_profit_max': self.take_profit_max,
            'take_profits': [
                {
                    'level': tp.level,
                    'price': str(tp.price),
                    'quantity': str(tp.quantity),
                    'timestamp': tp.timestamp.isoformat(),
                    'value': str(tp.value)
                }
                for tp in self.take_profits
            ],
            'close_data': self.close_data
        }
    
    @classmethod
    def from_dict(cls, data: Dict, asset: Asset) -> 'Position':
        """
        Create a Position instance from a dictionary.
        
        Args:
            data: Dictionary with position data
            asset: Asset instance for this position
            
        Returns:
            New Position instance
        """
        # Convert take profits data
        take_profits = []
        for tp_data in data.get('take_profits', []):
            take_profits.append(TakeProfit(
                level=tp_data['level'],
                price=Decimal(tp_data['price']),
                quantity=Decimal(tp_data['quantity']),
                timestamp=datetime.fromisoformat(tp_data['timestamp'])
            ))
        
        # Create the position
        return cls(
            id=data.get('id', str(uuid.uuid4())),
            asset=asset,
            direction=data['direction'],
            initial_quantity=Decimal(data['initial_quantity']),
            remaining_quantity=Decimal(data.get('remaining_quantity', data['initial_quantity'])),
            entry_price=Decimal(data['entry_price']),
            bot_strategy=data['bot_strategy'],
            timeframe=data['timeframe'],
            bot_settings=data.get('bot_settings', 'default'),
            leverage=Decimal(data.get('leverage', '1')),
            margin_type=data.get('margin_type'),
            timestamp=datetime.fromisoformat(data['timestamp']),
            take_profits=take_profits,
            status=data.get('status', PositionStatus.OPEN.value),
            take_profit_max=data.get('take_profit_max', 3),
            external_id=data.get('external_id'),
            close_data=data.get('close_data')
        )
