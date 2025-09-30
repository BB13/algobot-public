import logging
import asyncio
from typing import Optional
import sys
import os
from datetime import datetime

# Add the project root to path for importing the fix script
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.tasks.fix_position_race import fix_position_race

from ..repositories.file_position_repository import FilePositionRepository
from ..core.position import PositionStatus
from src.core.config import SAFETY_MEASURES_INTERVAL
from src.core.user_settings import get_settings

logger = logging.getLogger(__name__)

class MaintenanceTasks:
    """
    Collection of maintenance tasks to keep the system healthy.
    These tasks can be scheduled to run periodically.
    """
    
    def __init__(self, position_repository: FilePositionRepository):
        """
        Initialize maintenance tasks with required dependencies.
        
        Args:
            position_repository: Repository for position data
        """
        self.position_repository = position_repository
        logger.info("MaintenanceTasks initialized")
    
    async def clean_closed_positions(self) -> int:
        """
        Check for closed positions in the open positions file and ensure they are
        properly moved to the closed positions file.
        
        This is a fail-safe mechanism to handle cases where positions are marked
        as closed but weren't properly moved during normal operation.
        
        Returns:
            Number of positions moved
        """
        logger.debug("Running maintenance task: clean_closed_positions")
        
        try:
            # Force reload to ensure we have the latest position state
            await self.position_repository.reload_positions()
            
            # Load all positions from the repository cache
            all_positions = []
            for positions_list in self.position_repository.positions_cache.values():
                all_positions.extend(positions_list)
            
            # Count positions with CLOSED status
            closed_positions = [p for p in all_positions if p.status == PositionStatus.CLOSED]
            if not closed_positions:
                logger.debug("No closed positions found in open positions file")
                return 0
            
            # Log details of what we found
            logger.info(f"Found {len(closed_positions)} closed positions in open positions file")
            for pos in closed_positions:
                logger.info(f"Position {pos.id} - {pos.asset.symbol} {pos.direction.value} is marked as closed")
            
            # Handle each closed position
            for position in closed_positions:
                try:
                    logger.debug(f"Handling closed position {position.id}")
                    await self.position_repository._handle_closed_position(position)
                except Exception as e:
                    logger.error(f"Error handling closed position {position.id}: {str(e)}")
            
            # Return the count of positions handled
            return len(closed_positions)
            
        except Exception as e:
            logger.error(f"Error in clean_closed_positions task: {str(e)}", exc_info=True)
            return 0
    
    async def run_scheduled_tasks(self, interval_seconds: Optional[int] = None) -> None:
        """
        Run all maintenance tasks on a schedule.
        
        Args:
            interval_seconds: How often to run the tasks (default: from config)
        """
        # If not provided, get interval from settings
        if interval_seconds is None:
            # Default to 3600 seconds if not found in settings
            interval_seconds = get_settings().config.get("safety", {}).get("check_interval", 3600)
        
        # Wait a moment for services to be fully ready after startup
        await asyncio.sleep(5)
        
        logger.info(f"Starting scheduled maintenance tasks (interval: {interval_seconds}s)")
        
        heartbeat_counter = 0
        while True:
            heartbeat_counter += 1
            
            # Only log every 10 iterations at INFO level to reduce noise (or on first run)
            if heartbeat_counter == 1 or heartbeat_counter % 10 == 0:
                logger.info(f"Maintenance task heartbeat {heartbeat_counter} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                logger.debug(f"Maintenance task iteration {heartbeat_counter}")
            
            try:
                # Run individual tasks and log results
                logger.debug("Running clean_closed_positions...")
                cleaned_positions = await self.clean_closed_positions()
                
                # Also run the position race condition fix
                try:
                    logger.debug("Running position race condition fix...")
                    await fix_position_race()
                    logger.debug("Position race condition fix completed")
                except Exception as e:
                    logger.error(f"Error running position race condition fix: {str(e)}", exc_info=True)
                
                # Log significant results at INFO level
                if cleaned_positions > 0:
                    logger.info(f"Maintenance completed: Moved {cleaned_positions} closed positions")
                else:
                    logger.debug("Maintenance completed: No issues found")
                
            except asyncio.CancelledError:
                logger.info("Scheduled maintenance tasks cancelled")
                break
            except Exception as e:
                logger.error(f"Error in scheduled maintenance: {str(e)}", exc_info=True)
                # Log but don't crash
            
            # Dynamically fetch the interval in case it was updated via settings
            try:
                # Get the current interval from settings (with fallback)
                current_interval = get_settings().config.get("safety", {}).get("check_interval", interval_seconds)
                if current_interval != interval_seconds:
                    logger.info(f"Maintenance check interval updated from {interval_seconds}s to {current_interval}s")
                    interval_seconds = current_interval
            except Exception as e:
                logger.error(f"Error updating maintenance interval: {str(e)}")
                # Continue with existing interval
            
            # Wait for the next interval
            try:
                logger.debug(f"Maintenance tasks sleeping for {interval_seconds} seconds")
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                logger.info("Maintenance task sleep canceled")
                break
            except Exception as e:
                logger.error(f"Error in maintenance task sleep: {str(e)}")
                # Fall back to a simpler sleep
                await asyncio.sleep(max(interval_seconds, 60))
    
    async def start_scheduled_tasks(self, interval_seconds: Optional[int] = None) -> asyncio.Task:
        """
        Start the scheduled maintenance tasks in the background.
        
        Args:
            interval_seconds: How often to run the tasks (optional, reads from config if not provided)
            
        Returns:
            Task object that can be used to cancel the scheduled tasks
        """
        return asyncio.create_task(
            self.run_scheduled_tasks(interval_seconds)
        )

# Helper function to create the maintenance tasks instance
def create_maintenance_tasks(position_repository: FilePositionRepository) -> MaintenanceTasks:
    """Create a MaintenanceTasks instance with the provided repository."""
    return MaintenanceTasks(position_repository) 