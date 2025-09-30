"""
telegram_commands.py
Command handlers for the Telegram bot.
"""
import re
import logging
import asyncio
from decimal import Decimal
from functools import wraps
from typing import Dict, List, Optional, Any, Callable, Awaitable, Union, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import sys
import os
# Ensure paths are set up correctly for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ..services.position_service import PositionService
from ..core.position import Position, PositionDirection
from ..core.config import (
    ENABLE_CHART_SNAPSHOTS, 
    MULTI_COIN_CHARTS_URL_TEMPLATE,
    ASSET_SHORTNAME_MAP,
)
# Import CHART_PRESETS from the new config module
from ..core.config import CHART_PRESETS
from .chart_capture import capture_chart_screenshot
# Import the renamed image utility
from .image_utils import prepare_chart_image

# Import authentication functions directly
from .telegram_users import is_authorized, is_admin, CHAT_ID_TO_USE

# REMOVED: Import from main which creates circular dependency
# Instead, we'll get these services via app state or global variables


logger = logging.getLogger(__name__)

# Services to be set externally
_position_service = None
_exchange_adapter = None
_signal_processor = None

def set_services(position_service, exchange_adapter, signal_processor=None):
    """
    Set the services for the command handlers to use.
    This should be called from main.py after initializing the services.
    """
    global _position_service, _exchange_adapter, _signal_processor
    _position_service = position_service
    _exchange_adapter = exchange_adapter
    _signal_processor = signal_processor
    logger.info("Services set for telegram commands")

# Helper functions to get services
def get_position_service():
    """Get the position service instance. Will raise error if not initialized."""
    if _position_service is None:
        raise RuntimeError("Position service not initialized")
    return _position_service

def get_exchange_adapter():
    """Get the exchange adapter instance. Will raise error if not initialized."""
    if _exchange_adapter is None:
        raise RuntimeError("Exchange adapter not initialized")
    return _exchange_adapter

def get_signal_processor_instance():
    """Get the signal processor instance. Will raise error if not initialized."""
    if _signal_processor is None:
        raise RuntimeError("Signal processor not initialized")
    return _signal_processor

# Authentication decorators
def check_auth(func: Callable) -> Callable:
    """
    Decorator to check if a user is authorized before executing a command.
    
    Args:
        func: Command handler function
        
    Returns:
        Wrapped function that checks authentication
    """
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update.effective_chat:
            return
            
        # Check if user is authorized directly
        chat_id = str(update.effective_chat.id)
        if is_authorized(chat_id):
            return await func(update, context, *args, **kwargs)
        else:
            await update.effective_message.reply_text(
                "You are not authorized to use this command. Please use /auth [token] to authenticate."
            )
            return
    
    return wrapped


def admin_only(func: Callable) -> Callable:
    """
    Decorator to check if a user is an admin before executing a command.
    
    Args:
        func: Command handler function
        
    Returns:
        Wrapped function that checks admin status
    """
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update.effective_chat:
            return
            
        # Check if user is an admin directly
        chat_id = str(update.effective_chat.id)
        if is_admin(chat_id):
            return await func(update, context, *args, **kwargs)
        else:
            await update.effective_message.reply_text(
                "This command is only available to admin users."
            )
            return
    
    return wrapped


# Command handlers
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /start command.
    
    Introduces the bot and prompts for authentication.
    """
    if not update.effective_chat:
        return
    
    chat_id = str(update.effective_chat.id)
    
    # Check if user is already authorized
    if is_authorized(chat_id):
        # Create keyboard markup for quick command access
        keyboard = [
            [
                InlineKeyboardButton("👀 Open Positions", callback_data="positions"),
                InlineKeyboardButton("📊 Profit Stats", callback_data="profit")
            ],
            [
                InlineKeyboardButton("❓ Help", callback_data="help")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.effective_message.reply_text(
            f"Hello {update.effective_user.first_name}! Welcome to the Trading Bot. "
            f"You are already authenticated. Use /help to see available commands.",
            reply_markup=reply_markup
        )
    else:
        await update.effective_message.reply_text(
            f"Hello {update.effective_user.first_name}! Welcome to the Trading Bot. "
            f"This is a private bot with limited access.\n\n"
            f"If you've been provided with access credentials, use /auth [token] to authenticate."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /help command.
    
    Shows a list of available commands.
    """
    if not update.effective_chat:
        return
    
    chat_id = str(update.effective_chat.id)
    
    basic_commands = (
        "Available commands:\n\n"
        "/start - Start the bot\n"
        "/help - Show this help message\n"
        "/auth [token] - Authenticate with a token\n"
    )
    
    # Check if user is authorized
    if is_authorized(chat_id):
        # Add trading commands
        trading_commands = (
            "\nTrading commands:\n"
            "/positions - Show all open positions\n"
            "/position [id] - Show details for a specific position\n"
            "/profit - Show overall profit statistics\n"
            "/close [id] - Close a specific position\n"
            "/closeall [strategy] - Close all positions (optionally for a specific strategy)\n"
            "/stats - Show system statistics\n"
        )
        
        # Add admin commands if the user is an admin
        admin_commands = ""
        if is_admin(chat_id):
            admin_commands = (
                "\nAdmin commands:\n"
                "/adduser [chat_id] [is_admin] - Add a new user (is_admin: 0 or 1)\n"
                "/removeuser [chat_id] - Remove a user\n"
                "/listusers - List all users\n"
            )
        
        # Send the help message
        await update.effective_message.reply_text(
            basic_commands + trading_commands + admin_commands
        )
    else:
        # Only show basic commands for unauthenticated users
        await update.effective_message.reply_text(basic_commands)


async def auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /auth command.
    
    Authenticates a user with a token.
    """
    if not update.effective_chat or not update.effective_user:
        return
    
    chat_id = str(update.effective_chat.id)
    
    # Check if already authenticated
    if is_authorized(chat_id):
        await update.effective_message.reply_text(
            "You are already authenticated."
        )
        return
    
    # Check if token was provided
    if not context.args or len(context.args) < 1:
        await update.effective_message.reply_text(
            "Please provide an authentication token: /auth [token]"
        )
        return
    
    # Extract token
    token = context.args[0]
    
    # Simplified authentication - just check against TELEGRAM_SECRET
    from ..core.config import TELEGRAM_SECRET
    if token == TELEGRAM_SECRET:
        # In our simplified approach, we could just add the chat_id to AUTHORIZED_USERS
        # but since we're using direct chat ID, this would only be necessary 
        # if we support dynamic user addition
        await update.effective_message.reply_text(
            "Authentication successful! You can now use the bot.\n"
            "Use /help to see available commands."
        )
    else:
        await update.effective_message.reply_text(
            "Authentication failed. Please check your token and try again."
        )


@check_auth
async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /positions command.
    
    Shows all open positions.
    """
    if not update.effective_chat:
        return
    
    try:
        # Get the position service
        position_service = get_position_service()
        
        # Ensure the latest positions are loaded from the file
        await position_service.repository.reload_positions()
        logger.info("Reloaded positions from file for /positions command.")

        # Get filter arguments if any
        filters = {}
        if context.args:
            # Check for strategy filter
            if len(context.args) >= 1:
                filters['bot_strategy'] = context.args[0]
            
            # Check for asset filter
            if len(context.args) >= 2:
                filters['asset'] = context.args[1]
        
        try:
            # Get open positions
            if filters:
                # Use specific filters
                open_positions = await position_service.repository.get_open_positions(filters)
            else:
                # Get all open positions
                open_positions = await position_service.repository.get_open_positions()
            
            if not open_positions:
                await update.effective_message.reply_text("No open positions found.")
                return
            
            # Format positions
            message = f"📊 <b>Open Positions ({len(open_positions)})</b>\n\n"
            
            # Group positions by strategy
            strategies = {}
            for position in open_positions:
                if position.bot_strategy not in strategies:
                    strategies[position.bot_strategy] = []
                strategies[position.bot_strategy].append(position)
            
            # Format each strategy
            for strategy, positions in strategies.items():
                message += f"<b>Strategy: {strategy}</b>\n"
                
                for position in positions:
                    # Get current price to calculate P&L
                    current_price = await position_service.exchange.get_current_price(position.asset)
                    
                    # Calculate P&L
                    pnl = position.get_unrealized_pnl(current_price)
                    pnl_percentage = position.get_pnl_percentage(current_price)
                    
                    # Format P&L with color and symbol
                    if pnl >= 0:
                        pnl_str = f"🟢 +{pnl:.2f} ({pnl_percentage:.2f}%)"
                    else:
                        pnl_str = f"🔴 {pnl:.2f} ({pnl_percentage:.2f}%)"
                    
                    # Format position
                    message += (
                        f"- <code>{position.asset.symbol}</code> | <b>{position.direction.value}</b> | "
                        f"<code>{position.remaining_quantity:.6f}</code> | {pnl_str} | "
                        f"ID: <code>{position.id[:8]}</code>\n"
                    )
                
                message += "\n"
            
            # Add command info
            message += (
                "<i>Use /position [id] to view details of a specific position</i>\n"
                "<i>Use /close [id] to close a position</i>"
            )
            
            await update.effective_message.reply_text(
                message, 
                parse_mode=ParseMode.HTML
            )
            
        except Exception as e:
            logger.error(f"Error fetching positions: {str(e)}", exc_info=True)
            await update.effective_message.reply_text(
                f"Error fetching positions: {str(e)}"
            )
    except Exception as e:
        logger.error(f"Error reloading positions: {str(e)}", exc_info=True)
        await update.effective_message.reply_text(
            f"Error reloading positions: {str(e)}"
        )


@check_auth
async def position_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /position command.
    
    Shows details for a specific position.
    """
    if not update.effective_chat:
        return
    
    # Check if position ID was provided
    if not context.args or len(context.args) < 1:
        await update.effective_message.reply_text(
            "Please provide a position ID: /position [id]"
        )
        return
    
    # Extract position ID (allow partial ID)
    position_id_partial = context.args[0]
    
    try:
        # Get the position service
        position_service = get_position_service()
        
        # Get all positions and find the one that matches
        all_positions = await position_service.repository.get_open_positions()
        
        # Find matching position
        matching_positions = [p for p in all_positions if p.id.startswith(position_id_partial)]
        
        if not matching_positions:
            await update.effective_message.reply_text(
                f"No position found with ID starting with '{position_id_partial}'."
            )
            return
        
        if len(matching_positions) > 1:
            # Multiple matches, show a list
            message = f"Found {len(matching_positions)} positions matching ID '{position_id_partial}':\n\n"
            for position in matching_positions:
                message += f"- ID: <code>{position.id}</code> | {position.asset.symbol} | {position.direction.value}\n"
            
            message += "\nPlease provide a more specific ID."
            
            await update.effective_message.reply_text(
                message,
                parse_mode=ParseMode.HTML
            )
            return
        
        # Get the single matching position
        position = matching_positions[0]
        
        # Get current price to calculate P&L
        current_price = await position_service.exchange.get_current_price(position.asset)
        
        # Calculate P&L
        unrealized_pnl = position.get_unrealized_pnl(current_price)
        realized_pnl = position.get_realized_pnl()
        total_pnl = unrealized_pnl + realized_pnl
        pnl_percentage = position.get_pnl_percentage(current_price)
        
        # Format P&L with color and symbol
        if total_pnl >= 0:
            pnl_str = f"🟢 +{total_pnl:.6f} ({pnl_percentage:.2f}%)"
        else:
            pnl_str = f"🔴 {total_pnl:.6f} ({pnl_percentage:.2f}%)"
        
        # Format position details
        message = f"📊 <b>Position Details</b>\n\n"
        message += f"<b>ID:</b> <code>{position.id}</code>\n"
        message += f"<b>Asset:</b> {position.asset.symbol}\n"
        message += f"<b>Direction:</b> {position.direction.value}\n"
        message += f"<b>Strategy:</b> {position.bot_strategy}_{position.bot_settings}\n"
        message += f"<b>Timeframe:</b> {position.timeframe}\n"
        message += f"<b>Initial Quantity:</b> {position.initial_quantity:.6f}\n"
        message += f"<b>Remaining Quantity:</b> {position.remaining_quantity:.6f}\n"
        message += f"<b>Entry Price:</b> {position.entry_price:.6f}\n"
        message += f"<b>Current Price:</b> {current_price:.6f}\n"
        message += f"<b>P&L:</b> {pnl_str}\n"
        message += f"<b>Timestamp:</b> {position.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        # Add take profit information
        message += f"<b>Take Profits:</b>\n"
        if position.take_profits:
            for tp in position.take_profits:
                message += (
                    f"- TP{tp.level}: {tp.quantity:.6f} @ {tp.price:.6f} "
                    f"({tp.timestamp.strftime('%Y-%m-%d %H:%M:%S')})\n"
                )
        else:
            message += "- No take profits executed yet\n"
        
        # Create inline keyboard for quick actions
        keyboard = [
            [
                InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{position.id}"),
                InlineKeyboardButton("❌ Close Position", callback_data=f"close_{position.id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.effective_message.reply_text(
            message,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Error fetching position details: {str(e)}", exc_info=True)
        await update.effective_message.reply_text(
            f"Error fetching position details: {str(e)}"
        )


@check_auth
async def profit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /profit command.
    
    Shows overall profit statistics.
    """
    if not update.effective_chat:
        return
    
    try:
        # Get the position service
        position_service = get_position_service()
        
        # Get open positions
        open_positions = await position_service.repository.get_open_positions()
        
        # Get closed positions (limit to recent ones)
        closed_positions = await position_service.repository.get_closed_positions()
        
        # Calculate total profit
        total_profit = Decimal('0')
        unrealized_profit = Decimal('0')
        realized_profit = Decimal('0')
        
        # Calculate unrealized profit from open positions
        for position in open_positions:
            current_price = await position_service.exchange.get_current_price(position.asset)
            unrealized_profit += position.get_unrealized_pnl(current_price)
            realized_profit += position.get_realized_pnl()
        
        # Calculate realized profit from closed positions
        for position in closed_positions:
            realized_profit += position.get_realized_pnl()
        
        # Calculate total profit
        total_profit = unrealized_profit + realized_profit
        
        # Format the profit message
        message = f"📊 <b>Profit Statistics</b>\n\n"
        
        # Format profit values with color and symbol
        if unrealized_profit >= 0:
            unrealized_str = f"🟢 +{unrealized_profit:.6f}"
        else:
            unrealized_str = f"🔴 {unrealized_profit:.6f}"
            
        if realized_profit >= 0:
            realized_str = f"🟢 +{realized_profit:.6f}"
        else:
            realized_str = f"🔴 {realized_profit:.6f}"
            
        if total_profit >= 0:
            total_str = f"🟢 +{total_profit:.6f}"
        else:
            total_str = f"🔴 {total_profit:.6f}"
        
        message += f"<b>Unrealized Profit:</b> {unrealized_str}\n"
        message += f"<b>Realized Profit:</b> {realized_str}\n"
        message += f"<b>Total Profit:</b> {total_str}\n\n"
        
        # Add position counts
        message += f"<b>Open Positions:</b> {len(open_positions)}\n"
        message += f"<b>Closed Positions:</b> {len(closed_positions)}\n\n"
        
        # Profit breakdown by strategy
        strategies = {}
        
        # Add open positions to strategies
        for position in open_positions:
            strategy = position.bot_strategy
            if strategy not in strategies:
                strategies[strategy] = {
                    'unrealized': Decimal('0'),
                    'realized': Decimal('0'),
                    'total': Decimal('0'),
                    'count_open': 0,
                    'count_closed': 0
                }
            
            current_price = await position_service.exchange.get_current_price(position.asset)
            strategies[strategy]['unrealized'] += position.get_unrealized_pnl(current_price)
            strategies[strategy]['realized'] += position.get_realized_pnl()
            strategies[strategy]['total'] += position.get_unrealized_pnl(current_price) + position.get_realized_pnl()
            strategies[strategy]['count_open'] += 1
        
        # Add closed positions to strategies
        for position in closed_positions:
            strategy = position.bot_strategy
            if strategy not in strategies:
                strategies[strategy] = {
                    'unrealized': Decimal('0'),
                    'realized': Decimal('0'),
                    'total': Decimal('0'),
                    'count_open': 0,
                    'count_closed': 0
                }
            
            strategies[strategy]['realized'] += position.get_realized_pnl()
            strategies[strategy]['total'] += position.get_realized_pnl()
            strategies[strategy]['count_closed'] += 1
        
        # Add strategy breakdown
        if strategies:
            message += "<b>Profit by Strategy:</b>\n"
            
            for strategy, stats in strategies.items():
                if stats['total'] >= 0:
                    total_str = f"🟢 +{stats['total']:.6f}"
                else:
                    total_str = f"🔴 {stats['total']:.6f}"
                
                message += (
                    f"- <b>{strategy}:</b> {total_str} | "
                    f"Open: {stats['count_open']} | Closed: {stats['count_closed']}\n"
                )
        
        await update.effective_message.reply_text(
            message,
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        logger.error(f"Error calculating profit statistics: {str(e)}", exc_info=True)
        await update.effective_message.reply_text(
            f"Error calculating profit statistics: {str(e)}"
        )


@check_auth
async def close_position_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /close command.
    
    Closes a specific position.
    """
    if not update.effective_chat:
        return
    
    # Check if position ID was provided
    if not context.args or len(context.args) < 1:
        await update.effective_message.reply_text(
            "Please provide a position ID: /close [id]"
        )
        return
    
    # Extract position ID (allow partial ID)
    position_id_partial = context.args[0]
    
    try:
        # Get the position service
        position_service = get_position_service()
        
        # Get all open positions and find the one that matches
        open_positions = await position_service.repository.get_open_positions()
        
        # Find matching position
        matching_positions = [p for p in open_positions if p.id.startswith(position_id_partial)]
        
        if not matching_positions:
            await update.effective_message.reply_text(
                f"No open position found with ID starting with '{position_id_partial}'."
            )
            return
        
        if len(matching_positions) > 1:
            # Multiple matches, show a list
            message = f"Found {len(matching_positions)} positions matching ID '{position_id_partial}':\n\n"
            for position in matching_positions:
                message += f"- ID: <code>{position.id}</code> | {position.asset.symbol} | {position.direction.value}\n"
            
            message += "\nPlease provide a more specific ID."
            
            await update.effective_message.reply_text(
                message,
                parse_mode=ParseMode.HTML
            )
            return
        
        # Get the single matching position
        position = matching_positions[0]
        
        # Send confirmation message
        await update.effective_message.reply_text(
            f"Closing position {position.asset.symbol} {position.direction.value}...\n"
            f"This may take a moment."
        )
        
        # Close the position
        closed_position, order = await position_service.close_position(
            position.id, 
            f"Closed via Telegram by user {update.effective_user.id}"
        )
        
        # Notify about success
        await update.effective_message.reply_text(
            f"✅ Position closed successfully!\n\n"
            f"<b>Asset:</b> {closed_position.asset.symbol}\n"
            f"<b>Direction:</b> {closed_position.direction.value}\n"
            f"<b>Remaining Quantity:</b> {closed_position.remaining_quantity:.6f}\n"
            f"<b>Realized PnL:</b> {closed_position.get_realized_pnl():.6f}",
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        logger.error(f"Error closing position: {str(e)}", exc_info=True)
        await update.effective_message.reply_text(
            f"Error closing position: {str(e)}"
        )


@check_auth
async def close_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /closeall command.
    
    Closes all positions, optionally filtered by strategy.
    """
    if not update.effective_chat:
        return
    
    # Check if strategy filter was provided
    strategy_filter = None
    if context.args and len(context.args) >= 1:
        strategy_filter = context.args[0]
    
    try:
        # Get the position service
        position_service = get_position_service()
        
        # Get open positions
        filters = {}
        if strategy_filter:
            filters['bot_strategy'] = strategy_filter
        
        open_positions = await position_service.repository.get_open_positions(filters)
        
        if not open_positions:
            await update.effective_message.reply_text(
                "No open positions found to close." if not strategy_filter else
                f"No open positions found for strategy '{strategy_filter}'."
            )
            return
        
        # Send confirmation message
        await update.effective_message.reply_text(
            f"Closing {len(open_positions)} positions..." +
            (f" for strategy '{strategy_filter}'" if strategy_filter else "") +
            "\nThis may take a moment."
        )
        
        # Close all positions
        results = []
        for position in open_positions:
            try:
                closed_position, order = await position_service.close_position(
                    position.id, 
                    f"Closed via Telegram by user {update.effective_user.id}"
                )
                results.append({
                    'asset': closed_position.asset.symbol,
                    'direction': closed_position.direction.value,
                    'success': True,
                    'pnl': closed_position.get_realized_pnl()
                })
            except Exception as e:
                logger.error(f"Error closing position {position.id}: {str(e)}")
                results.append({
                    'asset': position.asset.symbol,
                    'direction': position.direction.value,
                    'success': False,
                    'error': str(e)
                })
        
        # Format results
        message = f"📊 <b>Close All Results</b>\n\n"
        message += f"<b>Total Positions:</b> {len(open_positions)}\n"
        message += f"<b>Successfully Closed:</b> {sum(1 for r in results if r['success'])}\n"
        message += f"<b>Failed:</b> {sum(1 for r in results if not r['success'])}\n\n"
        
        total_pnl = sum(r['pnl'] for r in results if r['success'])
        if total_pnl >= 0:
            pnl_str = f"🟢 +{total_pnl:.6f}"
        else:
            pnl_str = f"🔴 {total_pnl:.6f}"
        
        message += f"<b>Total Realized PnL:</b> {pnl_str}\n\n"
        
        if sum(1 for r in results if not r['success']) > 0:
            message += "<b>Failed Positions:</b>\n"
            for result in results:
                if not result['success']:
                    message += f"- {result['asset']} {result['direction']}: {result['error']}\n"
        
        await update.effective_message.reply_text(
            message,
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        logger.error(f"Error closing all positions: {str(e)}", exc_info=True)
        await update.effective_message.reply_text(
            f"Error closing all positions: {str(e)}"
        )


@check_auth
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /stats command.
    
    Shows system statistics.
    """
    if not update.effective_chat:
        return
    
    try:
        # Get the position service and exchange adapter
        position_service = get_position_service()
        exchange_adapter = get_exchange_adapter()
        
        # Get open positions
        open_positions = await position_service.repository.get_open_positions()
        
        # Get exchange rate limits
        # This would need to be implemented in the exchange adapter
        rate_limits = "Rate limits not available"  # Placeholder
        
        # Get bot uptime
        import time
        from ..main import get_start_time
        
        current_time = time.time()
        start_time = get_start_time()
        uptime_seconds = current_time - start_time
        
        # Format uptime
        days, remainder = divmod(uptime_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        uptime_str = f"{int(days)}d {int(hours)}h {int(minutes)}m {int(seconds)}s"
        
        # Format message
        message = f"📊 <b>System Statistics</b>\n\n"
        message += f"<b>Uptime:</b> {uptime_str}\n"
        message += f"<b>Open Positions:</b> {len(open_positions)}\n"
        
        # Add asset distribution
        asset_counts = {}
        for position in open_positions:
            asset = position.asset.symbol
            if asset not in asset_counts:
                asset_counts[asset] = 0
            asset_counts[asset] += 1
        
        if asset_counts:
            message += "\n<b>Assets Distribution:</b>\n"
            for asset, count in asset_counts.items():
                message += f"- {asset}: {count} positions\n"
        
        # Add strategy distribution
        strategy_counts = {}
        for position in open_positions:
            strategy = position.bot_strategy
            if strategy not in strategy_counts:
                strategy_counts[strategy] = 0
            strategy_counts[strategy] += 1
        
        if strategy_counts:
            message += "\n<b>Strategy Distribution:</b>\n"
            for strategy, count in strategy_counts.items():
                message += f"- {strategy}: {count} positions\n"
        
        # Add API rate limit info
        message += f"\n<b>Exchange API:</b>\n{rate_limits}\n"
        
        await update.effective_message.reply_text(
            message,
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        logger.error(f"Error fetching system statistics: {str(e)}", exc_info=True)
        await update.effective_message.reply_text(
            f"Error fetching system statistics: {str(e)}"
        )


@admin_only
async def add_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /adduser command.
    
    Adds a new user to the authorized users list.
    
    Format: /adduser [chat_id] [is_admin]
    Example: /adduser 123456789 1
    """
    if not update.effective_chat:
        return
    
    # Check if chat_id was provided
    if not context.args or len(context.args) < 1:
        await update.effective_message.reply_text(
            "Please provide a chat ID: /adduser [chat_id] [is_admin]\n"
            "Example: /adduser 123456789 1"
        )
        return
    
    # Extract chat_id and is_admin
    chat_id = context.args[0]
    is_admin_flag = len(context.args) > 1 and context.args[1] == '1'
    
    # In our simplified approach, we would add to AUTHORIZED_USERS and ADMIN_USERS 
    # But for this bot, we're using a direct chat ID approach, so we just inform the user
    await update.effective_message.reply_text(
        f"In this simplified version, user management is done via environment variables.\n"
        f"To add a user, set APPROVED_CHAT_IDS in the environment or config file."
    )


@admin_only
async def remove_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /removeuser command.
    
    Removes a user from the authorized users list.
    
    Format: /removeuser [chat_id]
    Example: /removeuser 123456789
    """
    if not update.effective_chat:
        return
    
    # Check if chat_id was provided
    if not context.args or len(context.args) < 1:
        await update.effective_message.reply_text(
            "Please provide a chat ID: /removeuser [chat_id]\n"
            "Example: /removeuser 123456789"
        )
        return
    
    # Extract chat_id
    chat_id = context.args[0]
    
    # In our simplified approach, inform the user that user management is done via environment variables
    await update.effective_message.reply_text(
        f"In this simplified version, user management is done via environment variables.\n"
        f"To remove a user, update APPROVED_CHAT_IDS in the environment or config file."
    )


@admin_only
async def list_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /listusers command.
    
    Lists all authorized users.
    """
    if not update.effective_chat:
        return
    
    # Get the list of authorized users
    from .telegram_users import get_all_users, get_admin_users
    
    all_users = get_all_users()
    admin_users = get_admin_users()
    
    # Format the user list
    message = "Authorized users:\n\n"
    
    if not all_users:
        message += "No users found."
    else:
        for user_id in all_users:
            user_type = "Admin" if user_id in admin_users else "User"
            message += f"- {user_id} ({user_type})\n"
    
    await update.effective_message.reply_text(message)


async def _process_single_chart(full_symbol: str, url_template: str, update: Update):
    """
    Helper function to process chart generation for a single asset.
    
    Args:
        full_symbol: The asset symbol, potentially prefixed (e.g., BINANCE:BTCUSDT).
        url_template: The MultiCoinCharts URL template.
        update: The Telegram Update object.
    """
    try:
        # Generate the asset string for the URL by repeating the single asset
        asset_param = full_symbol # Assumes prefix is already included if provided by caller
        assets_string = ",".join([asset_param] * 4) # Repeat single asset
        target_url = url_template.format(ASSETS=assets_string)

        # Try to find short name for display, fallback to symbol without prefix
        symbol_only = full_symbol.split(':')[-1] if ':' in full_symbol else full_symbol
        display_name = next((sn for sn, fs in ASSET_SHORTNAME_MAP.items() if fs == symbol_only), symbol_only)
        
        # Let user know we're trying to capture the screenshot
        await update.effective_message.reply_text(
            f"Capturing chart for {display_name}... (This may take up to 30 seconds)"
        )
        
        # Use a more generous timeout for the screenshot capture
        capture_attempt = 0
        max_attempts = 2
        screenshot_path = None
        
        while capture_attempt < max_attempts and not screenshot_path:
            capture_attempt += 1
            try:
                screenshot_path = await capture_chart_screenshot(target_url=target_url)
                if not screenshot_path and capture_attempt < max_attempts:
                    logger.warning(f"Screenshot capture failed for {display_name}, attempt {capture_attempt}. Retrying...")
                    await asyncio.sleep(2)  # Short delay before retry
            except Exception as capture_err:
                logger.error(f"Error during screenshot capture for {display_name}: {capture_err}", exc_info=True)
                if capture_attempt < max_attempts:
                    logger.info(f"Retrying screenshot capture for {display_name}...")
                    await asyncio.sleep(2)  # Short delay before retry
        
        if screenshot_path:
            # --- Prepare the image --- 
            logger.info(f"Preparing screenshot for {display_name}: {screenshot_path}")
            prepared_path = prepare_chart_image(
                image_path=screenshot_path, 
                top_percent=15.0, 
                bottom_percent=30.0,
                border_size=2,
                border_color="black"
            )
            
            path_to_send = prepared_path if prepared_path else screenshot_path
            if not prepared_path:
                 logger.warning(f"Image preparation failed for {screenshot_path}, sending original.")
            # ------------------------- 

            try:
                logger.info(f"Sending chart photo for {display_name}...")
                await update.effective_message.reply_photo(
                    photo=open(path_to_send, 'rb'), # Use path_to_send
                    caption=f"MultiCoinCharts: {display_name}", # Simplified caption
                )
                return True # Indicate success
            finally:
                # Clean up temporary files (handles original if prep failed)
                cleanup_paths = [screenshot_path]
                if prepared_path and prepared_path != screenshot_path:
                    cleanup_paths.append(prepared_path)
                for p in cleanup_paths:
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                            temp_dir = os.path.dirname(p)
                            if os.path.exists(temp_dir) and not os.listdir(temp_dir):
                                try: os.rmdir(temp_dir)
                                except OSError: pass 
                        except OSError as e:
                            logger.warning(f"Error removing temp file/dir {p}: {e}")
        else:
            await update.effective_message.reply_text(
                f"Failed to capture chart for {display_name} after {max_attempts} attempts. The site may be temporarily unavailable."
            )
            return False # Indicate capture failure
            
    except Exception as e:
        # Determine display name for error message even if processing failed early
        symbol_only_err = full_symbol.split(':')[-1] if ':' in full_symbol else full_symbol
        display_name_err = next((sn for sn, fs in ASSET_SHORTNAME_MAP.items() if fs == symbol_only_err), symbol_only_err)
        logger.error(f"Error processing chart for {display_name_err} in _process_single_chart: {str(e)}", exc_info=True)
        try:
             await update.effective_message.reply_text(
                 f"Error generating chart for {display_name_err}: {str(e)}"
             )
        except Exception as send_err:
             logger.error(f"Failed to send error message to user for {display_name_err}: {send_err}")
        return False # Indicate processing failure

@check_auth
async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle the /chart command.
    
    Displays prepared MultiCoinCharts screenshots.
    - If [SHORTNAME]: Shows chart for that specific asset.
    - If [PRESET_ID]: Shows charts for all assets defined in that preset.
    - If no argument: Shows charts for all unique assets with open positions.
    Usage: /chart [SHORTNAME|PRESET_ID] (e.g., /chart LINK, /chart 1) or /chart
    """
    if not update.effective_chat:
        return

    if not ENABLE_CHART_SNAPSHOTS:
        await update.effective_message.reply_text(
            "Chart snapshot feature is currently disabled."
        )
        return

    url_template = MULTI_COIN_CHARTS_URL_TEMPLATE
    if not url_template or '{ASSETS}' not in url_template:
        await update.effective_message.reply_text(
            "MultiCoinCharts URL template is missing or invalid."
        )
        return

    symbols_to_process: List[str] = [] # List of potentially prefixed symbols
    request_description = ""

    if not context.args:
        # --- No argument: Get all open positions' assets --- 
        await update.effective_message.reply_text("Fetching open positions...")
        try:
            position_service = get_position_service()
            
            # Reload positions to ensure we have the latest data
            logger.info("Reloading positions from file for /chart command.")
            await position_service.repository.reload_positions()
            
            open_positions = await position_service.repository.get_open_positions()
            if not open_positions:
                await update.effective_message.reply_text("No open positions found.")
                return
            # Use BINANCE prefix for open positions (adjust if needed)
            unique_symbols = sorted(list(set(f"BINANCE:{pos.asset.symbol}" for pos in open_positions)))
            symbols_to_process = unique_symbols
            request_description = "open positions"
            if not symbols_to_process:
                await update.effective_message.reply_text("Could not determine assets from open positions.")
                return
            
            # Log the positions found to help with debugging
            logger.info(f"Found {len(open_positions)} open positions with {len(unique_symbols)} unique assets")
            for symbol in unique_symbols:
                logger.info(f"Preparing chart for: {symbol}")
            
        except Exception as e:
            logger.error(f"Error getting open positions for /chart: {str(e)}", exc_info=True)
            await update.effective_message.reply_text(f"Error retrieving open positions: {str(e)}")
            return
    else:
        # --- Argument provided: Check if it's a preset ID or asset short name --- 
        arg = context.args[0]
        
        if arg in CHART_PRESETS:
             # Argument is a Preset ID
             preset = CHART_PRESETS[arg]
             preset_name = preset.get('name', f"Preset {arg}")
             preset_assets = preset.get('assets', []) # These should be prefixed like BINANCE:BTCUSDT
             if not preset_assets:
                  await update.effective_message.reply_text(f"Preset '{arg}' ({preset_name}) has no assets defined.")
                  return
             symbols_to_process = preset_assets
             request_description = f"Preset {arg} ({preset_name})"
        else:
             # Argument is potentially an Asset Short Name
             asset_short_name = arg.upper()
             full_symbol = ASSET_SHORTNAME_MAP.get(asset_short_name)
             if not full_symbol:
                 await update.effective_message.reply_text(
                     f"Unknown asset short name or preset ID: {arg}."
                 )
                 return
             # Assume BINANCE prefix for short names (adjust if needed)
             symbols_to_process = [f"BINANCE:{full_symbol}"] 
             request_description = f"asset {asset_short_name}"

    # --- Process Charts --- 
    if not symbols_to_process:
        logger.warning("No symbols determined for chart generation in /chart")
        # Previous messages should have informed the user
        return
        
    await update.effective_message.reply_text(
        f"Generating {len(symbols_to_process)} chart(s) for {request_description}..."
    )
        
    tasks = []
    for symbol in symbols_to_process:
        tasks.append(asyncio.create_task(_process_single_chart(symbol, url_template, update)))
    
    # Wait for all tasks to complete
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Log summary of results
    success_count = sum(1 for r in results if isinstance(r, bool) and r)
    error_count = len(results) - success_count
    logger.info(f"Finished processing chart command for {request_description}. Success: {success_count}, Failed: {error_count}")
    await update.effective_message.reply_text(f"Finished generating {success_count}/{len(results)} chart(s) for {request_description}.")