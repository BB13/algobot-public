import logging
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from ..core.exchange_adapter import ExchangeAdapter
from ..services.position_service import PositionService
from ..core.config import (
    SAFETY_MEASURES_INTERVAL,
    STOP_LOSS_PERCENTAGE,
    MAX_STOP_LOSS_PERCENTAGE,
    LONG_TERM_TRADE_HRS
)
from ..core.user_settings import get_settings

logger = logging.getLogger(__name__)

class SafetyTasks:
    """
    Collection of safety tasks to monitor and protect positions.
    These tasks can be scheduled to run periodically.
    """
    
    def __init__(self, position_service: PositionService, exchange_adapter: ExchangeAdapter):
        """
        Initialize safety tasks with required dependencies.
        
        Args:
            position_service: Service for managing positions
            exchange_adapter: Exchange adapter for market operations
        """
        self.position_service = position_service
        self.exchange_adapter = exchange_adapter
        logger.info("SafetyTasks initialized")
    
    @property
    def stop_loss_percentage(self) -> Decimal:
        """Get stop loss percentage from settings."""
        trading_params = get_settings().trading_parameters
        return Decimal(str(trading_params.get('stop_loss', {}).get('percentage', STOP_LOSS_PERCENTAGE)))
    
    @property
    def max_stop_loss_percentage(self) -> Decimal:
        """Get maximum stop loss percentage from settings."""
        trading_params = get_settings().trading_parameters
        return Decimal(str(trading_params.get('stop_loss', {}).get('max_percentage', MAX_STOP_LOSS_PERCENTAGE)))
    
    @property
    def long_term_trade_hrs(self) -> int:
        """Get long term trade hours threshold from settings."""
        trading_params = get_settings().trading_parameters
        return trading_params.get('stop_loss', {}).get('long_term_trade_hrs', LONG_TERM_TRADE_HRS)
    
    @property
    def safety_check_interval(self) -> int:
        """Get safety check interval from settings."""
        safety_params = get_settings().config.get('safety', {})
        return safety_params.get('check_interval', SAFETY_MEASURES_INTERVAL)
    
    async def run_safety_checks(self) -> None:
        """
        Run safety checks on open positions.
        
        This includes:
        1. Stop loss check - close positions that have losses beyond threshold
        2. Time-based closure - close positions that have been open too long
        """
        try:
            open_positions = await self.position_service.get_open_positions()
            if not open_positions:
                logger.debug("No open positions found for safety checks.")
                return

            logger.debug(f"Checking {len(open_positions)} open positions...")
            
            # Get current values from settings
            stop_loss_pct = self.stop_loss_percentage
            max_stop_loss_pct = self.max_stop_loss_percentage
            long_term_hrs = self.long_term_trade_hrs
            
            for position in open_positions:
                # Fetch price for each position individually
                try:
                    current_price = await self.exchange_adapter.get_current_price(position.asset)
                except Exception as price_exc:
                    logger.warning(f"Could not get current price for {position.asset.symbol}: {price_exc}. Skipping safety check for position {position.id}")
                    continue # Skip to the next position if price fetch fails
                
                # 1. Stop Loss Check
                try:
                    # Ensure get_pnl_percentage exists and handles potential errors
                    if hasattr(position, 'get_pnl_percentage'):
                        pnl_percent = position.get_pnl_percentage(current_price)

                        # Check if PnL is below stop loss threshold
                        if pnl_percent <= -abs(stop_loss_pct):
                            # Check if PnL is NOT beyond the max stop loss (prevent closing in flash crash)
                            if pnl_percent >= -abs(max_stop_loss_pct):
                                logger.warning(f"STOP LOSS triggered for position {position.id} ({position.asset.symbol}). PnL: {pnl_percent:.2f}%. Closing position.")
                                try:
                                    await self.position_service.close_position(position.id, reason=f"Stop Loss triggered at {pnl_percent:.2f}%")
                                    # Avoid checking this position again in this loop iteration
                                    continue
                                except Exception as close_exc:
                                    logger.error(f"Failed to close position {position.id} due to stop loss: {close_exc}", exc_info=True)
                            else:
                                logger.warning(f"Stop Loss threshold ({stop_loss_pct}%) breached for position {position.id} ({pnl_percent:.2f}%), but exceeded MAX Stop Loss ({max_stop_loss_pct}%). NOT closing.")
                        else:
                             logger.debug(f"Position {position.id} PnL ({pnl_percent:.2f}%) is above stop loss threshold (-{stop_loss_pct}%).")

                    else:
                        logger.error(f"Position object {position.id} missing 'get_pnl_percentage' method.")

                except Exception as pnl_exc:
                     logger.error(f"Error calculating PnL for position {position.id}: {pnl_exc}", exc_info=True)

                # 2. Long Term Trade Check (Time-based Closure)
                try:
                    position_age = datetime.now(position.timestamp.tzinfo) - position.timestamp # Ensure timezone aware comparison if needed
                    if position_age > timedelta(hours=long_term_hrs):
                        logger.warning(f"LONG TERM TRADE detected for position {position.id} ({position.asset.symbol}). Age: {position_age}. Closing position.")
                        try:
                            await self.position_service.close_position(position.id, reason=f"Time-based closure after {long_term_hrs} hours")
                            # Avoid checking this position again
                            continue
                        except Exception as close_exc:
                            logger.error(f"Failed to close long-term position {position.id}: {close_exc}", exc_info=True)
                except Exception as age_exc:
                    logger.error(f"Error checking age for position {position.id}: {age_exc}", exc_info=True)

        except Exception as e:
            logger.error(f"Error in safety checks: {str(e)}", exc_info=True)
    
    async def run_scheduled_tasks(self, interval_seconds: Optional[int] = None) -> None:
        """
        Run all safety tasks on a schedule.
        
        Args:
            interval_seconds: How often to run the tasks (default from config)
        """
        # If not provided, get interval from settings
        if interval_seconds is None:
            interval_seconds = self.safety_check_interval
        
        # Wait a moment for services to be fully ready after startup
        await asyncio.sleep(5)
        
        logger.info(f"Starting scheduled safety tasks (interval: {interval_seconds}s)")
        
        heartbeat_counter = 0
        while True:
            heartbeat_counter += 1
            
            # Only log every 10 iterations at INFO level to reduce noise (or on first run)
            if heartbeat_counter == 1 or heartbeat_counter % 10 == 0:
                logger.info(f"Safety task heartbeat {heartbeat_counter} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                logger.debug(f"Safety task iteration {heartbeat_counter}")
            
            try:
                # Run safety checks
                await self.run_safety_checks()
                logger.debug(f"Safety checks completed for iteration {heartbeat_counter}")
            except asyncio.CancelledError:
                logger.info("Scheduled safety tasks cancelled")
                break
            except Exception as e:
                logger.error(f"CRITICAL ERROR in safety tasks: {str(e)}", exc_info=True)
                # Log but don't crash
            
            # Dynamically fetch the interval in case it was updated via settings
            try:
                # Get the current interval from settings (with fallback)
                current_interval = self.safety_check_interval
                if current_interval != interval_seconds:
                    logger.info(f"Safety check interval updated from {interval_seconds}s to {current_interval}s")
                    interval_seconds = current_interval
            except Exception as e:
                logger.error(f"Error updating safety interval: {str(e)}")
                # Continue with existing interval
            
            # Wait for the next interval
            try:
                logger.debug(f"Safety tasks sleeping for {interval_seconds} seconds")
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                logger.info("Safety task sleep canceled")
                break
            except Exception as e:
                logger.error(f"Error in safety task sleep: {str(e)}")
                # Fall back to a simpler sleep
                await asyncio.sleep(max(interval_seconds, 60))
    
    async def start_scheduled_tasks(self, interval_seconds: Optional[int] = None) -> asyncio.Task:
        """
        Start the scheduled safety tasks in the background.
        
        Args:
            interval_seconds: How often to run the tasks (default from config)
            
        Returns:
            Task object that can be used to cancel the scheduled tasks
        """
        return asyncio.create_task(
            self.run_scheduled_tasks(interval_seconds)
        )

# Helper function to create the safety tasks instance
def create_safety_tasks(position_service: PositionService, exchange_adapter: ExchangeAdapter) -> SafetyTasks:
    """Create a SafetyTasks instance with the provided services."""
    return SafetyTasks(position_service, exchange_adapter) 