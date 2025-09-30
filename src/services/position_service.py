"""
Position service for managing trading positions.
"""
import logging
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional, Any, Tuple

# Import UserSettings to access margin config
from ..core.user_settings import get_settings
from ..core.asset import Asset
from ..core.position import Position, PositionDirection, PositionStatus
from ..core.exchange_adapter import ExchangeAdapter
from ..core.position_repository import PositionRepository
from ..core.logging_config import log_order_execution, log_position_update


logger = logging.getLogger(__name__)


class PositionService:
    """
    Handles position management logic.
    
    This service orchestrates the interaction between the exchange adapter
    and position repository to create, manage, and close trading positions.
    """
    
    def __init__(self, exchange_adapter: ExchangeAdapter, position_repository: PositionRepository):
        """
        Initialize the position service.
        
        Args:
            exchange_adapter: Exchange adapter for interacting with the exchange
            position_repository: Repository for storing and retrieving positions
        """
        self.exchange = exchange_adapter
        self.repository = position_repository
        # Get user settings instance
        self.settings = get_settings() 
        logger.info("PositionService initialized")
    
    async def open_position(
        self,
        asset: Asset,
        direction: PositionDirection,
        amount: Decimal,
        bot_strategy: str,
        timeframe: str,
        bot_settings: str = "default",
        take_profit_max: int = 3
    ) -> Position:
        """
        Open a new trading position.
        
        Args:
            asset: Asset to trade
            direction: Position direction (LONG or SHORT)
            amount: Amount of quote currency to use
            bot_strategy: Strategy identifier
            timeframe: Trading timeframe
            bot_settings: Additional bot configuration
            take_profit_max: Maximum number of take profits
            
        Returns:
            Newly created Position
            
        Raises:
            Exception: If opening the position fails
        """
        logger.info(f"Attempting to open {direction.value} position for {asset.symbol} - Strategy: {bot_strategy}_{bot_settings} @ {timeframe} with amount {amount}")
        
        try:
            # 1. Calculate optimal quantity based on amount and asset rules
            quantity = await self.exchange.calculate_optimal_quantity(asset, amount, direction)
            if quantity <= 0:
                raise ValueError(f"Calculated quantity is zero or less for amount {amount} and asset {asset.symbol}")
            logger.info(f"Calculated optimal quantity: {quantity} {asset.symbol.replace('USDT', '')}") # Assuming USDT quote

            # 2. Get current market price (as fallback)
            current_price = await self.exchange.get_current_price(asset)
            logger.debug(f"Current market price for {asset.symbol}: {current_price}")

            # --- Determine Trade Type and Prepare Margin Parameters --- 
            use_margin = False
            is_isolated = None
            side_effect_type = None
            margin_log_details = None
            position_leverage = Decimal('1')
            position_margin_type = None

            if direction == PositionDirection.SHORT and self.settings.allow_short_trades:
                use_margin = True
                side_effect_type = 'AUTO_BORROW_REPAY' 
                position_leverage = Decimal(self.settings.default_leverage)
                position_margin_type = self.settings.margin_type
                is_isolated = (position_margin_type == 'ISOLATED')
                margin_log_details = f"SHORT/{position_margin_type}/{side_effect_type}/{position_leverage}x"
                logger.info(f"Preparing SHORT margin trade: {margin_log_details}, Isolated: {is_isolated}")
            
            elif direction == PositionDirection.LONG and self.settings.allow_long_trades and self.settings.use_margin_for_longs:
                use_margin = True
                side_effect_type = 'MARGIN_BUY'
                position_leverage = Decimal(self.settings.default_leverage)
                position_margin_type = self.settings.margin_type
                is_isolated = (position_margin_type == 'ISOLATED')
                margin_log_details = f"LONG/{position_margin_type}/{side_effect_type}/{position_leverage}x"
                logger.info(f"Preparing LONG margin trade: {margin_log_details}, Isolated: {is_isolated}")
            
            else:
                # Standard Spot Long (or disallowed short/long)
                logger.info(f"Preparing standard SPOT {direction.value} trade.")
                # Ensure leverage/margin type reflect spot trade
                position_leverage = Decimal('1')
                position_margin_type = None
            # --- End Parameter Preparation ---
            
            # 3. Execute market order on exchange, passing margin params (if use_margin is True)
            logger.info(f"Placing market order: {direction.value} {quantity} {asset.symbol}")
            order = await self.exchange.place_market_order(
                asset=asset, 
                direction=direction, 
                quantity=quantity,
                # Pass params only if use_margin is True, otherwise they are None
                is_isolated=is_isolated if use_margin else None, 
                side_effect_type=side_effect_type if use_margin else None 
            )
            logger.info(f"Market order placed successfully. Order details: {order}")

            # 4. Extract filled price and quantity from order details
            # Use average filled price if available, otherwise fallback
            filled_price = self._get_average_fill_price(order) or current_price
            filled_quantity = Decimal(order.get('executedQty', quantity)) # Use executedQty if available
            order_id = str(order.get('orderId', 'N/A'))

            logger.info(f"Order filled - Price: {filled_price}, Quantity: {filled_quantity}, Order ID: {order_id}")

            # 5. Create position object
            position = Position(
                asset=asset,
                direction=direction,
                initial_quantity=filled_quantity,
                entry_price=filled_price,
                bot_strategy=bot_strategy,
                timeframe=timeframe,
                bot_settings=bot_settings,
                take_profit_max=take_profit_max,
                external_id=order_id, # Store exchange order ID
                leverage=position_leverage, # Store leverage
                margin_type=position_margin_type # Store margin type
            )
            position.remaining_quantity = filled_quantity # Initialize remaining quantity

            # 6. Save position to repository
            await self.repository.save(position)
            logger.info(f"Position {position.id} saved successfully.")
            
            # Log the position opening to the order log
            log_order_execution(
                order_type="ENTRY",
                asset=asset.symbol,
                direction=direction.value,
                quantity=str(filled_quantity),
                price=str(filled_price),
                order_id=order_id,
                success=True,
                margin_details=margin_log_details # Log margin details
            )
            
            log_position_update(
                position_id=position.id,
                asset=asset.symbol,
                direction=direction.value,
                status=position.status.value,
                update_type="OPEN",
                details=f"Strategy: {bot_strategy}_{bot_settings} @ {timeframe}",
                leverage=str(position_leverage), # Log leverage
                margin_type=position_margin_type # Log margin type
            )

            return position

        except Exception as e:
            logger.error(f"Failed to open position for {asset.symbol}: {str(e)}", exc_info=True)
            # Consider specific exception handling or re-raising
            raise # Re-raise the exception for the caller to handle

    def _get_average_fill_price(self, order: Dict[str, Any]) -> Optional[Decimal]:
        """Calculate the average fill price from order fills."""
        fills = order.get('fills', [])
        if not fills:
            return None

        total_cost = Decimal('0')
        total_quantity = Decimal('0')
        for fill in fills:
            price = Decimal(fill.get('price', '0'))
            qty = Decimal(fill.get('qty', '0'))
            total_cost += price * qty
            total_quantity += qty

        if total_quantity > 0:
            return total_cost / total_quantity
        return None

    async def execute_take_profit(
        self,
        position_id: str,
        tp_level: int,
        tp_percentages: Dict[int, Decimal] # Use Decimal for percentages
    ) -> Tuple[Position, Dict[str, Any]]:
        """
        Execute a take profit for an open position.
        
        Args:
            position_id: ID of the position
            tp_level: The take profit level (1, 2, 3)
            tp_percentages: Dictionary of percentages for each TP level
            
        Returns:
            Tuple of (updated position, order details)
            
        Raises:
            ValueError: If position not found or validation fails
            Exception: If execution fails unexpectedly
        """
        # Ensure we have the latest position data
        await self.repository.reload_positions()
        
        position = await self.repository.get_by_id(position_id)
        if not position:
            raise ValueError(f"Position not found: {position_id}")
        
        if position.is_closed:
            raise ValueError("Position already closed")
            
        # Check if the requested TP level has already been executed
        if any(tp.level >= tp_level for tp in position.take_profits):
            raise ValueError(f"TP level {tp_level} or higher has already been executed")
        
        is_last_tp = tp_level == position.take_profit_max
        logger.info(f"Executing TP {tp_level} for position {position_id} - Is final TP: {is_last_tp}")

        # Calculate quantity to execute for this TP level
        tp_number = tp_level
        percentage = tp_percentages.get(tp_number)
        if not percentage:
            raise ValueError(f"No percentage defined for TP level {tp_number}")
            
        # Calculate percentage relative to the previous TP
        # E.g., If TP1=33%, TP2=66%, TP3=100%, then amounts would be 33%, 33%, 34% of initial
        prev_tp_percentage = Decimal("0")
        if tp_number > 1 and tp_number - 1 in tp_percentages:
            prev_tp_percentage = tp_percentages[tp_number - 1]
        
        current_percentage = percentage - prev_tp_percentage
        if current_percentage <= 0:
            raise ValueError(f"Invalid TP percentage configuration: {tp_percentages}")
            
        logger.info(f"TP {tp_level} percentage: {current_percentage}% (calculating from {prev_tp_percentage}% to {percentage}%)")
        
        # Calculate amount to sell at this level (percentage of initial quantity)
        tp_quantity = (current_percentage / Decimal("100")) * position.initial_quantity
        
        # Don't try to TP more than what remains
        quantity_to_execute = min(tp_quantity, position.remaining_quantity)
        
        if quantity_to_execute <= 0:
            raise ValueError(f"No quantity available for TP {tp_level}")
        
        # Apply quantity validation and step size constraints based on exchange requirements
        try:
            # Refresh asset information to ensure we have the latest constraints
            position.asset = await self.exchange.get_asset_info(position.asset.symbol)
            
            # Calculate the adjusted quantity based on step size
            if position.asset.step_size is not None and position.asset.step_size > 0:
                original_quantity = quantity_to_execute
                quantity_to_execute = (quantity_to_execute // position.asset.step_size * position.asset.step_size).quantize(position.asset.step_size, rounding=ROUND_DOWN)
                
                logger.info(f"Adjusted TP quantity from {original_quantity} to {quantity_to_execute} due to step size {position.asset.step_size}")
            
            # Check for minimum quantity requirements
            if position.asset.min_quantity is not None and quantity_to_execute < position.asset.min_quantity:
                logger.warning(f"TP quantity {quantity_to_execute} is below minimum {position.asset.min_quantity} for {position.asset.symbol}")
                
                # If this is the last TP and quantity is too small, close the position instead
                if is_last_tp:
                    logger.info(f"Final TP with small quantity, closing position {position_id} instead")
                    return await self.close_position(position_id, f"Auto-closed due to small adjusted quantity on final TP {tp_level}")
                else:
                    return position, {}
        except Exception as e:
            logger.error(f"Error validating quantity for TP {tp_level} on position {position_id}: {str(e)}")
            # Continue with the original quantity, the exchange adapter will do final validation
            
        logger.info(f"Executing TP {tp_level} for position {position_id} - Quantity: {quantity_to_execute}")

        try:
            # Determine the direction to close the portion (opposite of position direction)
            reverse_direction = (
                PositionDirection.SHORT if position.direction == PositionDirection.LONG
                else PositionDirection.LONG
            )

            # --- Prepare Margin Params for Closing/TP --- 
            is_margin_position = position.leverage > 1 or position.direction == PositionDirection.SHORT
            is_isolated = None
            side_effect_type = None
            margin_log_details = None

            if is_margin_position:
                is_isolated = (position.margin_type == 'ISOLATED')
                # AUTO_REPAY works for closing both leveraged longs and shorts
                side_effect_type = 'AUTO_REPAY' 
                margin_log_details = f"{position.margin_type or 'CROSS'}/{side_effect_type}"
                logger.info(f"Preparing margin TP order. Isolated: {is_isolated}, SideEffect: {side_effect_type}")
            # --- End Margin Params ---

            # Execute the trade on the exchange
            logger.info(f"Placing market order for TP: {reverse_direction.value} {quantity_to_execute} {position.asset.symbol}")
            order = await self.exchange.place_market_order(
                asset=position.asset,
                direction=reverse_direction,
                quantity=quantity_to_execute,
                is_isolated=is_isolated, # Pass margin params
                side_effect_type=side_effect_type # Pass margin params
            )
            logger.info(f"TP market order placed successfully. Order details: {order}")

            # Extract the executed price and quantity
            executed_price = self._get_average_fill_price(order) or await self.exchange.get_current_price(position.asset)
            executed_quantity = Decimal(order.get('executedQty', quantity_to_execute))
            order_id = str(order.get('orderId', 'N/A'))

            logger.info(f"TP {tp_level} order filled - Price: {executed_price}, Quantity: {executed_quantity}, Order ID: {order_id}")

            # Add the take profit event to the position object
            position.add_take_profit(executed_price, executed_quantity, tp_level)

            # Check if the position should be closed after this TP
            # NOTE: We don't need to manually close the position here since the add_take_profit method
            # already handles auto-closing when it's the final TP level or no quantity remains
            # Only log the status
            if position.is_closed:
                logger.info(f"Position {position.id} fully closed after TP {tp_level}.")
            elif is_last_tp and not position.is_closed:
                # If for some reason the position isn't closed yet and it's the last TP level, close it
                logger.info(f"Final TP {tp_level} - Explicitly closing position {position.id}")
                position.close(executed_price, Decimal('0'))

            # Update the position in the repository
            await self.repository.update(position)
            logger.info(f"Position {position.id} updated after TP {tp_level}. Status: {position.status.value}, Remaining Qty: {position.remaining_quantity}")

            # Log the take profit execution to the order log
            log_order_execution(
                order_type=f"TP{tp_level}",
                asset=position.asset.symbol,
                direction=reverse_direction.value,
                quantity=str(executed_quantity),
                price=str(executed_price),
                order_id=order_id,
                success=True,
                margin_details=margin_log_details # Log margin details
            )
            
            log_position_update(
                position_id=position.id,
                asset=position.asset.symbol,
                direction=position.direction.value,
                status=position.status.value,
                update_type=f"TP{tp_level}",
                details=f"Executed qty: {executed_quantity}, Remaining qty: {position.remaining_quantity}",
                leverage=str(position.leverage), # Log leverage
                margin_type=position.margin_type # Log margin type
            )

            return position, order

        except Exception as e:
            logger.error(f"Failed to execute TP {tp_level} for position {position_id}: {str(e)}", exc_info=True)
            # Don't update position state if exchange order failed
            raise # Re-raise the exception

    async def close_position(self, position_id: str, reason: str = "Closure requested") -> Tuple[Position, Optional[Dict[str, Any]]]:
        """
        Close an open trading position fully.
        
        Args:
            position_id: ID of the position to close
            reason: Reason for closing the position
            
        Returns:
            Tuple of (closed position, order details or None if order failed/not needed)
            
        Raises:
            ValueError: If position not found
            Exception: If closing fails unexpectedly
        """
        logger.info(f"Attempting to close position {position_id}. Reason: {reason}")

        # Ensure we have the latest position data
        await self.repository.reload_positions()

        position = await self.repository.get_by_id(position_id)
        if not position:
            raise ValueError(f"Position not found: {position_id}")
        if position.is_closed:
            logger.warning(f"Position {position_id} is already closed. No action taken.")
            return position, None # Return the already closed position

        if position.remaining_quantity <= 0:
            logger.warning(f"Position {position_id} has zero remaining quantity. Marking as closed without exchange order.")
            # Mark as closed if not already, using last known price or current price
            close_price = position.last_price or await self.exchange.get_current_price(position.asset)
            position.close(close_price, Decimal('0'))
            await self.repository.update(position)
            return position, None

        logger.info(f"Position {position_id} has {position.remaining_quantity} remaining. Proceeding with market close order.")

        order = None
        try:
            # Determine the direction to close the position
            reverse_direction = (
                PositionDirection.SHORT if position.direction == PositionDirection.LONG
                else PositionDirection.LONG
            )

            # --- Prepare Margin Params for Closing --- 
            is_margin_position = position.leverage > 1 or position.direction == PositionDirection.SHORT
            is_isolated = None
            side_effect_type = None
            margin_log_details = None
            
            if is_margin_position:
                is_isolated = (position.margin_type == 'ISOLATED')
                side_effect_type = 'AUTO_REPAY'
                margin_log_details = f"{position.margin_type or 'CROSS'}/{side_effect_type}"
                logger.info(f"Preparing margin close order. Isolated: {is_isolated}, SideEffect: {side_effect_type}")
            # --- End Margin Params ---

            # Execute the closing trade on the exchange
            logger.info(f"Placing market order to close: {reverse_direction.value} {position.remaining_quantity} {position.asset.symbol}")
            
            # Apply quantity validation and step size constraints
            try:
                # Refresh asset information to ensure we have the latest constraints
                position.asset = await self.exchange.get_asset_info(position.asset.symbol)
                
                # Get the quantity to execute and apply step size constraints
                quantity_to_execute = position.remaining_quantity
                if position.asset.step_size is not None and position.asset.step_size > 0:
                    original_quantity = quantity_to_execute
                    quantity_to_execute = (quantity_to_execute // position.asset.step_size * position.asset.step_size).quantize(position.asset.step_size, rounding=ROUND_DOWN)
                    logger.info(f"Adjusted close quantity from {original_quantity} to {quantity_to_execute} due to step size {position.asset.step_size}")
                
                # Check if quantity is too small
                if position.asset.min_quantity is not None and quantity_to_execute < position.asset.min_quantity:
                    logger.warning(f"Closing quantity {quantity_to_execute} is below minimum {position.asset.min_quantity} for {position.asset.symbol}")
                    # Still mark the position as closed but don't execute exchange order
                    current_price = await self.exchange.get_current_price(position.asset)
                    position.close(current_price, position.remaining_quantity, reason=f"{reason} (Below minimum quantity)")
                    await self.repository.update(position)
                    logger.info(f"Position {position_id} marked as closed without order (below minimum quantity)")
                    return position, None
            except Exception as e:
                logger.warning(f"Error adjusting quantity for closing position {position_id}: {str(e)}")
                # Continue with original quantity - the exchange adapter will handle final validation

            # Execute the order with the adjusted quantity
            order = await self.exchange.place_market_order(
                position.asset,
                reverse_direction,
                quantity_to_execute,
                is_isolated=is_isolated, # Pass margin params
                side_effect_type=side_effect_type # Pass margin params
            )
            logger.info(f"Close market order placed successfully. Order details: {order}")

            # Extract the executed price and quantity
            executed_price = self._get_average_fill_price(order) or await self.exchange.get_current_price(position.asset)
            # Use the actual remaining quantity for the close event, even if executedQty differs slightly
            executed_quantity = position.remaining_quantity
            order_id = str(order.get('orderId', 'N/A'))

            logger.info(f"Close order filled - Price: {executed_price}, Quantity: {executed_quantity}, Order ID: {order_id}")

            # Close the position in our system
            position.close(executed_price, executed_quantity, reason=reason, external_id=order_id)

            # Update in repository (moves to closed state)
            await self.repository.update(position)
            logger.info(f"Position {position_id} marked as closed successfully in repository.")

            # Log the position closure to the order log
            log_order_execution(
                order_type="CLOSE",
                asset=position.asset.symbol,
                direction=reverse_direction.value,
                quantity=str(executed_quantity),
                price=str(executed_price),
                order_id=order_id,
                success=True,
                details=reason,
                margin_details=margin_log_details # Log margin details
            )
            
            log_position_update(
                position_id=position.id,
                asset=position.asset.symbol,
                direction=position.direction.value,
                status="CLOSED",
                update_type="CLOSE",
                details=reason,
                leverage=str(position.leverage), # Log leverage
                margin_type=position.margin_type # Log margin type
            )

            return position, order

        except Exception as e:
            logger.error(f"Failed to execute close order on exchange for position {position_id}: {str(e)}", exc_info=True)

            # Log the failed order
            log_order_execution(
                order_type="CLOSE",
                asset=position.asset.symbol,
                direction="SELL" if position.direction == PositionDirection.LONG else "BUY",
                quantity=str(position.remaining_quantity),
                price="Unknown",
                order_id="Failed",
                success=False,
                details=f"Error: {str(e)}",
                margin_details="N/A - Order Failed" # Indicate margin order failure
            )

            # Use current price for closing as fill price is unknown
            current_price = await self.exchange.get_current_price(position.asset)
            position.close(
                current_price,
                position.remaining_quantity, # Assume the full quantity failed to close
                reason=f"{reason} (Exchange Failed: {str(e)})",
                margin_details="N/A - Order Failed" # Indicate margin order failure
            )
            await self.repository.update(position)
            
            # Log the virtual position closure
            log_position_update(
                position_id=position.id,
                asset=position.asset.symbol,
                direction=position.direction.value,
                status="CLOSED",
                update_type="VIRTUAL_CLOSE",
                details=f"{reason} (Exchange Failed: {str(e)})",
                leverage=str(position.leverage), # Log leverage
                margin_type=position.margin_type # Log margin type
            )
            
            # Return the locally closed position, but indicate order failure
            return position, None # No successful order details

    async def get_position_details(self, position_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a position including current PnL if open.
        
        Args:
            position_id: ID of the position
            
        Returns:
            Dictionary with position details and analytics, or None if not found
        """
        position = await self.repository.get_by_id(position_id)
        if not position:
            logger.warning(f"Position details requested for non-existent ID: {position_id}")
            return None

        details = position.to_dict() # Get base details

        # If position is open, add current market data and PnL
        if position.status == PositionStatus.OPEN:
            try:
                current_price = await self.exchange.get_current_price(position.asset)
                details['current_price'] = str(current_price)
                details['unrealized_pnl'] = str(position.get_unrealized_pnl(current_price))
                details['realized_pnl'] = str(position.get_realized_pnl())
                details['total_pnl'] = str(position.get_total_pnl(current_price))
                details['pnl_percentage'] = f"{position.get_pnl_percentage(current_price):.2f}%"
            except Exception as e:
                logger.error(f"Could not fetch current price or calculate PnL for open position {position_id}: {str(e)}")
                details['current_price'] = 'Error'
                details['total_pnl'] = 'Error'
                details['pnl_percentage'] = 'Error'
        else:
            # For closed positions, PnL is fixed
            details['total_pnl'] = str(position.get_total_pnl()) # Uses close price
            details['pnl_percentage'] = f"{position.get_pnl_percentage():.2f}%" # Uses close price

        return details
    
    async def get_open_positions(self, filters: Optional[Dict[str, Any]] = None) -> List[Position]:
        """
        Get all open positions, optionally filtered.
        
        Args:
            filters: Optional dictionary of filter criteria (e.g., {'bot_strategy': 'my_strat'})
            
        Returns:
            List of matching open positions
        """
        return await self.repository.get_open_positions(filters)
    
    async def get_closed_positions(self, filters: Optional[Dict[str, Any]] = None) -> List[Position]:
        """
        Get all closed positions, optionally filtered.
        
        Args:
            filters: Optional dictionary of filter criteria
            
        Returns:
            List of matching closed positions
        """
        return await self.repository.get_closed_positions(filters)
    
    # Example filter usage wrappers:
    async def get_open_positions_for_strategy(self, bot_strategy: str, bot_settings: Optional[str] = None) -> List[Position]:
        """Get open positions filtered by strategy and optionally settings."""
        filters = {'bot_strategy': bot_strategy}
        if bot_settings:
            filters['bot_settings'] = bot_settings
        return await self.get_open_positions(filters)
    
    async def get_open_positions_for_asset(self, asset_symbol: str) -> List[Position]:
        """Get open positions filtered by asset symbol."""
        filters = {'asset': asset_symbol}
        return await self.get_open_positions(filters)
