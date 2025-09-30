"""
Position repository interface for position storage.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any

from .position import Position


class PositionRepository(ABC):
    """
    Interface for position storage and retrieval operations.
    This allows different storage implementations (file, database, etc.)
    while providing a consistent API.
    """
    
    @abstractmethod
    async def save(self, position: Position) -> None:
        """
        Save a position to storage.
        
        Args:
            position: Position to save
            
        Raises:
            Exception: If save operation fails
        """
        pass
        
    @abstractmethod
    async def get_by_id(self, position_id: str) -> Optional[Position]:
        """
        Retrieve a position by its ID.
        
        Args:
            position_id: Unique identifier for the position
            
        Returns:
            Position if found, None otherwise
        """
        pass
        
    @abstractmethod
    async def get_open_positions(self, filters: Optional[Dict[str, Any]] = None) -> List[Position]:
        """
        Get all open positions, optionally filtered.
        
        Args:
            filters: Optional dictionary of filter criteria
            
        Returns:
            List of matching open positions
        """
        pass
        
    @abstractmethod
    async def get_closed_positions(self, filters: Optional[Dict[str, Any]] = None) -> List[Position]:
        """
        Get all closed positions, optionally filtered.
        
        Args:
            filters: Optional dictionary of filter criteria
            
        Returns:
            List of matching closed positions
        """
        pass
        
    @abstractmethod
    async def update(self, position: Position) -> None:
        """
        Update an existing position.
        
        Args:
            position: Position with updated data
            
        Raises:
            ValueError: If position doesn't exist
            Exception: If update operation fails
        """
        pass
        
    @abstractmethod
    async def delete(self, position_id: str) -> None:
        """
        Delete a position by ID.
        
        Args:
            position_id: Unique identifier for the position
            
        Raises:
            ValueError: If position doesn't exist
            Exception: If delete operation fails
        """
        pass
