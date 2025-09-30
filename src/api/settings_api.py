"""
Settings API for AlgoBot.

This module provides API endpoints for interacting with user configuration settings,
allowing for live updating of settings while the application is running.
"""
import logging
import os
from typing import Dict, Any, Optional, List
import ruamel.yaml
import sys

from fastapi import APIRouter, HTTPException, Body, Depends, Header, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

# Import settings management
from src.core.user_settings import get_settings, UserSettings

# Get the root logger for more visibility
logger = logging.getLogger()
print(f"Settings API using logger: {logger.name}, level: {logger.level}", file=sys.stderr)

# --- Security Setup ---
# Define the API key header
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# Get the expected API key from environment variable
EXPECTED_API_KEY = os.getenv("SETTINGS_API_KEY")

async def verify_api_key(api_key: str = Security(API_KEY_HEADER)):
    """Dependency to verify the API key."""
    if not EXPECTED_API_KEY:
        # Log a warning if the key isn't configured on the server
        logger.critical("SETTINGS_API_KEY is not set in the environment. Settings API is unprotected.")
        # Depending on security posture, you might raise HTTPException here to block access
        # raise HTTPException(status_code=503, detail="Server configuration error: API Key not set")
        return # Allow access if not configured, but log critical warning

    if not api_key:
        logger.warning("Settings API access attempt without API key.")
        raise HTTPException(status_code=403, detail="Missing API Key in X-API-Key header")

    if api_key != EXPECTED_API_KEY:
        logger.warning("Settings API access attempt with invalid API key.")
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key # Return the key or some confirmation if needed
# --- End Security Setup ---

# Create router - Add dependency for authentication
router = APIRouter(
    prefix="/api/settings",
    tags=["Settings"],
    dependencies=[Depends(verify_api_key)] # Apply security to all routes in this router
)

# Models for request and response
class SettingUpdateRequest(BaseModel):
    """Model for updating a specific setting by its path."""
    path: List[str] = Field(..., description="Path to the setting in dot notation (e.g. ['adapters', 'binance_spot', 'enabled'])")
    value: Any = Field(..., description="New value for the setting")

class SettingResponse(BaseModel):
    """Model for API responses."""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None

@router.post("/update", response_model=SettingResponse)
async def update_setting(
    request: SettingUpdateRequest = Body(...),
    # The dependency is now handled at the router level
    # api_key: str = Depends(verify_api_key) # No longer needed here
):
    """
    Update a specific setting in the user configuration.
    
    Settings are identified by their path (e.g., ['adapters', 'binance_spot', 'enabled']).
    The changes are applied both in memory and persisted to the user_config.yaml file.
    """
    try:
        settings = get_settings()
        
        # Apply the update to the in-memory config
        success = _update_config_value(settings.config, request.path, request.value)
        if not success:
            raise HTTPException(status_code=400, message=f"Failed to update setting at path {request.path}")
        
        # Persist the change to the YAML file (preserving comments and formatting)
        _save_config_to_file(settings.config, settings.config_path)
        
        # Trigger reload to refresh cached resources
        settings.reload()
        
        # Log the change with a more noticeable format
        logger.warning(f"*** SETTINGS UPDATED *** Path: {request.path}, New Value: {request.value}")
        
        return SettingResponse(
            success=True,
            message=f"Setting at path {request.path} updated successfully",
            data={"path": request.path, "value": request.value}
        )
        
    except Exception as e:
        logger.error(f"Error updating setting: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update setting: {str(e)}")

@router.get("/", response_model=SettingResponse)
async def get_all_settings(
    # The dependency is now handled at the router level
    # api_key: str = Depends(verify_api_key) # No longer needed here
):
    """
    Get the complete user configuration.
    """
    try:
        settings = get_settings()
        
        return SettingResponse(
            success=True,
            message="Settings retrieved successfully",
            data=settings.config
        )
        
    except Exception as e:
        logger.error(f"Error retrieving settings: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve settings: {str(e)}")

def _update_config_value(config: Dict[str, Any], path: List[str], value: Any) -> bool:
    """
    Update a nested value in the configuration dictionary.
    
    Args:
        config: Configuration dictionary to update
        path: List of keys representing the path to the value
        value: New value to set
    
    Returns:
        True if update was successful, False otherwise
    """
    # Make a copy of the path to avoid modifying the original
    path_copy = path.copy()
    
    # Navigate to the nested dictionary that contains the value to update
    current = config
    while len(path_copy) > 1:
        key = path_copy.pop(0)
        if key not in current:
            current[key] = {}  # Create missing sections
        current = current[key]
    
    # Set the value
    if path_copy:
        final_key = path_copy[0]
        current[final_key] = value
        return True
    return False

def _save_config_to_file(config: Dict[str, Any], file_path: str) -> None:
    """
    Save the configuration to a YAML file, preserving comments and formatting.
    
    Args:
        config: Configuration dictionary to save
        file_path: Path to the YAML file
    """
    yaml = ruamel.yaml.YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    
    try:
        # Try to load existing file to preserve comments and formatting
        with open(file_path, 'r') as f:
            existing_yaml = yaml.load(f)
        
        # Update the existing YAML with new values
        _deep_update(existing_yaml, config)
        
        # Write back to file
        with open(file_path, 'w') as f:
            yaml.dump(existing_yaml, f)
    except Exception as e:
        # If that fails, write the config directly
        logger.warning(f"Could not preserve YAML formatting: {str(e)}. Writing config directly.")
        with open(file_path, 'w') as f:
            yaml.dump(config, f)

def _deep_update(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """
    Deep update of nested dictionaries.
    
    Args:
        target: Target dictionary to update
        source: Source dictionary with values to apply
    """
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            _deep_update(target[key], value)
        else:
            target[key] = value 