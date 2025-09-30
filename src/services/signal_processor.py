"""
Signal processor service for processing trading signals.
"""
import logging
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Optional, List, Tuple

from ..core.asset import Asset
from ..core.position import Position, PositionDirection
from ..core.exchange_adapter import ExchangeAdapter
from .position_service import PositionService
from ..core.config import (
    DEFAULT_TRADE_AMOUNT,
    DEFAULT_TP_PERCENTAGES_3,
    DEFAULT_TP_PERCENTAGES_4,
    TELEGRAM_NOTIFICATION_ENABLED
)

# Import the Telegram notification components
from ..telegram.telegram_bot import get_telegram_bot

# Import user settings for dynamic access
from ..core.user_settings import get_settings


logger = logging.getLogger(__name__)


class SignalProcessor:
    """
    Processes incoming trading signals and dispatches them to appropriate handlers.
    
    This service translates external signals into actions on positions, handling
    entry signals, take profit signals, and stop signals.
    """
    
    def __init__(
        self, 
        position_service: PositionService, 
        exchange_adapter: ExchangeAdapter,
        default_tp_config_3: Dict[int, int] = DEFAULT_TP_PERCENTAGES_3,
        default_tp_config_4: Dict[int, int] = DEFAULT_TP_PERCENTAGES_4,
        default_trade_amount: Decimal = DEFAULT_TRADE_AMOUNT
    ):
        """
        Initialize the signal processor.
        
        Args:
            position_service: Service for managing positions
            exchange_adapter: Exchange adapter for market operations
            default_tp_config_3: Default 3-level TP percentages by level (int)
            default_tp_config_4: Default 4-level TP percentages by level (int)
            default_trade_amount: Default trade amount if not specified in signal
        """
        self.position_service = position_service
        self.exchange = exchange_adapter
        self.default_tp_config_3 = {k: Decimal(v) for k, v in default_tp_config_3.items()}
        self.default_tp_config_4 = {k: Decimal(v) for k, v in default_tp_config_4.items()}
        self.default_trade_amount = default_trade_amount
        
        # Initialize notification flag from config
        self.notifications_enabled = TELEGRAM_NOTIFICATION_ENABLED
        logger.info(
            f"SignalProcessor initialized (Notifications: {'Enabled' if self.notifications_enabled else 'Disabled'})"
        )
    
    @property
    def allow_long(self) -> bool:
        """Dynamically get the allow_long setting."""
        return get_settings().allow_long_trades
    
    @property
    def allow_short(self) -> bool:
        """Dynamically get the allow_short setting."""
        return get_settings().allow_short_trades
    
    @property
    def trading_parameters(self) -> Dict[str, Any]:
        """Dynamically get trading parameters."""
        return get_settings().trading_parameters
    
    async def process_signal(self, signal_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a trading signal and take appropriate action.
        
        Args:
            signal_data: Dictionary of signal parameters
            
        Returns:
            Result of signal processing with status and details
            
        Raises:
            ValueError: If signal is invalid or missing required parameters
            Exception: If signal processing fails for other reasons
        """
        try:
            # Ensure we have the latest position data
            await self.position_service.repository.reload_positions()
            
            logger.info(f"Processing trading signal: {signal_data}")
            
            # Clean the signal data (format values, remove placeholders)
            cleaned_data = self._clean_signal_data(signal_data)
            
            # Extract required parameters
            command = cleaned_data.get("command", "").upper().strip()
            asset_symbol = cleaned_data.get("asset", "")
            interval = cleaned_data.get("interval", "")
            
            # Validate basic parameters
            if not command:
                raise ValueError("Command parameter is required")
            if not asset_symbol:
                raise ValueError("Asset parameter is required")
            if not interval:
                raise ValueError("Interval parameter is required")
            
            # Extract bot strategy components
            bot_raw = cleaned_data.get("bot", "")
            bot_settings_raw = cleaned_data.get("botSettings", None)
            
            # Split bot strategy if it contains an underscore
            if not bot_settings_raw and "_" in bot_raw:
                parts = bot_raw.split("_", 1)
                bot_strategy = parts[0]
                bot_settings = parts[1] if len(parts) > 1 else "default"
            else:
                bot_strategy = bot_raw
                bot_settings = bot_settings_raw or "default"
            
            if not bot_strategy:
                raise ValueError("Bot strategy (bot) parameter is required")
            
            # Create Asset object with full details from exchange
            asset = await self.exchange.get_asset_info(asset_symbol)
            
            # Process different command types
            if command in ["LONG", "SHORT"]:
                return await self._process_entry_signal(command, asset, bot_strategy, interval, cleaned_data, bot_settings)
            elif command.startswith("TP") or command.startswith("TPS"):
                # More explicit handling of TP commands to distinguish between TP (long) and TPS (short)
                return await self._process_take_profit_signal(command, asset, bot_strategy, interval, cleaned_data, bot_settings)
            elif command in ["STOP L", "STOPL", "STOP S", "STOPS"]:
                return await self._process_stop_signal(command, asset, bot_strategy, interval, cleaned_data, bot_settings)
            else:
                raise ValueError(f"Unknown command: {command}")
                
        except Exception as e:
            logger.error(f"Error processing signal: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def _clean_signal_data(self, signal_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Clean signal data by handling TradingView placeholder values.
        
        Args:
            signal_data: Raw signal data from webhook
            
        Returns:
            Cleaned signal data with placeholder values removed or replaced
        """
        cleaned_data = {}
        
        # Process each key-value pair
        for key, value in signal_data.items():
            # Skip empty values
            if not value:
                continue
                
            # Check if value is a TradingView placeholder still - skip it or use a default
            if isinstance(value, str) and value.startswith('{{') and value.endswith('}}'):
                # Log that we're ignoring a placeholder value
                logger.warning(f"Ignoring placeholder value for '{key}': {value}")
                
                # For critical parameters, we might set defaults instead of skipping
                if key == "command" and "strategy.order.action" in value:
                    # Common TradingView placeholder for order action
                    logger.warning(f"Cannot process command with placeholder: {value}")
                elif key == "asset" and "ticker" in value:
                    # Common TradingView placeholder for asset
                    logger.warning(f"Cannot process asset with placeholder: {value}")
                
                # Skip this parameter since it has a placeholder
                continue
            
            # Store the clean value
            cleaned_data[key] = value
        
        return cleaned_data
    
    async def _process_entry_signal(
        self, 
        command: str, 
        asset: Asset, 
        bot_strategy: str, 
        interval: str,
        signal_data: Dict[str, Any],
        bot_settings: str
    ) -> Dict[str, Any]:
        """
        Process an entry signal (LONG or SHORT).
        
        Args:
            command: Entry command (LONG or SHORT)
            asset: Asset to trade
            bot_strategy: Bot identifier
            interval: Trading interval
            signal_data: Complete signal data
            bot_settings: Bot configuration settings
            
        Returns:
            Result of signal processing
        """
        direction = PositionDirection.LONG if command == 'LONG' else PositionDirection.SHORT
        logger.info(f"Processing {command} entry signal for {asset.symbol} - {bot_strategy}_{bot_settings}...")
        
        # Get trade amount (calculate early, might be needed for logs/checks)
        amount = self._calculate_trade_amount(signal_data)
        # Get max take profit setting
        max_tp = int(signal_data.get("maxTP", 3)) # Default to 3 TP levels
        
        # --- Check for and close opposite positions --- 
        opposite_direction = PositionDirection.SHORT if direction == PositionDirection.LONG else PositionDirection.LONG
        positions_to_close = await self._find_matching_positions(
            asset_symbol=asset.symbol, 
            bot_strategy=bot_strategy, 
            interval=interval,
            bot_settings=bot_settings,
            direction_filter=opposite_direction # Filter by opposite direction
        )
        
        closed_opposite = False
        if positions_to_close:
            logger.warning(f"Found {len(positions_to_close)} opposite position(s) for {asset.symbol} - {bot_strategy}_{bot_settings}. Closing them before potentially opening {command}.")
            close_results = []
            for pos in positions_to_close:
                try:
                    closed_pos, order_info = await self.position_service.close_position(pos.id, f"Closed due to opposite {command} signal")
                    close_results.append({"position_id": closed_pos.id, "success": True})
                    await self._send_notification_position_closed(closed_pos, order_info, reason=f"Closed due to opposite {command} signal")
                    closed_opposite = True # Mark that we attempted closure
                except Exception as e:
                    logger.error(f"Failed to auto-close opposite position {pos.id}: {str(e)}")
                    close_results.append({"position_id": pos.id, "success": False, "error": str(e)})
            
            # Log if any closures failed, but proceed for now
            if any(not r["success"] for r in close_results):
                 logger.error("Failed to close one or more opposite positions. Proceeding with caution.")
        # --- End close opposite positions ---
        
        # --- Now check if the INCOMING signal direction is allowed --- 
        if direction == PositionDirection.LONG and not self.allow_long:
            message = f"Long trades are disabled. Ignoring LONG signal for {asset.symbol}."
            if closed_opposite:
                message = f"Closed opposite position(s), but Long trades are disabled. Ignoring LONG signal for {asset.symbol}."
            logger.warning(message)
            # Return success, indicating signal processed but trade ignored due to config
            return {"success": True, "message": message, "action_taken": "ignored", "closed_opposite": closed_opposite}
        
        if direction == PositionDirection.SHORT and not self.allow_short:
            message = f"Short trades are disabled. Ignoring SHORT signal for {asset.symbol}."
            if closed_opposite:
                message = f"Closed opposite position(s), but Short trades are disabled. Ignoring SHORT signal for {asset.symbol}."
            logger.warning(message)
            # Return success, indicating signal processed but trade ignored due to config
            return {"success": True, "message": message, "action_taken": "ignored", "closed_opposite": closed_opposite}
        # --- End direction check --- 

        # If we reach here, the direction is allowed, and opposites are handled.
        logger.info(f"Proceeding to open {command} position for {asset.symbol} with amount {amount}.")
        
        # Open the new position
        position = await self.position_service.open_position(
            asset=asset,
            direction=direction,
            amount=amount,
            bot_strategy=bot_strategy,
            timeframe=interval,
            bot_settings=bot_settings,
            take_profit_max=max_tp
        )
        
        # Send notification for opened position
        await self._send_notification_position_opened(position, {})
        
        return {
            "success": True, 
            "command": command,
            "position_id": position.id,
            "message": f"{command} position opened successfully for {asset.symbol}",
            "details": position.to_dict() # Include position details in response
        }
    
    async def _process_take_profit_signal(
        self, 
        command: str, 
        asset: Asset, 
        bot_strategy: str, 
        interval: str,
        signal_data: Dict[str, Any],
        bot_settings: str
    ) -> Dict[str, Any]:
        """
        Process a take profit signal (TP n or TPS n).
        
        Args:
            command: Take profit command (TP1, TP 1, etc.)
            asset: Asset being traded
            bot_strategy: Bot identifier
            interval: Trading interval
            signal_data: Complete signal data
            bot_settings: Bot configuration settings
            
        Returns:
            Result of signal processing
            
        Note: 
            If a higher TP level is received before lower levels (e.g., TP2 before TP1),
            this method will execute all missing TPs in sequence to ensure proper handling.
            
            "TP" signals are for LONG positions, while "TPS" signals are for SHORT positions.
        """
        try:
            # Determine position direction based on command prefix
            is_short_tp = command.upper().startswith("TPS")
            position_direction = PositionDirection.SHORT if is_short_tp else PositionDirection.LONG
            direction_name = "SHORT" if is_short_tp else "LONG"
            
            # Handle both "TP 1" and "TP1" formats, as well as "TPS 1" and "TPS1" formats
            if ' ' in command:
                # Format: "TP 1" or "TPS 1"
                tp_level = int(command.split(' ')[1])
            else:
                # Format: "TP1" or "TPS1"
                tp_level = int(command.replace('TPS', '').replace('TP', ''))
        except (IndexError, ValueError):
            raise ValueError(f"Invalid TP command format: {command}. Expected 'TP n', 'TPn', 'TPS n', or 'TPSn'.")
        
        logger.info(f"Processing {direction_name} TP {tp_level} signal for {asset.symbol} - {bot_strategy}_{bot_settings}...")
        
        # Find potentially matching open positions WITH direction filter
        matching_positions = await self._find_matching_positions(
            asset.symbol, bot_strategy, interval, bot_settings, 
            direction_filter=position_direction  # Add direction filter
        )
        
        if not matching_positions:
            raise ValueError(f"No open {direction_name} positions found for {asset.symbol} - {bot_strategy}_{bot_settings} @ {interval}")
        
        # Log found positions for debugging
        logger.info(f"Found {len(matching_positions)} potential {direction_name} positions for TP {tp_level}:")
        for pos in matching_positions:
            logger.info(f"  - Position {pos.id}: remaining qty={pos.remaining_quantity}, TPs={len(pos.take_profits)}/{pos.take_profit_max}, status={pos.status.value}")
        
        # Determine the correct position for this TP level
        position_to_tp = self._find_position_for_tp(matching_positions, tp_level)
        
        if not position_to_tp:
            # This can happen if TP signal arrives late or duplicates
            logger.warning(f"No suitable {direction_name} position found for TP {tp_level} among {len(matching_positions)} candidates for {asset.symbol} - {bot_strategy}_{bot_settings}. Signal might be duplicate or late.")
            raise ValueError(f"No suitable {direction_name} position found for TP {tp_level} (already executed or position closed?)")
        
        logger.info(f"Selected {direction_name} position {position_to_tp.id} for TP {tp_level} - Current remaining qty: {position_to_tp.remaining_quantity}")
        
        # Get TP configuration (default or from signal)
        tp_config = self._get_take_profit_config(signal_data, position_to_tp.take_profit_max)
        
        # Check if there are missing TP levels that should be executed first
        last_tp_executed = position_to_tp.last_tp_level
        missing_tp_levels = []
        for level in range(last_tp_executed + 1, tp_level):
            missing_tp_levels.append(level)
        
        # Initialize updated_position - will be updated during processing
        updated_position = position_to_tp
        executed_tp_details = []
        
        if missing_tp_levels:
            logger.warning(f"Received {direction_name} TP {tp_level} but previous levels {missing_tp_levels} not executed yet. Executing missing levels first.")
            
            # Execute each missing TP level in sequence
            for level in missing_tp_levels:
                try:
                    logger.info(f"Executing missing {direction_name} TP level {level} before requested level {tp_level}")
                    updated_position, order_details = await self.position_service.execute_take_profit(
                        position_id=updated_position.id,
                        tp_level=level,
                        tp_percentages=tp_config
                    )
                    
                    # Get TP details for notification
                    executed_tp = next((tp for tp in updated_position.take_profits if tp.level == level), None)
                    if executed_tp:
                        # Send notification for each executed TP
                        await self._send_notification_take_profit(
                            position=updated_position,
                            tp_level=level,
                            price=executed_tp.price,
                            quantity=executed_tp.quantity,
                            order_details=order_details
                        )
                        
                        executed_tp_details.append({
                            "level": level,
                            "price": str(executed_tp.price),
                            "quantity": str(executed_tp.quantity)
                        })
                    
                    logger.info(f"Missing {direction_name} TP {level} executed successfully. Position remaining qty: {updated_position.remaining_quantity}")
                    
                    # If position is closed after any of the missing TPs, we're done
                    if updated_position.is_closed:
                        logger.info(f"{direction_name} position {updated_position.id} closed after executing missing TP {level}. Won't execute requested TP {tp_level}.")
                        
                        # Send closure notification
                        await self._send_notification_position_closed(
                            position=updated_position,
                            order_details=order_details,
                            reason=f"{direction_name} position fully closed after TP {level} (triggered by TP {tp_level} request)"
                        )
                        
                        return {
                            "success": True,
                            "command": command,
                            "position_id": updated_position.id,
                            "tp_level": level,
                            "position_direction": direction_name,
                            "message": f"{direction_name} position closed after executing missing TP {level} (original request was for TP {tp_level})",
                            "position_closed": True,
                            "executed_missing_tps": executed_tp_details,
                            "details": updated_position.to_dict()
                        }
                        
                except Exception as e:
                    logger.error(f"Error executing missing {direction_name} TP {level}: {str(e)}", exc_info=True)
                    # Continue to the next level or the requested TP if possible
        
        # Now execute the originally requested TP level (if position is still open)
        if not updated_position.is_closed:
            logger.info(f"Now executing originally requested {direction_name} TP {tp_level}")
            updated_position, order_details = await self.position_service.execute_take_profit(
                position_id=updated_position.id,
                tp_level=tp_level,
                tp_percentages=tp_config
            )
            
            logger.info(f"{direction_name} take profit {tp_level} executed for position {updated_position.id}")
            logger.info(f"Position status after TP: status={updated_position.status.value}, remaining={updated_position.remaining_quantity}")
            
            # Verify position status - it should be closed if it was the final TP or no quantity remains
            is_final_tp = tp_level == updated_position.take_profit_max
            should_be_closed = is_final_tp or updated_position.remaining_quantity <= 0
            
            if should_be_closed and not updated_position.is_closed:
                logger.warning(f"{direction_name} position {updated_position.id} should be closed after TP {tp_level} but isn't. Forcing closure.")
                
                # Force closure of the position
                updated_position, close_order = await self.position_service.close_position(
                    position_id=updated_position.id,
                    reason=f"Force-closed after {direction_name} TP {tp_level} ({'final TP' if is_final_tp else 'zero quantity'})"
                )
                
                logger.info(f"{direction_name} position {updated_position.id} forcibly closed: status={updated_position.status.value}")
            
            # Get TP details for the notification
            executed_tp = next((tp for tp in updated_position.take_profits if tp.level == tp_level), None)
            if executed_tp:
                # Send notification for TP execution
                await self._send_notification_take_profit(
                    position=updated_position,
                    tp_level=tp_level,
                    price=executed_tp.price,
                    quantity=executed_tp.quantity,
                    order_details=order_details
                )
                
                # If position is closed after this TP, also send closure notification
                if updated_position.is_closed:
                    await self._send_notification_position_closed(
                        position=updated_position,
                        order_details=order_details,
                        reason=f"{direction_name} position fully closed after TP {tp_level}"
                    )
        
        result = {
            "success": True,
            "command": command,
            "position_id": updated_position.id,
            "tp_level": tp_level,
            "position_direction": direction_name,
            "message": f"{direction_name} TP {tp_level} executed successfully for {asset.symbol}",
            "position_closed": updated_position.is_closed,
            "details": updated_position.to_dict() # Include position details in response
        }
        
        # Add info about missing TPs if any were executed
        if missing_tp_levels:
            result["executed_missing_tps"] = executed_tp_details
            result["message"] = f"Executed missing {direction_name} TPs {missing_tp_levels} and requested TP {tp_level} for {asset.symbol}"
            
        return result
    
    async def _process_stop_signal(
        self, 
        command: str, 
        asset: Asset, 
        bot_strategy: str, 
        interval: str,
        signal_data: Dict[str, Any],
        bot_settings: str
    ) -> Dict[str, Any]:
        """
        Process a stop signal (STOP L or STOP S).
        
        Args:
            command: Stop command (STOP L, STOPL, STOP S, or STOPS)
            asset: Asset being traded
            bot_strategy: Bot identifier
            interval: Trading interval
            signal_data: Complete signal data
            bot_settings: Bot configuration settings
            
        Returns:
            Result of signal processing
        """
        try:
            # Handle both "STOP L" and "STOPL" formats
            if ' ' in command:
                # Format: "STOP L"
                position_type = command.split(' ')[1]
            else:
                # Format: "STOPL"
                position_type = command.replace('STOP', '')
        except IndexError:
             raise ValueError(f"Invalid STOP command format: {command}. Expected 'STOP L', 'STOPL', 'STOP S', or 'STOPS'.")
        
        direction_map = {'L': PositionDirection.LONG, 'S': PositionDirection.SHORT}
        if position_type not in direction_map:
            raise ValueError(f"Invalid position type in STOP command: {position_type}. Must be L or S.")
        
        target_direction = direction_map[position_type]
        logger.info(f"Processing STOP {position_type} signal for {target_direction.value} positions...")
        
        # Find matching open positions
        matching_positions = await self._find_matching_positions(
            asset.symbol, bot_strategy, interval, bot_settings
        )
        
        # Filter by the target direction
        positions_to_close = [p for p in matching_positions if p.direction == target_direction]
        
        if not positions_to_close:
            # It's not necessarily an error if no positions match (e.g., already closed)
            logger.warning(f"No matching open {target_direction.value} positions found for STOP {position_type} signal: {asset.symbol} - {bot_strategy}_{bot_settings}")
            return {
                "success": True, # Signal processed, even if no action taken
                "command": command,
                "message": f"No open {target_direction.value} positions found to close.",
                "results": []
            }
        
        # Close all matching positions
        logger.info(f"Found {len(positions_to_close)} {target_direction.value} position(s) to close.")
        results = []
        for position in positions_to_close:
            try:
                closed_pos, order_info = await self.position_service.close_position(
                    position.id,
                    f"Closed by STOP {position_type} signal"
                )
                results.append({"position_id": position.id, "success": True, "order_details": order_info})
                
                # Send notification for closed position
                await self._send_notification_position_closed(
                    position=closed_pos,
                    order_details=order_info,
                    reason=f"Closed by STOP {position_type} signal"
                )
                
            except Exception as e:
                logger.error(f"Failed to close position {position.id} due to STOP signal: {str(e)}")
                results.append({"position_id": position.id, "success": False, "error": str(e)})
        
        return {
            "success": True, # Overall signal processing success
            "command": command,
            "results": results,
            "message": f"Attempted to close {len(positions_to_close)} {target_direction.value} position(s)."
        }
    
    # --- Helper Methods ---
    
    def _calculate_trade_amount(self, signal_data: Dict[str, Any]) -> Decimal:
        """
        Calculate trade amount from signal data or return default.
        
        Args:
            signal_data: Signal data which may contain 'amount'
            
        Returns:
            Trade amount to use
        """
        # Get the default trade amount from settings or use the hardcoded default
        default_amount = self.trading_parameters.get('default_trade_amount', self.default_trade_amount)
        max_amount = self.trading_parameters.get('max_trade_amount', Decimal(1000))
        
        # Try to extract amount from signal_data
        amount_str = signal_data.get("amount", "")
        if not amount_str:
            logger.debug(f"No amount specified in signal, using default: {default_amount}")
            return Decimal(default_amount)
        
        try:
            amount = Decimal(str(amount_str))
            # Enforce maximum amount from settings
            if amount > max_amount:
                logger.warning(f"Signal requested amount {amount} exceeds maximum {max_amount}, capping at maximum")
                return max_amount
            return amount
        except (InvalidOperation, ValueError) as e:
            logger.warning(f"Invalid amount format in signal: {amount_str}. Using default amount: {default_amount}")
            return Decimal(default_amount)
    
    def _get_take_profit_config(self, signal_data: Dict[str, Any], max_tp: int) -> Dict[int, Decimal]:
        """
        Get TP percentages from signal ('altTP') or use defaults based on max_tp.
        
        Args:
            signal_data: Trading signal data
            max_tp: Maximum take profit level
            
        Returns:
            Dictionary mapping TP levels to percentages
        """
        alt_tp_str = signal_data.get("altTP")
        if alt_tp_str:
            try:
                percentages = [Decimal(p.strip()) for p in alt_tp_str.split('-')]
                if not percentages: raise ValueError("Empty altTP string")
                # Ensure the number of levels matches max_tp if possible, or log warning
                if len(percentages) != max_tp:
                     logger.warning(f"altTP levels ({len(percentages)}) don't match maxTP ({max_tp}). Using altTP values.")
                # Ensure last level is 100 if specified, otherwise force it? For now, trust input.
                # if percentages[-1] != 100:
                #     logger.warning(f"Last altTP level is not 100 ({percentages[-1]}%). Adjusting.")
                #     percentages[-1] = Decimal('100')

                tp_config = {i + 1: p for i, p in enumerate(percentages)}
                logger.info(f"Using custom TP config from altTP: {tp_config}")
                return tp_config
            except (ValueError, InvalidOperation) as e:
                logger.warning(f"Invalid altTP format '{alt_tp_str}', using default TP config. Error: {e}")
                # Fall through to default

        # Use default based on max_tp
        if max_tp == 4:
            logger.debug("Using default 4-level TP config.")
            return self.default_tp_config_4
        else: # Default to 3 levels for any other max_tp value
            if max_tp != 3:
                 logger.warning(f"maxTP is {max_tp}, but only 3-level and 4-level defaults exist. Using 3-level default.")
            logger.debug("Using default 3-level TP config.")
            return self.default_tp_config_3
    
    async def _find_matching_positions(
        self, 
        asset_symbol: str, 
        bot_strategy: str, 
        interval: str,
        bot_settings: str,
        direction_filter: Optional[PositionDirection] = None
    ) -> List[Position]:
        """
        Find open positions matching the given criteria.
        
        Args:
            asset_symbol: Asset symbol
            bot_strategy: Bot identifier
            interval: Trading interval
            bot_settings: Bot configuration settings
            direction_filter: Optional direction to filter by
            
        Returns:
            List of matching positions
        """
        filters = {
            'asset': asset_symbol,
            'bot_strategy': bot_strategy,
            'timeframe': interval,
            'bot_settings': bot_settings
        }
        
        # Add direction filter if provided
        if direction_filter:
            filters['direction'] = direction_filter.value # Use the string value for filtering
            
        return await self.position_service.get_open_positions(filters)
    
    def _find_position_for_tp(self, positions: List[Position], tp_level: int) -> Optional[Position]:
        """
        Find the most appropriate open position for a specific TP level signal.

        Logic:
        1. Filter out positions that have already executed this TP level or higher.
        2. Among the remaining, prefer the one with the highest number of TPs already executed (closest to the target level).
        3. If there's a tie in TP count, prefer the oldest position.
        """
        candidates = []
        for p in positions:
            # Check if this TP level has already been hit for this position
            if not any(tp.level >= tp_level for tp in p.take_profits):
                candidates.append(p)

        if not candidates:
            return None

        # Sort candidates: highest TP count first, then oldest timestamp first
        candidates.sort(key=lambda p: (-len(p.take_profits), p.timestamp))

        # The best candidate is the first one after sorting
        return candidates[0]

    # --- Notification Helper Methods ---
    
    async def _send_notification_position_opened(
        self,
        position: Position,
        order_details: Dict[str, Any],
        admin_only: bool = False
    ) -> None:
        """Send notification when a position is opened."""
        if not self.notifications_enabled:
            logger.debug(f"Telegram notifications disabled, skipping position opened notification for {position.id}")
            return
        
        try:
            # Get the telegram bot instance
            from ..telegram.telegram_bot import get_telegram_bot
            from ..telegram.telegram_users import CHAT_ID_TO_USE
            
            bot = get_telegram_bot()
            notification_manager = bot.get_notification_manager()
            
            # Send the notification directly to the configured chat ID
            if CHAT_ID_TO_USE:
                logger.info(f"Sending position opened notification to {CHAT_ID_TO_USE}")
                await notification_manager.notify_position_opened(
                    position=position,
                    order_details=order_details,
                    direct_chat_id=CHAT_ID_TO_USE
                )
                logger.info(f"Sent position opened notification for {position.id}")
            else:
                logger.warning(f"No configured chat ID found, position opened notification not sent for {position.id}")
        except Exception as e:
            logger.error(f"Error sending position opened notification: {str(e)}", exc_info=True)
    
    async def _send_notification_take_profit(
        self,
        position: Position,
        tp_level: int,
        price: Decimal,
        quantity: Decimal,
        order_details: Dict[str, Any],
        admin_only: bool = False
    ) -> None:
        """Send notification when a take profit is executed."""
        if not self.notifications_enabled:
            logger.debug(f"Telegram notifications disabled, skipping TP notification for {position.id}")
            return
        
        try:
            # Get the telegram bot instance
            from ..telegram.telegram_bot import get_telegram_bot
            from ..telegram.telegram_users import CHAT_ID_TO_USE
            
            bot = get_telegram_bot()
            notification_manager = bot.get_notification_manager()
            
            # Send the notification directly to the configured chat ID
            if CHAT_ID_TO_USE:
                logger.info(f"Sending TP notification to {CHAT_ID_TO_USE}")
                await notification_manager.notify_take_profit(
                    position=position,
                    tp_level=tp_level,
                    price=price,
                    quantity=quantity,
                    order_details=order_details,
                    direct_chat_id=CHAT_ID_TO_USE
                )
                logger.info(f"Sent TP notification for {position.id}")
            else:
                logger.warning(f"No configured chat ID found, TP notification not sent for {position.id}")
        except Exception as e:
            logger.error(f"Error sending take profit notification: {str(e)}", exc_info=True)
    
    async def _send_notification_position_closed(
        self,
        position: Position,
        order_details: Optional[Dict[str, Any]] = None,
        reason: str = "",
        admin_only: bool = False
    ) -> None:
        """Send notification when a position is closed."""
        if not self.notifications_enabled:
            logger.debug(f"Telegram notifications disabled, skipping position closed notification for {position.id}")
            return
        
        try:
            # Get the telegram bot instance
            from ..telegram.telegram_bot import get_telegram_bot
            from ..telegram.telegram_users import CHAT_ID_TO_USE
            
            bot = get_telegram_bot()
            notification_manager = bot.get_notification_manager()
            
            # Send the notification directly to the configured chat ID
            if CHAT_ID_TO_USE:
                logger.info(f"Sending position closed notification to {CHAT_ID_TO_USE}")
                await notification_manager.notify_position_closed(
                    position=position,
                    order_details=order_details,
                    reason=reason,
                    direct_chat_id=CHAT_ID_TO_USE
                )
                logger.info(f"Sent position closed notification for {position.id}")
            else:
                logger.warning(f"No configured chat ID found, position closed notification not sent for {position.id}")
        except Exception as e:
            logger.error(f"Error sending position closed notification: {str(e)}", exc_info=True)
    
    async def _send_notification_stop_loss(
        self,
        position: Position,
        current_price: Decimal,
        loss_percentage: Decimal,
        admin_only: bool = False
    ) -> None:
        """Send notification when a stop loss is triggered."""
        if not self.notifications_enabled:
            logger.debug(f"Telegram notifications disabled, skipping stop loss notification for {position.id}")
            return
        
        try:
            # Get the telegram bot instance
            from ..telegram.telegram_bot import get_telegram_bot
            from ..telegram.telegram_users import CHAT_ID_TO_USE
            
            bot = get_telegram_bot()
            notification_manager = bot.get_notification_manager()
            
            # Send the notification directly to the configured chat ID
            if CHAT_ID_TO_USE:
                logger.info(f"Sending stop loss notification to {CHAT_ID_TO_USE}")
                await notification_manager.notify_stop_loss(
                    position=position,
                    current_price=current_price,
                    loss_percentage=loss_percentage,
                    direct_chat_id=CHAT_ID_TO_USE
                )
                logger.info(f"Sent stop loss notification for {position.id}")
            else:
                logger.warning(f"No configured chat ID found, stop loss notification not sent for {position.id}")
        except Exception as e:
            logger.error(f"Error sending stop loss notification: {str(e)}", exc_info=True)
