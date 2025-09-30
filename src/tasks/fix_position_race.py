#!/usr/bin/env python
"""
Fix position race conditions by ensuring no positions appear in both open and closed files.
This script can be run periodically or after observed issues.
"""
import json
import os
import logging
import shutil
from datetime import datetime
from pathlib import Path
import asyncio

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("fix_position_race")

# Paths to position files (using the same config as in the actual code)
POSITIONS_FILE = "src/data/open_positions.json"
CLOSED_POSITIONS_FILE = "src/data/closed_positions.json"
BACKUP_DIR = "src/data/backup"

def create_backup(file_path):
    """Create a backup of a file."""
    if not os.path.exists(file_path):
        return ""
    
    # Create backup filename with timestamp
    backup_name = f"{Path(file_path).stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{Path(file_path).suffix}"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    
    # Create backup directory if it doesn't exist
    os.makedirs(BACKUP_DIR, exist_ok=True)
    
    # Copy the file
    shutil.copy2(file_path, backup_path)
    logger.info(f"Created backup: {backup_path}")
    return backup_path

async def fix_position_race():
    """Fix position race conditions by ensuring positions don't appear in both files."""
    # Ensure both files exist
    if not os.path.exists(POSITIONS_FILE):
        logger.error(f"Open positions file not found: {POSITIONS_FILE}")
        return
    
    if not os.path.exists(CLOSED_POSITIONS_FILE):
        logger.warning(f"Closed positions file not found: {CLOSED_POSITIONS_FILE}")
        return
    
    # Create backups
    create_backup(POSITIONS_FILE)
    create_backup(CLOSED_POSITIONS_FILE)
    
    # Load open positions
    try:
        with open(POSITIONS_FILE, 'r') as f:
            open_positions = json.load(f)
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in open positions file: {POSITIONS_FILE}")
        return
    
    # Load closed positions
    try:
        with open(CLOSED_POSITIONS_FILE, 'r') as f:
            closed_positions = json.load(f)
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in closed positions file: {CLOSED_POSITIONS_FILE}")
        return
    
    # Collect all position IDs from closed positions
    closed_position_ids = set()
    for strategy, positions in closed_positions.items():
        for position in positions:
            if 'id' in position:
                closed_position_ids.add(position['id'])
    
    logger.info(f"Found {len(closed_position_ids)} positions in closed positions file")
    
    # Check for positions that appear in both files
    conflicting_positions = 0
    
    # Iterate through each strategy in open positions
    for strategy, positions in list(open_positions.items()):
        positions_to_remove = []
        
        # Iterate through each position in the strategy
        for i, position in enumerate(positions):
            position_id = position.get('id')
            
            # Check if this position ID is in the closed positions
            if position_id in closed_position_ids:
                logger.warning(f"Position {position_id} found in both open and closed files - removing from open")
                positions_to_remove.append(i)
                conflicting_positions += 1
            
            # Also check if status is CLOSED but still in open positions
            if position.get('status') == 'CLOSED':
                logger.warning(f"Position {position_id} marked as CLOSED but in open file - removing from open")
                if i not in positions_to_remove:
                    positions_to_remove.append(i)
                    conflicting_positions += 1
        
        # Remove the conflicting positions (iterate in reverse to maintain indexes)
        for index in sorted(positions_to_remove, reverse=True):
            del positions[index]
        
        # If no positions left for this strategy, remove the strategy entry
        if not positions:
            del open_positions[strategy]
    
    # Save the updated open positions file
    with open(POSITIONS_FILE, 'w') as f:
        json.dump(open_positions, f, indent=2)
    
    logger.info(f"Fixed {conflicting_positions} conflicting positions")
    
    # Additional step: Check for duplicates within closed positions file
    duplicate_closed = 0
    for strategy, positions in closed_positions.items():
        seen_ids = set()
        positions_to_keep = []
        
        for position in positions:
            position_id = position.get('id')
            if position_id and position_id not in seen_ids:
                positions_to_keep.append(position)
                seen_ids.add(position_id)
            else:
                duplicate_closed += 1
                logger.warning(f"Duplicate position {position_id} found in closed positions - removing duplicate")
        
        # Update to only keep unique positions
        closed_positions[strategy] = positions_to_keep
    
    if duplicate_closed > 0:
        # Save the deduplicated closed positions file
        with open(CLOSED_POSITIONS_FILE, 'w') as f:
            json.dump(closed_positions, f, indent=2)
        logger.info(f"Removed {duplicate_closed} duplicate positions from closed positions file")
    
    logger.info("Position files check and fix completed")

if __name__ == "__main__":
    asyncio.run(fix_position_race()) 