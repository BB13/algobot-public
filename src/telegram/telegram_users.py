"""
telegram_users.py

Simplified Telegram user management.

This module provides the necessary configuration and decorators for Telegram bot authentication.
"""
import os
import logging
import functools
from typing import Callable, Any, List, Union
from telegram import Update
from telegram.ext import ContextTypes

from ..core.config import TELEGRAM_ADMIN_CHAT_ID, APPROVED_CHAT_IDS


logger = logging.getLogger(__name__)

# Get the approved chat ID from environment variable first, then config
APPROVED_CHAT_ID_ENV = os.getenv("APPROVED_CHAT_IDS")
logger.info(f"APPROVED_CHAT_ID from environment: {APPROVED_CHAT_ID_ENV}")
logger.info(f"APPROVED_CHAT_IDS from config: {APPROVED_CHAT_IDS}")

# If environment variable exists, use it, otherwise fall back to config
if APPROVED_CHAT_ID_ENV:
    logger.info("Using APPROVED_CHAT_IDS from environment variable")
    CHAT_ID_TO_USE = APPROVED_CHAT_ID_ENV
elif APPROVED_CHAT_IDS:
    logger.info("Using APPROVED_CHAT_IDS from config")
    CHAT_ID_TO_USE = APPROVED_CHAT_IDS[0] if isinstance(APPROVED_CHAT_IDS, list) else APPROVED_CHAT_IDS
else:
    logger.warning("No approved chat IDs found in environment or config")
    CHAT_ID_TO_USE = None


# Simple dictionary to track authentication
AUTHORIZED_USERS = {CHAT_ID_TO_USE: True} if CHAT_ID_TO_USE else {}
ADMIN_USERS = {CHAT_ID_TO_USE: True} if CHAT_ID_TO_USE else {}


def check_auth(func: Callable) -> Callable:
    """
    Decorator to check if a user is authorized to use the bot.
    
    Only allows authorized users to use the command.
    """
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
        if not update.effective_chat:
            logger.warning("Received update without effective_chat")
            return
        
        chat_id = str(update.effective_chat.id)
        
        if chat_id in AUTHORIZED_USERS:
            return await func(update, context)
        else:
            logger.warning(f"Unauthorized access attempt from chat ID: {chat_id}")
            await update.effective_chat.send_message("You are not authorized to use this bot.")
            return
    
    return wrapper


def admin_only(func: Callable) -> Callable:
    """
    Decorator to check if a user is an admin.
    
    Only allows admin users to use the command.
    """
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
        if not update.effective_chat:
            logger.warning("Received update without effective_chat")
            return
        
        chat_id = str(update.effective_chat.id)
        
        if chat_id in ADMIN_USERS:
            return await func(update, context)
        else:
            logger.warning(f"Admin privilege required - attempt from chat ID: {chat_id}")
            await update.effective_chat.send_message("This command requires admin privileges.")
            return
    
    return wrapper


# Helper functions to simulate the UserManager interface
def get_all_users() -> List[str]:
    """Get a list of all authorized user chat IDs."""
    return list(AUTHORIZED_USERS.keys())


def get_admin_users() -> List[str]:
    """Get a list of admin user chat IDs."""
    return list(ADMIN_USERS.keys())


def is_authorized(chat_id: Union[int, str]) -> bool:
    """Check if a chat ID is authorized to use the bot."""
    return str(chat_id) in AUTHORIZED_USERS


def is_admin(chat_id: Union[int, str]) -> bool:
    """Check if a chat ID belongs to an admin user."""
    return str(chat_id) in ADMIN_USERS
