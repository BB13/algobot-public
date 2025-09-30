"""
telegram_bot.py
Main Telegram bot module for the trading bot.
"""
import os
import sys
import logging
import asyncio
from typing import Optional, List, Dict, Any, Union

from telegram import Update, Bot
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler, 
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# Ensure paths are set up correctly for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ..core.config import (
    TELEGRAM_SECRET,
    TELEGRAM_ADMIN_CHAT_ID,
    TELEGRAM_USERS_FILE,
    TELEGRAM_NOTIFICATION_ENABLED,
    LOG_DIR
)

from .telegram_users import (
    check_auth, 
    admin_only,
    get_all_users,
    get_admin_users,
    CHAT_ID_TO_USE
)

from .telegram_commands import (
    start_command,
    help_command,
    positions_command,
    position_command,
    profit_command,
    close_position_command,
    close_all_command,
    auth_command,
    add_user_command,
    remove_user_command,
    list_users_command,
    stats_command,
    chart_command
)

from .telegram_callbacks import callback_handler

from .telegram_notifications import NotificationManager

# Set up logger
logger = logging.getLogger(__name__)
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(LOG_DIR, 'telegram.log'))
    ]
)


class TradingBotTelegram:
    """
    Main Telegram bot class for the trading bot.
    
    This class initializes the bot, registers command handlers,
    and handles the bot's lifecycle.
    """
    
    def __init__(self):
        """Initialize the Telegram bot and its dependencies."""
        self.token = TELEGRAM_SECRET
        if not self.token:
            raise ValueError("TELEGRAM_SECRET not set")
        
        # Initialize services and managers
        self.notification_manager = NotificationManager(self)
        
        # Initialize the application
        self.application = Application.builder().token(self.token).build()
        
        # Register command handlers
        self._register_handlers()
        
        logger.info("Telegram bot initialized")
    
    def _register_handlers(self):
        """Register command and message handlers."""
        # Basic commands
        self.application.add_handler(CommandHandler("start", start_command))
        self.application.add_handler(CommandHandler("help", help_command))
        
        # Trading commands (authenticated)
        self.application.add_handler(CommandHandler("positions", positions_command))
        self.application.add_handler(CommandHandler("position", position_command))
        self.application.add_handler(CommandHandler("profit", profit_command))
        self.application.add_handler(CommandHandler("close", close_position_command))
        self.application.add_handler(CommandHandler("closeall", close_all_command))
        self.application.add_handler(CommandHandler("stats", stats_command))
        self.application.add_handler(CommandHandler("chart", chart_command))
        
        # Authentication commands
        self.application.add_handler(CommandHandler("auth", auth_command))
        
        # Admin commands
        self.application.add_handler(CommandHandler("adduser", add_user_command))
        self.application.add_handler(CommandHandler("removeuser", remove_user_command))
        self.application.add_handler(CommandHandler("listusers", list_users_command))
        
        # Callback query handler for inline buttons
        self.application.add_handler(CallbackQueryHandler(callback_handler))
        
        # Error handler
        self.application.add_error_handler(self._handle_error)
        
        logger.info("Command handlers registered")
    
    async def _handle_error(self, update: Optional[Update], context: ContextTypes.DEFAULT_TYPE):
        """Log errors and send a message to the user."""
        logger.error("Exception while handling an update:", exc_info=context.error)
        
        if update and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="An error occurred while processing your request. Please try again later."
            )
    
    async def start(self):
        """Start the bot and begin polling for updates."""
        logger.info("Starting Telegram bot...")
        
        # Start the bot
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        
        logger.info("Telegram bot started and polling for updates")
    
    async def stop(self):
        """Stop the bot gracefully."""
        logger.info("Stopping Telegram bot...")
        
        # Stop the bot
        await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()
        
        logger.info("Telegram bot stopped")
    
    async def send_message(
        self, 
        chat_id: Union[str, int], 
        text: str, 
        parse_mode: Optional[str] = ParseMode.HTML,
        reply_markup: Any = None
    ) -> bool:
        """
        Send a message to a specific chat.
        
        Args:
            chat_id: The chat ID to send the message to
            text: The message text
            parse_mode: The parse mode for the message
            reply_markup: Optional reply markup
            
        Returns:
            True if the message was sent successfully, False otherwise
        """
        try:
            logger.debug(f"Sending message to chat_id: {chat_id}, parse_mode: {parse_mode}, text length: {len(text)}")
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
            logger.debug(f"Message sent successfully to chat_id: {chat_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to send message to {chat_id}: {str(e)}", exc_info=True)
            return False
    
    async def broadcast_message(
        self, 
        text: str, 
        users: Optional[List[Union[str, int]]] = None,
        admin_only: bool = False,
        parse_mode: Optional[str] = ParseMode.HTML
    ) -> Dict[Union[str, int], bool]:
        """
        Broadcast a message to multiple users.
        
        Args:
            text: The message text
            users: List of user chat_ids to send to (None for all users)
            admin_only: If True, send only to admin users
            parse_mode: The parse mode for the message
            
        Returns:
            Dictionary mapping chat_ids to success status
        """
        results = {}
        
        # Determine recipients
        if users is None:
            if admin_only:
                users = get_admin_users()
            else:
                users = get_all_users()
        
        logger.info(f"Broadcasting message to users: {users}")
        
        if not users:
            logger.warning("No users to broadcast message to")
            return results
        
        # Send messages
        for chat_id in users:
            logger.debug(f"Attempting to send message to chat_id: {chat_id}")
            success = await self.send_message(chat_id, text, parse_mode)
            results[chat_id] = success
            logger.info(f"Message to {chat_id} {'sent successfully' if success else 'failed'}")
        
        return results
    
    def get_user_manager(self):
        """
        Legacy method to maintain compatibility.
        Returns an object that simulates the UserManager interface.
        """
        # Create a minimal compatible interface
        class LegacyUserManager:
            def get_all_users(self):
                return get_all_users()
                
            def get_admin_users(self):
                return get_admin_users()
        
        return LegacyUserManager()
    
    def get_notification_manager(self) -> NotificationManager:
        """Get the notification manager instance."""
        return self.notification_manager


# Singleton instance for the bot
_bot_instance = None

def get_telegram_bot() -> TradingBotTelegram:
    """
    Get the singleton Telegram bot instance.
    
    Returns:
        The TradingBotTelegram instance
    """
    global _bot_instance
    if _bot_instance is None:
        _bot_instance = TradingBotTelegram()
    return _bot_instance


async def main():
    """Main function to start the Telegram bot."""
    bot = get_telegram_bot()
    
    try:
        await bot.start()
        
        # Keep the bot running
        while True:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
    finally:
        await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())