"""
Shutdown tasks to gracefully handle application termination.
"""

import logging
import asyncio
from datetime import datetime
from decimal import Decimal
from typing import List, Dict, Any, Optional

from ..services.position_service import PositionService
from ..core.position import Position
from ..core.config import CLOSE_POSITIONS_ON_SHUTDOWN, SHUTDOWN_CLOSE_METHOD

logger = logging.getLogger(__name__)

async def close_all_positions_on_shutdown(position_service: PositionService) -> Dict[str, Any]:
    """
    Close all open positions when the application is shutting down.
    
    Args:
        position_service: Service for managing positions
        
    Returns:
        Dictionary with results of the operation
    """
    if not CLOSE_POSITIONS_ON_SHUTDOWN:
        logger.info("Closing positions on shutdown is disabled. Skipping.")
        return {"closed_positions": 0, "skipped": True}
    
    try:
        logger.info("Beginning shutdown procedure to close all open positions...")
        
        # Force reload positions to ensure we have the latest data
        await position_service.repository.reload_positions()
        
        # Get all open positions
        open_positions = await position_service.get_open_positions()
        
        if not open_positions:
            logger.info("No open positions found to close on shutdown.")
            return {"closed_positions": 0, "success": True}
        
        logger.info(f"Found {len(open_positions)} open positions to close on shutdown.")
        
        # Results tracking
        results = {
            "closed_positions": 0,
            "success": True,
            "positions": [],
            "errors": []
        }
        
        # Process each position
        for position in open_positions:
            try:
                position_id = position.id
                symbol = position.asset.symbol
                direction = position.direction.value
                
                logger.info(f"Closing position on shutdown: {position_id} ({symbol} {direction})")
                
                if SHUTDOWN_CLOSE_METHOD == "virtual":
                    # Virtual close - no exchange order, just mark as closed locally
                    current_price = await position_service.exchange.get_current_price(position.asset)
                    position.close(
                        current_price, 
                        position.remaining_quantity,
                        reason="Closed due to application shutdown (virtual)"
                    )
                    await position_service.repository.update(position)
                    logger.info(f"Virtually closed position {position_id} at price {current_price}")
                    
                    results["positions"].append({
                        "id": position_id,
                        "symbol": symbol,
                        "direction": direction,
                        "close_price": str(current_price),
                        "close_method": "virtual"
                    })
                    
                else:
                    # Market close - execute actual order on exchange
                    closed_pos, order_info = await position_service.close_position(
                        position_id=position_id,
                        reason="Closed due to application shutdown"
                    )
                    
                    logger.info(f"Market closed position {position_id}")
                    
                    results["positions"].append({
                        "id": position_id,
                        "symbol": symbol,
                        "direction": direction,
                        "close_price": str(closed_pos.close_price) if hasattr(closed_pos, "close_price") else "unknown",
                        "close_method": "market",
                        "order_id": order_info.get("orderId", "N/A") if order_info else "N/A"
                    })
                
                results["closed_positions"] += 1
                
            except Exception as e:
                error_msg = f"Error closing position {position.id} on shutdown: {str(e)}"
                logger.error(error_msg, exc_info=True)
                results["errors"].append({
                    "position_id": position.id,
                    "error": str(e)
                })
                results["success"] = False
        
        # Log summary
        if results["closed_positions"] > 0:
            logger.info(f"Successfully closed {results['closed_positions']} positions on shutdown.")
        if results["errors"]:
            logger.warning(f"Encountered {len(results['errors'])} errors while closing positions on shutdown.")
        
        return results
    
    except Exception as e:
        logger.error(f"Error in shutdown procedure: {str(e)}", exc_info=True)
        return {
            "closed_positions": 0,
            "success": False,
            "error": str(e)
        } 