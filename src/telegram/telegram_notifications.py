"""
telegram_notifications.py

Notification system for the Telegram bot.

This module handles sending notifications to users when important events occur.
"""
import logging
from enum import Enum
from typing import Dict, List, Optional, Any, Union
from decimal import Decimal

# Updated import for ParseMode to work with python-telegram-bot v20+
from telegram.constants import ParseMode

from ..core.position import Position

# Add imports for chart capture
import os
from ..core.config import ENABLE_CHART_SNAPSHOTS, SEND_CHART_ON_NEW_POSITION
from .chart_capture import capture_chart_screenshot

logger = logging.getLogger(__name__)


class NotificationType(Enum):
    """Types of notifications."""
    POSITION_OPENED = "position_opened"
    TAKE_PROFIT = "take_profit"
    POSITION_CLOSED = "position_closed"
    STOP_LOSS = "stop_loss"
    ERROR = "error"
    SYSTEM = "system"


class NotificationManager:
    """
    Manages sending notifications to Telegram users.
    """
    
    def __init__(self, telegram_bot):
        """
        Initialize the notification manager.
        
        Args:
            telegram_bot: Telegram bot instance
        """
        self.telegram_bot = telegram_bot
    
    async def notify_position_opened(
        self, 
        position: Position, 
        order_details: Dict[str, Any],
        admin_only: bool = False,
        direct_chat_id: Optional[str] = None
    ) -> None:
        """
        Send a notification when a position is opened.
        
        Args:
            position: The opened position
            order_details: Exchange order details
            admin_only: Whether to send to admin users only
            direct_chat_id: Optional direct chat ID to send to
        """
        try:
            # Format the notification message
            message = (
                f"🟢 <b>Position Opened</b>\n\n"
                f"<b>Asset:</b> {position.asset.symbol}\n"
                f"<b>Direction:</b> {position.direction.value}\n"
                f"<b>Quantity:</b> {position.initial_quantity:.6f}\n"
                f"<b>Entry Price:</b> {position.entry_price:.6f}\n"
                f"<b>Value:</b> {position.initial_value:.6f}\n"
                f"<b>Strategy:</b> {position.bot_strategy}_{position.bot_settings}\n"
                f"<b>Timeframe:</b> {position.timeframe}\n"
                f"<b>ID:</b> <code>{position.id}</code>"
            )
            
            # Send the main notification text
            if direct_chat_id:
                success = await self.telegram_bot.send_message(
                    chat_id=direct_chat_id,
                    text=message,
                    parse_mode=ParseMode.HTML
                )
                logger.info(f"Direct notification to {direct_chat_id} success: {success}")
            else:
                await self._send_notification(
                    message=message,
                    notification_type=NotificationType.POSITION_OPENED,
                    admin_only=admin_only
                )

            # Send chart snapshot if enabled
            if ENABLE_CHART_SNAPSHOTS and SEND_CHART_ON_NEW_POSITION:
                logger.info(f"Sending chart snapshot for new position: {position.id}")
                screenshot_path = await capture_chart_screenshot(position.asset.symbol, position.timeframe)
                
                if screenshot_path:
                    try:
                        # Determine chat ID to send photo to
                        target_chat_id = direct_chat_id
                        if not target_chat_id:
                            from .telegram_users import CHAT_ID_TO_USE # Get configured chat ID
                            target_chat_id = CHAT_ID_TO_USE
                            # If still no target, might need to broadcast to all admins/users?
                            # For now, let's stick to the main configured user or direct if provided

                        if target_chat_id:
                            await self.telegram_bot.application.bot.send_photo(
                                chat_id=target_chat_id,
                                photo=open(screenshot_path, 'rb'),
                                caption=f"Chart for {position.asset.symbol} ({position.timeframe}) at position open",
                                parse_mode=ParseMode.HTML
                            )
                            logger.info(f"Sent chart snapshot to {target_chat_id}")
                        else:
                            logger.warning("Could not determine target chat ID for chart snapshot.")

                    except Exception as photo_err:
                        logger.error(f"Failed to send chart snapshot for {position.id}: {photo_err}", exc_info=True)
                    finally:
                        # Clean up the temporary screenshot file
                        if os.path.exists(screenshot_path):
                             try:
                                os.remove(screenshot_path)
                                # Remove temp directory if it's empty (optional)
                                temp_dir = os.path.dirname(screenshot_path)
                                if not os.listdir(temp_dir):
                                    os.rmdir(temp_dir)
                             except OSError as e:
                                logger.warning(f"Error removing temporary file/dir {screenshot_path}: {e}")
                else:
                    logger.warning(f"Failed to capture chart screenshot for new position: {position.id}")
            
        except Exception as e:
            logger.error(f"Error sending position opened notification: {str(e)}", exc_info=True)
    
    async def notify_take_profit(
        self, 
        position: Position, 
        tp_level: int,
        price: Decimal,
        quantity: Decimal,
        order_details: Dict[str, Any],
        admin_only: bool = False,
        direct_chat_id: Optional[str] = None
    ) -> None:
        """
        Send a notification when a take profit is executed.
        
        Args:
            position: The position
            tp_level: Take profit level
            price: Execution price
            quantity: Execution quantity
            order_details: Exchange order details
            admin_only: Whether to send to admin users only
            direct_chat_id: Optional direct chat ID to send to
        """
        try:
            # Calculate profit
            profit = (price - position.entry_price) * quantity if position.direction.value == "LONG" else (position.entry_price - price) * quantity
            profit_percentage = (profit / (position.entry_price * quantity)) * Decimal("100")
            
            # Format the notification message
            message = (
                f"💰 <b>Take Profit Executed</b>\n\n"
                f"<b>Asset:</b> {position.asset.symbol}\n"
                f"<b>Direction:</b> {position.direction.value}\n"
                f"<b>TP Level:</b> {tp_level}\n"
                f"<b>Quantity:</b> {quantity:.6f}\n"
                f"<b>Price:</b> {price:.6f}\n"
                f"<b>Profit:</b> {profit:.6f} ({profit_percentage:.2f}%)\n"
                f"<b>Strategy:</b> {position.bot_strategy}_{position.bot_settings}\n"
                f"<b>Remaining:</b> {position.remaining_quantity:.6f}\n"
                f"<b>ID:</b> <code>{position.id}</code>"
            )
            
            # Add closed notification if this is the final TP
            if position.is_closed:
                message += "\n\n✅ <b>Position fully closed!</b>"
            
            # Send to users
            if direct_chat_id:
                # Send directly to specified chat ID
                success = await self.telegram_bot.send_message(
                    chat_id=direct_chat_id,
                    text=message,
                    parse_mode=ParseMode.HTML
                )
                logger.info(f"Direct TP notification to {direct_chat_id} success: {success}")
            else:
                # Use regular notification method
                await self._send_notification(
                    message=message,
                    notification_type=NotificationType.TAKE_PROFIT,
                    admin_only=admin_only
                )
            
        except Exception as e:
            logger.error(f"Error sending take profit notification: {str(e)}", exc_info=True)
    
    async def notify_position_closed(
        self, 
        position: Position, 
        order_details: Optional[Dict[str, Any]] = None,
        reason: str = "",
        admin_only: bool = False,
        direct_chat_id: Optional[str] = None
    ) -> None:
        """
        Send a notification when a position is closed.
        
        Args:
            position: The closed position
            order_details: Exchange order details
            reason: Reason for closing
            admin_only: Whether to send to admin users only
            direct_chat_id: Optional direct chat ID to send to
        """
        try:
            # Calculate realized PnL
            realized_pnl = position.get_realized_pnl()
            initial_value = position.entry_price * position.initial_quantity
            pnl_percentage = (realized_pnl / initial_value) * Decimal("100") if initial_value > 0 else Decimal("0")
            
            # Format PnL with color and symbol
            if realized_pnl >= 0:
                pnl_str = f"🟢 +{realized_pnl:.6f} ({pnl_percentage:.2f}%)"
            else:
                pnl_str = f"🔴 {realized_pnl:.6f} ({pnl_percentage:.2f}%)"
            
            # Format the notification message
            message = (
                f"🟡 <b>Position Closed</b>\n\n"
                f"<b>Asset:</b> {position.asset.symbol}\n"
                f"<b>Direction:</b> {position.direction.value}\n"
                f"<b>Initial Quantity:</b> {position.initial_quantity:.6f}\n"
                f"<b>Entry Price:</b> {position.entry_price:.6f}\n"
                f"<b>Realized PnL:</b> {pnl_str}\n"
                f"<b>Strategy:</b> {position.bot_strategy}_{position.bot_settings}\n"
                f"<b>ID:</b> <code>{position.id}</code>"
            )
            
            # Add reason if provided
            if reason:
                message += f"\n<b>Reason:</b> {reason}"
            
            # Send to users
            if direct_chat_id:
                # Send directly to specified chat ID
                success = await self.telegram_bot.send_message(
                    chat_id=direct_chat_id,
                    text=message,
                    parse_mode=ParseMode.HTML
                )
                logger.info(f"Direct close notification to {direct_chat_id} success: {success}")
            else:
                # Use regular notification method
                await self._send_notification(
                    message=message,
                    notification_type=NotificationType.POSITION_CLOSED,
                    admin_only=admin_only
                )
            
        except Exception as e:
            logger.error(f"Error sending position closed notification: {str(e)}", exc_info=True)
    
    async def notify_stop_loss(
        self, 
        position: Position, 
        current_price: Decimal,
        loss_percentage: Decimal,
        admin_only: bool = False,
        direct_chat_id: Optional[str] = None
    ) -> None:
        """
        Send a notification when a stop loss is triggered.
        
        Args:
            position: The position
            current_price: Current price
            loss_percentage: Loss percentage
            admin_only: Whether to send to admin users only
            direct_chat_id: Optional direct chat ID to send to
        """
        try:
            # Format the notification message
            message = (
                f"🔴 <b>Stop Loss Triggered</b>\n\n"
                f"<b>Asset:</b> {position.asset.symbol}\n"
                f"<b>Direction:</b> {position.direction.value}\n"
                f"<b>Entry Price:</b> {position.entry_price:.6f}\n"
                f"<b>Current Price:</b> {current_price:.6f}\n"
                f"<b>Loss:</b> {loss_percentage:.2f}%\n"
                f"<b>Strategy:</b> {position.bot_strategy}_{position.bot_settings}\n"
                f"<b>ID:</b> <code>{position.id}</code>\n\n"
                f"Position will be closed automatically."
            )
            
            # Send to users
            if direct_chat_id:
                # Send directly to specified chat ID
                success = await self.telegram_bot.send_message(
                    chat_id=direct_chat_id,
                    text=message,
                    parse_mode=ParseMode.HTML
                )
                logger.info(f"Direct stop loss notification to {direct_chat_id} success: {success}")
            else:
                # Use regular notification method
                await self._send_notification(
                    message=message,
                    notification_type=NotificationType.STOP_LOSS,
                    admin_only=admin_only
                )
            
        except Exception as e:
            logger.error(f"Error sending stop loss notification: {str(e)}", exc_info=True)
    
    async def notify_error(
        self, 
        error_message: str, 
        details: Optional[Dict[str, Any]] = None,
        admin_only: bool = True
    ) -> None:
        """
        Send a notification when an error occurs.
        
        Args:
            error_message: Error message
            details: Additional error details
            admin_only: Whether to send to admin users only
        """
        try:
            # Format the notification message
            message = f"❌ <b>Error</b>\n\n{error_message}"
            
            # Add details if provided
            if details:
                message += "\n\n<b>Details:</b>\n"
                for key, value in details.items():
                    message += f"<b>{key}:</b> {value}\n"
            
            # Send to users (only admins by default)
            await self._send_notification(
                message=message,
                notification_type=NotificationType.ERROR,
                admin_only=admin_only
            )
            
        except Exception as e:
            logger.error(f"Error sending error notification: {str(e)}", exc_info=True)
    
    async def notify_system(
        self, 
        title: str, 
        message: str,
        admin_only: bool = False
    ) -> None:
        """
        Send a system notification.
        
        Args:
            title: Notification title
            message: Notification message
            admin_only: Whether to send to admin users only
        """
        try:
            # Format the notification message
            formatted_message = f"ℹ️ <b>{title}</b>\n\n{message}"
            
            # Send to users
            await self._send_notification(
                message=formatted_message,
                notification_type=NotificationType.SYSTEM,
                admin_only=admin_only
            )
            
        except Exception as e:
            logger.error(f"Error sending system notification: {str(e)}", exc_info=True)
    
    async def _send_notification(
        self, 
        message: str, 
        notification_type: NotificationType,
        admin_only: bool = False
    ) -> None:
        """
        Send a notification to users based on their preferences.
        
        Args:
            message: Notification message
            notification_type: Type of notification
            admin_only: Whether to send to admin users only
        """
        try:
            logger.debug(f"Entering _send_notification method for {notification_type.value}")
            
            # Import CHAT_ID_TO_USE directly - this is our simplified approach
            from .telegram_users import CHAT_ID_TO_USE, get_all_users, get_admin_users
            
            if CHAT_ID_TO_USE:
                # Direct notification to configured user
                logger.debug(f"Sending direct notification to {CHAT_ID_TO_USE}")
                success = await self.telegram_bot.send_message(
                    chat_id=CHAT_ID_TO_USE,
                    text=message,
                    parse_mode=ParseMode.HTML
                )
                logger.info(f"Sent {notification_type.value} notification to {CHAT_ID_TO_USE}. Success: {success}")
                return
                
            # Fallback to getting users from helper functions if CHAT_ID_TO_USE is not set
            # Get users to notify
            if admin_only:
                users = get_admin_users()
            else:
                users = get_all_users()
            
            logger.info(f"Will send {notification_type.value} notification to users: {users}")
            
            if not users:
                logger.warning(f"No users found to send {notification_type.value} notification to")
                return
            
            # Send the message to all users
            logger.debug(f"About to broadcast message: {message[:50]}...")
            results = await self.telegram_bot.broadcast_message(
                text=message,
                users=users,
                parse_mode=ParseMode.HTML
            )
            
            # Log results
            success_count = sum(1 for success in results.values() if success)
            logger.info(
                f"Sent {notification_type.value} notification to {success_count}/{len(results)} users. Results: {results}"
            )
            
        except Exception as e:
            logger.error(f"Error sending notification: {str(e)}", exc_info=True)