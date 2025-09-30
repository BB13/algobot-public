"""
telegram_markup.py
Markup generators for Telegram bot UI elements.
"""
from typing import List, Optional, Dict, Any, Union

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from ..core.position import Position
from ..core.config import TELEGRAM_INLINE_BUTTONS


def get_main_menu_markup() -> InlineKeyboardMarkup:
    """
    Get the main menu markup with quick commands.
    
    Returns:
        InlineKeyboardMarkup with main menu options
    """
    if not TELEGRAM_INLINE_BUTTONS:
        return None
        
    keyboard = [
        [
            InlineKeyboardButton("👀 Open Positions", callback_data="positions"),
            InlineKeyboardButton("📊 Profit Stats", callback_data="profit")
        ],
        [
            InlineKeyboardButton("📈 System Stats", callback_data="stats"),
            InlineKeyboardButton("❓ Help", callback_data="help")
        ]
    ]
    
    return InlineKeyboardMarkup(keyboard)


def get_position_actions_markup(position_id: str) -> InlineKeyboardMarkup:
    """
    Get position actions markup for a specific position.
    
    Args:
        position_id: Position ID
        
    Returns:
        InlineKeyboardMarkup with position action buttons
    """
    if not TELEGRAM_INLINE_BUTTONS:
        return None
        
    keyboard = [
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{position_id}"),
            InlineKeyboardButton("❌ Close Position", callback_data=f"close_{position_id}")
        ],
        [
            InlineKeyboardButton("📊 Take Profit", callback_data=f"tp_{position_id}"),
            InlineKeyboardButton("👈 Back", callback_data="positions")
        ]
    ]
    
    return InlineKeyboardMarkup(keyboard)


def get_positions_list_markup(positions: List[Position]) -> Optional[InlineKeyboardMarkup]:
    """
    Get markup for a list of positions.
    
    Args:
        positions: List of positions
        
    Returns:
        InlineKeyboardMarkup with position buttons, or None if empty
    """
    if not positions or not TELEGRAM_INLINE_BUTTONS:
        return None
    
    # Group positions by strategy
    strategies = {}
    for position in positions:
        if position.bot_strategy not in strategies:
            strategies[position.bot_strategy] = []
        strategies[position.bot_strategy].append(position)
    
    keyboard = []
    
    # Create buttons for each position, grouped by strategy
    for strategy, strat_positions in strategies.items():
        # Add strategy header if we have multiple strategies
        if len(strategies) > 1:
            keyboard.append([
                InlineKeyboardButton(f"📊 {strategy}", callback_data=f"strategy_{strategy}")
            ])
        
        # Add position buttons
        for i, position in enumerate(strat_positions):
            # Show 2 positions per row
            if i % 2 == 0:
                row = []
                keyboard.append(row)
            else:
                row = keyboard[-1]
            
            # Create button for position
            button_text = f"{position.asset.symbol} {position.direction.value}"
            row.append(InlineKeyboardButton(
                button_text, 
                callback_data=f"position_{position.id}"
            ))
    
    # Add control buttons
    keyboard.append([
        InlineKeyboardButton("🔄 Refresh", callback_data="positions_refresh"),
        InlineKeyboardButton("❌ Close All", callback_data="positions_close_all")
    ])
    
    return InlineKeyboardMarkup(keyboard)


def get_take_profit_markup(position_id: str, available_tps: List[int]) -> Optional[InlineKeyboardMarkup]:
    """
    Get markup for take profit options.
    
    Args:
        position_id: Position ID
        available_tps: List of available TP levels
        
    Returns:
        InlineKeyboardMarkup with TP buttons, or None if empty
    """
    if not available_tps or not TELEGRAM_INLINE_BUTTONS:
        return None
    
    keyboard = []
    
    # Create buttons for each TP level
    row = []
    for tp_level in available_tps:
        # Show 3 TPs per row
        if len(row) == 3:
            keyboard.append(row)
            row = []
        
        # Create button for TP level
        button_text = f"TP{tp_level}"
        row.append(InlineKeyboardButton(
            button_text, 
            callback_data=f"execute_tp_{position_id}_{tp_level}"
        ))
    
    # Add remaining buttons
    if row:
        keyboard.append(row)
    
    # Add back button
    keyboard.append([
        InlineKeyboardButton("👈 Back", callback_data=f"position_{position_id}")
    ])
    
    return InlineKeyboardMarkup(keyboard)


def get_confirmation_markup(
    action: str, 
    entity_id: str, 
    confirm_text: str = "Confirm",
    cancel_text: str = "Cancel"
) -> Optional[InlineKeyboardMarkup]:
    """
    Get confirmation markup for sensitive actions.
    
    Args:
        action: Action to confirm
        entity_id: ID of the entity to act on
        confirm_text: Text for the confirm button
        cancel_text: Text for the cancel button
        
    Returns:
        InlineKeyboardMarkup with confirmation buttons
    """
    if not TELEGRAM_INLINE_BUTTONS:
        return None
        
    keyboard = [
        [
            InlineKeyboardButton(confirm_text, callback_data=f"confirm_{action}_{entity_id}"),
            InlineKeyboardButton(cancel_text, callback_data=f"cancel_{action}_{entity_id}")
        ]
    ]
    
    return InlineKeyboardMarkup(keyboard)