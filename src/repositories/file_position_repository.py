"""
File-based position repository implementation with transaction safety.
"""
import os
import json
import shutil
import logging
import csv
import tempfile
import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Any, Union, Tuple, Set
from pathlib import Path

from .file_lock import read_lock, write_lock, FileLockException

from ..core.asset import Asset
from ..core.position import Position, PositionDirection, PositionStatus, TakeProfit
from ..core.position_repository import PositionRepository
from ..core.config import POSITIONS_FILE, CLOSED_POSITIONS_FILE, TRADE_OUTCOMES_FILE


logger = logging.getLogger(__name__)


class FilePositionRepository(PositionRepository):
    """
    File-based implementation of the position repository.
    
    This repository stores positions in JSON files - one for open positions
    and another for closed positions. It also records trade outcomes in a CSV file.
    
    Features:
    - Transactional file operations with temp file approach
    - Automatic backup creation
    - Recovery from corrupted files
    """
    
    def __init__(
        self, 
        positions_file: str = POSITIONS_FILE,
        closed_positions_file: str = CLOSED_POSITIONS_FILE,
        trade_outcomes_file: str = TRADE_OUTCOMES_FILE,
        backup_dir: Optional[str] = None
    ):
        """
        Initialize the repository with file paths.
        
        Args:
            positions_file: Path to the open positions JSON file
            closed_positions_file: Path to the closed positions JSON file
            trade_outcomes_file: Path to the trade outcomes CSV file
            backup_dir: Directory for backups (defaults to 'backup' subdirectory)
        """
        self.positions_file = positions_file
        self.closed_positions_file = closed_positions_file
        self.trade_outcomes_file = trade_outcomes_file
        
        # Set up backup directory
        if backup_dir is None:
            self.backup_dir = os.path.join(os.path.dirname(positions_file), "backup")
        else:
            self.backup_dir = backup_dir
        
        # Create directories if they don't exist
        for directory in [os.path.dirname(positions_file), 
                          os.path.dirname(closed_positions_file),
                          os.path.dirname(trade_outcomes_file),
                          self.backup_dir]:
            os.makedirs(directory, exist_ok=True)
        
        # Ensure files exist with valid JSON
        self._ensure_valid_json_file(positions_file)
        self._ensure_valid_json_file(closed_positions_file)
        
        # Ensure trade outcomes file exists with header
        if not os.path.exists(trade_outcomes_file):
            with open(trade_outcomes_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'bot_strategy', 'bot_settings', 'timeframe', 'asset',
                    'id', 'direction', 'initial_value', 'final_value', 'profit',
                    'profit_percentage', 'take_profit_count', 'take_profit_max', 'duration'
                ])
        
        self.positions_cache = {}  # In-memory cache of positions
        self._load_positions()  # Load positions from file into cache
    
    def _ensure_valid_json_file(self, file_path: str) -> None:
        """
        Ensure a file exists and contains valid JSON data.
        
        Args:
            file_path: Path to the JSON file
        """
        try:
            # If file doesn't exist, create it with empty JSON object
            if not os.path.exists(file_path):
                with open(file_path, 'w') as f:
                    json.dump({}, f)
                return
            
            # If file exists, try to load it to validate JSON
            with open(file_path, 'r') as f:
                json.load(f)
        except json.JSONDecodeError:
            # If JSON is invalid, backup the file and create a new one
            logger.error(f"Invalid JSON in {file_path}, creating backup and new file")
            
            # Only create a backup if file has actual content to back up
            if os.path.getsize(file_path) > 10:  # Only backup if file has meaningful content
                backup_file = self._create_backup(file_path)
                logger.info(f"Backed up corrupted file to {backup_file}")
            
            # Create new file with empty JSON object
            with open(file_path, 'w') as f:
                json.dump({}, f)
    
    def _create_backup(self, file_path: str) -> str:
        """
        Create a backup of a file.
        
        Args:
            file_path: Path to the file to backup
            
        Returns:
            Path to the backup file
        """
        if not os.path.exists(file_path):
            return ""
        
        # Create backup filename with timestamp - use only hours/minutes to reduce frequency
        backup_name = f"{Path(file_path).stem}_error_{datetime.now().strftime('%Y%m%d_%H')}{Path(file_path).suffix}"
        backup_path = os.path.join(self.backup_dir, backup_name)
        
        # If backup with this name already exists, don't overwrite it
        if os.path.exists(backup_path):
            return backup_path
            
        # Copy the file
        shutil.copy2(file_path, backup_path)
        return backup_path
    
    async def save(self, position: Position) -> None:
        """
        Save a position to storage.
        
        Args:
            position: Position to save
            
        Raises:
            Exception: If save operation fails
        """
        try:
            # Generate key for position
            key = self._generate_key(position)
            
            # Add to positions cache
            if key not in self.positions_cache:
                self.positions_cache[key] = []
                
            # Check if position already exists
            existing_index = next(
                (i for i, p in enumerate(self.positions_cache[key]) if p.id == position.id),
                None
            )
            
            if existing_index is not None:
                # Update existing position
                self.positions_cache[key][existing_index] = position
            else:
                # Add new position
                self.positions_cache[key].append(position)
            
            # Save to file
            await self._save_positions_transactional()
            
            logger.info(f"Saved position {position.id} - {position.asset.symbol} {position.direction.value}")
            
        except Exception as e:
            logger.error(f"Error saving position: {str(e)}", exc_info=True)
            raise
    
    async def get_by_id(self, position_id: str) -> Optional[Position]:
        """
        Retrieve a position by its ID.
        
        Args:
            position_id: Unique identifier for the position
            
        Returns:
            Position if found, None otherwise
        """
        # Search all positions for matching ID
        for positions in self.positions_cache.values():
            for position in positions:
                if position.id == position_id:
                    return position
        
        return None
    
    async def get_open_positions(self, filters: Optional[Dict[str, Any]] = None) -> List[Position]:
        """
        Get all open positions, optionally filtered.
        
        Args:
            filters: Optional dictionary of filter criteria
            
        Returns:
            List of matching open positions
        """
        # Start with all positions
        all_positions = [
            p for positions in self.positions_cache.values() 
            for p in positions if not p.is_closed
        ]
        
        # Apply filters if provided
        if filters:
            filtered_positions = []
            for position in all_positions:
                if self._matches_filters(position, filters):
                    filtered_positions.append(position)
            return filtered_positions
        
        return all_positions
    
    async def get_closed_positions(self, filters: Optional[Dict[str, Any]] = None) -> List[Position]:
        """
        Get all closed positions, optionally filtered.
        
        This retrieves from the closed positions file, not the in-memory cache.
        
        Args:
            filters: Optional dictionary of filter criteria
            
        Returns:
            List of matching closed positions
        """
        closed_positions = self._load_closed_positions()
        all_closed = []
        
        # Convert dictionaries to Position objects
        for key, positions_data in closed_positions.items():
            for data in positions_data:
                # Extract symbol from key
                asset_symbol = key.split('_')[-1]
                
                # Create Asset object
                asset = Asset(
                    symbol=asset_symbol,
                    asset_type="crypto",
                    exchange_id="binance"
                )
                
                try:
                    # Create Position object
                    position = self._create_position_from_dict(data, asset)
                    all_closed.append(position)
                    
                except Exception as e:
                    logger.error(f"Error creating Position from data: {e}")
                    logger.error(f"Problematic data: {data}")
        
        # Apply filters if provided
        if filters:
            filtered_positions = []
            for position in all_closed:
                if self._matches_filters(position, filters):
                    filtered_positions.append(position)
            return filtered_positions
        
        return all_closed
    
    async def update(self, position: Position) -> None:
        """
        Update an existing position.
        
        Args:
            position: Position with updated data
            
        Raises:
            ValueError: If position doesn't exist
            Exception: If update operation fails
        """
        # Find and update position
        existing_position = await self.get_by_id(position.id)
        
        if not existing_position:
            raise ValueError(f"Position not found: {position.id}")
        
        # Save the updated position
        await self.save(position)
        
        # If position is now closed, handle closing
        if position.is_closed:
            await self._handle_closed_position(position)
    
    async def delete(self, position_id: str) -> None:
        """
        Delete a position by ID.
        
        Args:
            position_id: Unique identifier for the position
            
        Raises:
            ValueError: If position doesn't exist
            Exception: If delete operation fails
        """
        position = await self.get_by_id(position_id)
        
        if not position:
            raise ValueError(f"Position not found: {position_id}")
        
        # Find key and index
        key = self._generate_key(position)
        position_removed = False
        
        if key in self.positions_cache:
            positions = self.positions_cache[key]
            index = next((i for i, p in enumerate(positions) if p.id == position_id), None)
            
            if index is not None:
                # Remove position from list
                del positions[index]
                position_removed = True
                
                # Remove key if list is empty
                if not positions:
                    del self.positions_cache[key]
                
                # Save changes
                await self._save_positions_transactional()
                
                logger.info(f"Deleted position {position_id} from key {key}")
                
        # If we couldn't find the position by key/index, try a full search
        if not position_removed:
            found = False
            # Look through all keys in the cache
            for cache_key, positions_list in list(self.positions_cache.items()):
                filtered_positions = [p for p in positions_list if p.id != position_id]
                if len(filtered_positions) < len(positions_list):
                    # We found and removed the position
                    found = True
                    self.positions_cache[cache_key] = filtered_positions
                    # Remove key if list is now empty
                    if not filtered_positions:
                        del self.positions_cache[cache_key]
                    await self._save_positions_transactional()
                    logger.info(f"Deleted position {position_id} from alternate key {cache_key}")
                    break
                    
            if not found:
                logger.warning(f"Position {position_id} not found in any cache keys for deletion")
                raise ValueError(f"Position not found in cache: {position_id}")
    
    def _generate_key(self, position: Position) -> str:
        """
        Generate a unique key for a position.
        
        Args:
            position: Position to generate key for
            
        Returns:
            String key in format {bot_strategy}_{bot_settings}_{timeframe}_{asset_symbol}
        """
        return f"{position.bot_strategy}_{position.bot_settings}_{position.timeframe}_{position.asset.symbol}"
    
    def _load_positions(self) -> None:
        """
        Load positions from the positions file into the cache.
        """
        self.positions_cache = {}
        
        try:
            if os.path.exists(self.positions_file) and os.path.getsize(self.positions_file) > 0:
                # Use a read lock to safely read the file
                with read_lock(self.positions_file) as f:
                    try:
                        data = json.load(f)
                        
                        for key, positions_data in data.items():
                            positions_list = []
                            
                            for p_data in positions_data:
                                try:
                                    # Extract symbol from key
                                    asset_symbol = key.split('_')[-1]
                                    
                                    # Create Asset object
                                    asset = Asset(
                                        symbol=asset_symbol,
                                        asset_type="crypto",
                                        exchange_id="binance"
                                    )
                                    
                                    # Create Position object
                                    position = self._create_position_from_dict(p_data, asset)
                                    positions_list.append(position)
                                    
                                except Exception as e:
                                    logger.error(f"Error creating Position from data: {e}")
                                    logger.error(f"Problematic data: {p_data}")
                                    
                            if positions_list:
                                self.positions_cache[key] = positions_list
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON decode error in positions file: {str(e)}")
                        # Initialize empty cache if JSON is invalid
                        self.positions_cache = {}
                        
                        # Create backup of problematic file
                        self._create_backup(self.positions_file)
                        
                        # Create new empty file
                        with open(self.positions_file, 'w') as f:
                            json.dump({}, f)
                    except Exception as e:
                        logger.error(f"Unexpected error reading positions file: {str(e)}")
                        self.positions_cache = {}
                        
        except FileLockException as e:
            logger.error(f"Could not acquire lock on positions file: {str(e)}")
            # Initialize empty cache if we can't get a lock
            self.positions_cache = {}
        except Exception as e:
            logger.error(f"Error loading positions: {str(e)}", exc_info=True)
            # Initialize empty cache if loading fails
            self.positions_cache = {}
            
            # Create backup of problematic file
            if os.path.exists(self.positions_file):
                self._create_backup(self.positions_file)
    
    async def _save_positions_transactional(self) -> None:
        """
        Save positions from the cache to the positions file using a transaction-safe approach.
        
        This uses a temporary file and atomic rename to ensure data integrity.
        """
        try:
            # Convert positions to dictionaries
            data = {}
            
            for key, positions in self.positions_cache.items():
                data[key] = [p.to_dict() for p in positions]
            
            # Create parent directories if they don't exist
            os.makedirs(os.path.dirname(self.positions_file), exist_ok=True)
            
            # Create backup before replacing (once a day is enough)
            # Use only the date part with no time to ensure only one backup per day
            today_backup = os.path.join(
                self.backup_dir,
                f"{Path(self.positions_file).stem}_daily{Path(self.positions_file).suffix}"
            )
            
            # Check if we already have a backup for today based on modification time
            create_backup = True
            if os.path.exists(today_backup):
                # Get file modification time
                backup_mtime = os.path.getmtime(today_backup)
                backup_date = datetime.fromtimestamp(backup_mtime).date()
                today_date = datetime.now().date()
                
                # If we already have a backup from today, skip creating another one
                if backup_date == today_date:
                    create_backup = False
                    logger.debug(f"Daily backup already exists for today at {today_backup}")
            
            if create_backup and os.path.exists(self.positions_file):
                try:
                    with read_lock(self.positions_file) as f:
                        content = f.read()
                    
                    # Write backup with exclusive lock
                    os.makedirs(os.path.dirname(today_backup), exist_ok=True)
                    with write_lock(today_backup) as f:
                        f.write(content)
                    
                    logger.debug(f"Created daily backup at {today_backup}")
                except (FileLockException, Exception) as e:
                    logger.warning(f"Failed to create backup: {str(e)}")
            
            # Save with an exclusive write lock
            try:
                with write_lock(self.positions_file) as f:
                    # Clear the file and write new data
                    f.seek(0)
                    f.truncate()
                    json.dump(data, f, indent=2)
                    
                logger.debug(f"Successfully saved positions to {self.positions_file}")
                    
            except FileLockException as e:
                logger.error(f"Could not acquire write lock: {str(e)}")
                # Try the temp file approach as fallback
                self._save_positions_with_tempfile(data)
                
        except Exception as e:
            logger.error(f"Error saving positions: {str(e)}", exc_info=True)
            # Try the temp file approach as fallback
            self._save_positions_with_tempfile(data)
    
    def _save_positions_with_tempfile(self, data: Dict) -> None:
        """
        Save positions using a temporary file as a fallback method.
        
        Args:
            data: Dictionary of position data to save
        """
        try:
            # Create a temporary file
            temp_dir = os.path.dirname(self.positions_file)
            os.makedirs(temp_dir, exist_ok=True)
            
            with tempfile.NamedTemporaryFile(mode='w', dir=temp_dir, delete=False, suffix='.json') as temp_file:
                # Write data to temporary file
                json.dump(data, temp_file, indent=2)
                temp_file_path = temp_file.name

            # Replace the original file with the temporary file
            if os.name == 'nt':  # Windows
                # On Windows, we need to remove the target file first
                if os.path.exists(self.positions_file):
                    os.remove(self.positions_file)
            
            os.replace(temp_file_path, self.positions_file)
            logger.debug(f"Successfully saved positions to {self.positions_file} using temp file")
                
        except Exception as e:
            logger.error(f"Error saving positions with temp file: {str(e)}", exc_info=True)
            # If temp file exists but wasn't renamed, clean it up
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except:
                    pass
            raise
    
    def _load_closed_positions(self) -> Dict[str, List[Dict]]:
        """
        Load closed positions from the closed positions file.
        
        Returns:
            Dictionary of closed positions
        """
        try:
            if os.path.exists(self.closed_positions_file) and os.path.getsize(self.closed_positions_file) > 0:
                # Use a read lock to safely read
                with read_lock(self.closed_positions_file) as f:
                    try:
                        return json.load(f)
                    except json.JSONDecodeError:
                        logger.error(f"Error decoding JSON from {self.closed_positions_file}. Creating backup.")
                        self._create_backup(self.closed_positions_file)
                        return {}
            return {}
        except FileLockException as e:
            logger.error(f"Could not acquire lock on closed positions file: {str(e)}")
            return {}
        except Exception as e:
            logger.error(f"Error loading closed positions: {str(e)}", exc_info=True)
            return {}
    
    async def _handle_closed_position(self, position: Position) -> None:
        """
        Handle a closed position - save to closed positions file and record outcome.
        
        Args:
            position: Closed position to handle
        """
        if not position.is_closed:
            logger.warning(f"Position {position.id} is not marked as closed, cannot handle as closed position")
            return
            
        try:
            logger.info(f"Handling closed position {position.id} - {position.asset.symbol}")
            
            # Save to closed positions file
            closed_positions = self._load_closed_positions()
            
            key = self._generate_key(position)
            
            if key not in closed_positions:
                closed_positions[key] = []
            
            # Check if this position is already in closed positions to avoid duplicates
            position_already_closed = False
            for closed_position in closed_positions.get(key, []):
                if closed_position.get('id') == position.id:
                    logger.warning(f"Position {position.id} already exists in closed positions file, updating it")
                    position_already_closed = True
                    # Remove the old entry and add the new one
                    closed_positions[key] = [p for p in closed_positions[key] if p.get('id') != position.id]
                    break
                    
            # Add closed position with timestamp
            position_data = position.to_dict()
            position_data['closed_at'] = datetime.now().isoformat()
            
            closed_positions[key].append(position_data)
            
            # Save updated closed positions
            self._save_file_transactional(self.closed_positions_file, closed_positions)
            
            # Record trade outcome
            await self._record_trade_outcome(position)
            
            # Remove from open positions
            try:
                # Only attempt to delete if position hasn't been handled before
                if not position_already_closed:
                    await self.delete(position.id)
                    logger.info(f"Removed position {position.id} from open positions")
            except ValueError as e:
                # If position is already removed, just log a warning
                logger.warning(f"Position {position.id} could not be deleted from open positions: {str(e)}")
            except Exception as e:
                # For other errors, this is more serious but we should still continue
                logger.error(f"Error removing position {position.id} from open positions: {str(e)}", exc_info=True)
                
                # As a fallback, try to remove the position directly from the cache
                try:
                    key = self._generate_key(position)
                    if key in self.positions_cache:
                        self.positions_cache[key] = [p for p in self.positions_cache[key] if p.id != position.id]
                        if not self.positions_cache[key]:
                            del self.positions_cache[key]
                        await self._save_positions_transactional()
                        logger.info(f"Position {position.id} removed from cache using fallback method")
                except Exception as inner_e:
                    logger.error(f"Fallback removal also failed for position {position.id}: {str(inner_e)}")
            
            logger.info(f"Successfully handled closed position {position.id} - {position.asset.symbol}")
            
        except Exception as e:
            logger.error(f"Error handling closed position {position.id}: {str(e)}", exc_info=True)
    
    def _save_file_transactional(self, file_path: str, data: Any) -> None:
        """
        Save data to a file using a transaction-safe approach.
        
        Args:
            file_path: Path to the file
            data: Data to save (must be JSON serializable)
        """
        try:
            # Create parent directory if it doesn't exist
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            # Try to acquire a write lock
            try:
                with write_lock(file_path) as f:
                    # Clear the file and write new data
                    f.seek(0)
                    f.truncate()
                    json.dump(data, f, indent=2)
                    
                logger.debug(f"Successfully saved data to {file_path}")
                return
            except FileLockException as e:
                logger.warning(f"Could not acquire write lock: {str(e)}, using temp file approach")
            
            # Fall back to temp file approach if lock acquisition fails
            temp_dir = os.path.dirname(file_path)
            with tempfile.NamedTemporaryFile(mode='w', dir=temp_dir, delete=False, suffix='.json') as temp_file:
                # Write data to temporary file
                json.dump(data, temp_file, indent=2)
                temp_file_path = temp_file.name
            
            # Replace the original file with the temporary file
            if os.name == 'nt':  # Windows
                # On Windows, we need to remove the target file first
                if os.path.exists(file_path):
                    os.remove(file_path)
            
            os.replace(temp_file_path, file_path)
            logger.debug(f"Successfully saved data to {file_path} using temp file")
                
        except Exception as e:
            logger.error(f"Error saving data to {file_path}: {str(e)}", exc_info=True)
            # If temp file exists but wasn't renamed, clean it up
            if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except:
                    pass
            raise
    
    async def _record_trade_outcome(self, position: Position) -> None:
        """
        Record trade outcome to CSV file.
        
        Args:
            position: Closed position to record
        """
        try:
            # Calculate realized PnL
            realized_pnl = position.get_realized_pnl()
            
            # Calculate initial and final values
            initial_value = position.entry_price * position.initial_quantity
            
            # Calculate take profit value
            take_profit_value = sum(tp.price * tp.quantity for tp in position.take_profits)
            
            # Calculate close value
            close_value = Decimal('0')
            if position.close_data:
                close_price = Decimal(position.close_data.get('price', '0'))
                close_quantity = Decimal(position.close_data.get('quantity', '0'))
                close_value = close_price * close_quantity
            
            # Calculate final value
            final_value = take_profit_value + close_value
            
            # Calculate profit percentage
            profit_percentage = Decimal('0')
            if initial_value > 0:
                profit_percentage = (realized_pnl / initial_value) * Decimal('100')
            
            # Calculate duration in hours
            start_time = position.timestamp
            end_time = datetime.now()
            if position.close_data and 'timestamp' in position.close_data:
                try:
                    end_time = datetime.fromisoformat(position.close_data['timestamp'])
                except (ValueError, TypeError):
                    pass
            
            duration_hours = (end_time - start_time).total_seconds() / 3600
            
            # Prepare outcome data
            outcome = {
                'timestamp': datetime.now().isoformat(),
                'bot_strategy': position.bot_strategy,
                'bot_settings': position.bot_settings,
                'timeframe': position.timeframe,
                'asset': position.asset.symbol,
                'id': position.id,
                'direction': position.direction.value,
                'initial_value': str(initial_value),
                'final_value': str(final_value),
                'profit': str(realized_pnl),
                'profit_percentage': f"{profit_percentage:.2f}%",
                'take_profit_count': len(position.take_profits),
                'take_profit_max': position.take_profit_max,
                'duration': duration_hours
            }
            
            # Append to CSV file using a safe approach
            file_exists = os.path.exists(self.trade_outcomes_file)
            
            with open(self.trade_outcomes_file, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=outcome.keys())
                if not file_exists:
                    writer.writeheader()
                writer.writerow(outcome)
                
            logger.info(f"Recorded trade outcome for {position.id} - PnL: {realized_pnl}")
            
        except Exception as e:
            logger.error(f"Error recording trade outcome: {str(e)}", exc_info=True)
    
    def _matches_filters(self, position: Position, filters: Dict[str, Any]) -> bool:
        """
        Check if a position matches the given filters.
        
        Args:
            position: Position to check
            filters: Dictionary of filter criteria
            
        Returns:
            True if position matches all filters, False otherwise
        """
        for key, value in filters.items():
            if key == 'asset':
                if position.asset.symbol != value:
                    return False
            elif key == 'direction':
                if position.direction.value != value:
                    return False
            elif key == 'bot_strategy':
                if position.bot_strategy != value:
                    return False
            elif key == 'timeframe':
                if position.timeframe != value:
                    return False
            elif key == 'bot_settings':
                if position.bot_settings != value:
                    return False
            elif key == 'bot_strategy':  # Alias for bot_strategy
                if position.bot_strategy != value:
                    return False
            # Add more filter criteria as needed
            
        return True
    
    def _create_position_from_dict(self, data: Dict[str, Any], asset: Asset) -> Position:
        """
        Create a Position object from dictionary data.
        
        Args:
            data: Dictionary containing position data
            asset: Asset object for the position
            
        Returns:
            New Position object
        """
        # Extract parts from the data that need special handling
        direction = PositionDirection(data.get('direction', 'LONG').upper())
        
        # Set bot strategy if it's not in the data
        bot_strategy = data.get('bot_strategy', None)
        if not bot_strategy:
            # Try to extract from other fields
            if 'bot_strategy' in data:
                bot_strategy = data['bot_strategy']
        
        # Create the position
        position = Position(
            asset=asset,
            direction=direction,
            initial_quantity=Decimal(data.get('initial_quantity', '0')),
            entry_price=Decimal(data.get('entry_price', '0')),
            bot_strategy=bot_strategy or "",
            timeframe=data.get('timeframe', ""),
            bot_settings=data.get('bot_settings', 'default'),
            leverage=Decimal(data.get('leverage', '1')),
            id=data.get('id', ''),
            timestamp=datetime.fromisoformat(data.get('timestamp', datetime.now().isoformat())),
            take_profit_max=data.get('take_profit_max', 3),
            external_id=data.get('external_id')
        )
        
        # Set remaining quantity
        if 'remaining_quantity' in data:
            position.remaining_quantity = Decimal(data['remaining_quantity'])
        
        # Set status
        if 'status' in data:
            position.status = PositionStatus(data['status'].upper())
        
        # Handle take profits
        for tp_data in data.get('take_profits', []):
            # Create proper TakeProfit objects instead of dictionaries
            position.take_profits.append(
                TakeProfit(
                    level=tp_data.get('level', 1),
                    price=Decimal(tp_data.get('price', '0')),
                    quantity=Decimal(tp_data.get('quantity', '0')),
                    timestamp=datetime.fromisoformat(tp_data.get('timestamp', datetime.now().isoformat()))
                )
            )
        
        # Set close data
        if data.get('close_data'):
            position.close_data = data['close_data']
            position.status = PositionStatus.CLOSED
            
        # If position is supposed to be closed but status doesn't reflect it
        if position.is_closed and position.remaining_quantity > 0:
            position.remaining_quantity = Decimal('0')
        
        return position

    async def reload_positions(self) -> None:
        """
        Force reload positions from disk to ensure cache is up-to-date.
        
        This method refreshes the in-memory cache by reloading all positions
        from the positions files, ensuring that the latest data is used.
        """
        logger.debug("Reloading positions from files to refresh cache")
        # Clear the cache
        self.positions_cache = {}
        # Reload from files
        self._load_positions()
        logger.debug("Positions reloaded successfully")
        
        # Return early if there's an error loading positions
        try:
            # Check for positions that exist in both open and closed files
            closed_pos_dict = self._load_closed_positions()
            closed_ids = set()
            for key in closed_pos_dict:
                for pos in closed_pos_dict[key]:
                    if "id" in pos:
                        closed_ids.add(pos["id"])
            
            # Check for positions that appear in both files (potential race condition)
            for key in list(self.positions_cache.keys()):
                for position in list(self.positions_cache[key]):
                    if position.id in closed_ids:
                        logger.warning(f"Position found in both open and closed files: {position.id}. Will prioritize closed status.")
                        # We could auto-remove it here, but we'll let the normal position handlers take care of it
                        # for safety. The maintenance tasks will clean this up.
        except Exception as e:
            logger.error(f"Error checking for position overlap during reload: {e}")
            # Continue - don't fail the reload operation


# Simple factory function to create a file repository
def create_file_position_repository() -> FilePositionRepository:
    """
    Create a new file-based position repository.
    
    Returns:
        Configured FilePositionRepository instance
    """
    return FilePositionRepository()