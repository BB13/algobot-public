"""
telegram_callbacks.py
Callback query handlers for the Telegram bot.
"""
import logging
import re
from typing import Dict, List, Optional, Any, Callable, Awaitable, Tuple

from telegram import Update
from telegram.ext import ContextTypes, CallbackContext

from .telegram_commands import (
    positions_command,
    position_command,
    profit_command,
    stats_command,
    help_command,
    close_position_command
)
from .telegram_users import check_auth, admin_only


logger = logging.getLogger(__name__)


@check_auth
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle callback queries from inline keyboard buttons.
    
    Args:
        update: Update containing the callback query
        context: Callback context
    """
    query = update.callback_query
    
    if not query:
        return
    
    # Log the callback data
    logger.info(f"Callback query: {query.data}")
    
    # Answer the callback query to stop the loading animation
    await query.answer()
    
    # Process based on the callback data
    try:
        if query.data == "positions":
            # Show positions list
            await positions_command(update, context)
        
        elif query.data == "profit":
            # Show profit statistics
            await profit_command(update, context)
        
        elif query.data == "stats":
            # Show system statistics
            await stats_command(update, context)
        
        elif query.data == "help":
            # Show help message
            await help_command(update, context)
        
        elif query.data.startswith("position_"):
            # Show position details
            position_id = query.data.split("_", 1)[1]
            context.args = [position_id]
            await position_command(update, context)
        
        elif query.data.startswith("close_"):
            # Close position
            position_id = query.data.split("_", 1)[1]
            
            # Set up confirmation keyboard
            from .telegram_markup import get_confirmation_markup
            markup = get_confirmation_markup(
                action="close",
                entity_id=position_id,
                confirm_text="✅ Yes, Close Position",
                cancel_text="❌ Cancel"
            )
            
            await query.edit_message_text(
                f"Are you sure you want to close position with ID {position_id}?",
                reply_markup=markup
            )
        
        elif query.data.startswith("confirm_close_"):
            # Confirm position closure
            position_id = query.data.split("_", 2)[2]
            context.args = [position_id]
            await close_position_command(update, context)
        
        elif query.data.startswith("cancel_close_"):
            # Cancel position closure
            position_id = query.data.split("_", 2)[2]
            context.args = [position_id]
            await position_command(update, context)
        
        elif query.data == "positions_refresh":
            # Refresh positions list
            await positions_command(update, context)
        
        elif query.data == "positions_close_all":
            # Set up confirmation keyboard for closing all positions
            from .telegram_markup import get_confirmation_markup
            markup = get_confirmation_markup(
                action="closeall",
                entity_id="all",
                confirm_text="✅ Yes, Close All",
                cancel_text="❌ Cancel"
            )
            
            await query.edit_message_text(
                "Are you sure you want to close ALL open positions? This action cannot be undone.",
                reply_markup=markup
            )
        
        elif query.data == "confirm_closeall_all":
            # Close all positions
            from .telegram_commands import close_all_command
            await close_all_command(update, context)
        
        elif query.data == "cancel_closeall_all":
            # Cancel closing all positions
            await positions_command(update, context)
        
        elif query.data.startswith("refresh_"):
            # Refresh position details
            position_id = query.data.split("_", 1)[1]
            context.args = [position_id]
            await position_command(update, context)
        
        elif query.data.startswith("tp_"):
            # Show take profit options
            position_id = query.data.split("_", 1)[1]
            
            # Get position details
            from ..main import get_position_service
            position_service = get_position_service()
            position = await position_service.repository.get_by_id(position_id)
            
            if not position:
                await query.edit_message_text(
                    f"Position with ID {position_id} not found. It may have been closed."
                )
                return
            
            # Determine available TP levels
            executed_tps = set(tp.level for tp in position.take_profits)
            available_tps = [level for level in range(1, position.take_profit_max + 1) if level not in executed_tps]
            
            if not available_tps:
                await query.edit_message_text(
                    f"No take profit levels available for position {position.asset.symbol} {position.direction.value}. "
                    f"All {position.take_profit_max} TPs have been executed."
                )
                return
            
            # Create TP options keyboard
            from .telegram_markup import get_take_profit_markup
            markup = get_take_profit_markup(position_id, available_tps)
            
            await query.edit_message_text(
                f"Choose a take profit level to execute for {position.asset.symbol} {position.direction.value}:",
                reply_markup=markup
            )
        
        elif query.data.startswith("execute_tp_"):
            # Execute take profit
            parts = query.data.split("_")
            position_id = parts[2]
            tp_level = int(parts[3])
            
            # Set up confirmation keyboard
            from .telegram_markup import get_confirmation_markup
            markup = get_confirmation_markup(
                action=f"tp_{tp_level}",
                entity_id=position_id,
                confirm_text="✅ Yes, Execute TP",
                cancel_text="❌ Cancel"
            )
            
            await query.edit_message_text(
                f"Are you sure you want to execute TP {tp_level} for position {position_id}?",
                reply_markup=markup
            )
        
        elif query.data.startswith("confirm_tp_"):
            # Confirm take profit execution
            parts = query.data.split("_")
            tp_level = int(parts[1])
            position_id = parts[2]
            
            # Send wait message
            await query.edit_message_text(
                f"Executing TP {tp_level} for position {position_id}... Please wait."
            )
            
            # Get position and execute TP
            from ..main import get_position_service
            position_service = get_position_service()
            
            try:
                # Get default TP percentages
                from ..core.config import (
                    DEFAULT_TP_PERCENTAGES_3,
                    DEFAULT_TP_PERCENTAGES_4
                )
                
                # Get position
                position = await position_service.repository.get_by_id(position_id)
                
                if not position:
                    await query.edit_message_text(
                        f"Position with ID {position_id} not found. It may have been closed."
                    )
                    return
                
                # Determine TP percentages based on max TP count
                tp_percentages = DEFAULT_TP_PERCENTAGES_3
                if position.take_profit_max == 4:
                    tp_percentages = DEFAULT_TP_PERCENTAGES_4
                
                # Execute TP
                updated_position, order = await position_service.execute_take_profit(
                    position_id=position_id,
                    tp_level=tp_level,
                    tp_percentages=tp_percentages
                )
                
                # Determine result message
                if updated_position.is_closed:
                    message = (
                        f"✅ TP {tp_level} executed successfully!\n"
                        f"Position {updated_position.asset.symbol} {updated_position.direction.value} "
                        f"has been fully closed."
                    )
                else:
                    message = (
                        f"✅ TP {tp_level} executed successfully!\n"
                        f"Remaining quantity: {updated_position.remaining_quantity}"
                    )
                
                await query.edit_message_text(message)
                
            except Exception as e:
                logger.error(f"Error executing TP: {str(e)}", exc_info=True)
                await query.edit_message_text(
                    f"Error executing TP {tp_level} for position {position_id}: {str(e)}"
                )
        
        elif query.data.startswith("cancel_tp_"):
            # Cancel take profit execution
            parts = query.data.split("_")
            position_id = parts[2]
            context.args = [position_id]
            await position_command(update, context)
        
        elif query.data.startswith("strategy_"):
            # Filter positions by strategy
            strategy = query.data.split("_", 1)[1]
            context.args = [strategy]
            await positions_command(update, context)
        
        else:
            # Unknown callback data
            logger.warning(f"Unknown callback data: {query.data}")
            await query.edit_message_text(
                "Unknown command. Please try again."
            )
    
    except Exception as e:
        logger.error(f"Error handling callback: {str(e)}", exc_info=True)
        try:
            await query.edit_message_text(
                f"Error processing command: {str(e)}"
            )
        except:
            # The message might have been deleted or otherwise unavailable
            pass