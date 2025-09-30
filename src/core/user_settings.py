"""
User settings loader for AlgoBot.

This module handles loading, validating, and providing access to user configuration
from the user_config.yaml file.
"""
import os
import logging
from typing import Dict, Any, Optional
import yaml

from .exchange_adapter import ExchangeAdapter
from ..adapters.binance_adapter import create_binance_adapter

logger = logging.getLogger(__name__)

# Default path to user_config.yaml
DEFAULT_CONFIG_PATH = os.path.join(os.getcwd(), "user_config.yaml")


class UserSettings:
    """
    User settings manager for AlgoBot.
    
    Handles loading configuration from YAML and providing validated settings.
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize user settings manager.
        
        Args:
            config_path: Path to user_config.yaml (defaults to project root)
        """
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self.config = self._load_config()
        self._validate_config()
        
        # Cache for lazily-loaded resources
        self._exchange_adapter = None
        
    def _load_config(self) -> Dict[str, Any]:
        """
        Load user configuration from YAML file.
        
        Returns:
            Dict containing user configuration
            
        Raises:
            FileNotFoundError: If config file doesn't exist
            yaml.YAMLError: If YAML parsing fails
        """
        try:
            with open(self.config_path, "r") as f:
                config = yaml.safe_load(f)
                if not config: # Handle empty file case
                    logger.warning(f"Configuration file is empty: {self.config_path}. Using defaults.")
                    config = {}
                logger.info(f"Loaded user configuration from {self.config_path}")
                return config
        except FileNotFoundError:
            logger.error(f"Configuration file not found: {self.config_path}")
            # Create default config structure (will be filled by validation)
            config = {
                "adapters": {
                    "default": "binance_spot",
                    "binance_spot": {} # Defaults will be added in validation
                }
            }
            logger.warning("Created default configuration structure.")
            return config
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML configuration: {e}")
            raise
    
    def _validate_config(self):
        """Validate configuration and set defaults for missing values based on adapter structure."""
        # Ensure adapters section exists
        if "adapters" not in self.config:
            self.config["adapters"] = {}
        adapters_config = self.config["adapters"]

        # Ensure default adapter is specified
        if "default" not in adapters_config:
            adapters_config["default"] = "binance_spot" # Default to binance_spot
            logger.warning("No default adapter specified, defaulting to 'binance_spot'")
        default_adapter_name = adapters_config["default"]

        # Ensure the configuration block for the default adapter exists
        if default_adapter_name not in adapters_config:
            adapters_config[default_adapter_name] = {}
            logger.warning(f"Configuration block for default adapter '{default_adapter_name}' not found, creating empty block.")
        
        default_adapter_cfg = adapters_config[default_adapter_name]

        # Validate and set defaults for the default adapter configuration
        if "enabled" not in default_adapter_cfg:
            default_adapter_cfg["enabled"] = True # Default to enabled
        if "testnet" not in default_adapter_cfg:
            default_adapter_cfg["testnet"] = True # Default to testnet for safety
        if "use_margin_for_longs" not in default_adapter_cfg:
            default_adapter_cfg["use_margin_for_longs"] = False # Default to False
        
        # Ensure directions section exists within the adapter config
        if "directions" not in default_adapter_cfg:
            default_adapter_cfg["directions"] = {}
        directions = default_adapter_cfg["directions"]
        if "allow_long" not in directions:
            directions["allow_long"] = True
        if "allow_short" not in directions:
            directions["allow_short"] = True # Allow shorting via margin by default

        # Validate Spot Margin settings (even if named like futures for consistency)
        if "default_leverage" not in default_adapter_cfg:
            default_adapter_cfg["default_leverage"] = 3
        if "max_leverage" not in default_adapter_cfg:
            default_adapter_cfg["max_leverage"] = 10
        if "margin_type" not in default_adapter_cfg:
            default_adapter_cfg["margin_type"] = "CROSSED" # Default margin type
        
        # Ensure other top-level sections exist (if needed by other parts of the app)
        if "trading_parameters" not in self.config:
            self.config["trading_parameters"] = {} # Add sensible defaults if required
        if "shutdown" not in self.config:
             self.config["shutdown"] = {}
        if "logging" not in self.config:
             self.config["logging"] = {}
        if "chart_presets" not in self.config:
            self.config["chart_presets"] = {}
        if "risk_management" not in self.config: # Keep risk management validation if used elsewhere
            self.config["risk_management"] = {}

        logger.info("Configuration validated based on adapter structure.")

    @property
    def _default_adapter_config(self) -> Optional[Dict[str, Any]]:
        """Helper property to get the configuration of the default adapter."""
        if "adapters" not in self.config or "default" not in self.config["adapters"]:
            return None
        default_adapter_name = self.config["adapters"]["default"]
        return self.config["adapters"].get(default_adapter_name)

    @property
    def default_adapter_name(self) -> Optional[str]:
        """Get the name of the default adapter."""
        return self.config.get("adapters", {}).get("default")

    @property
    def is_testnet(self) -> bool:
        """Whether the default adapter is configured for testnet."""
        adapter_config = self._default_adapter_config
        return adapter_config.get("testnet", True) if adapter_config else True # Default to true if missing

    @property
    def allow_long_trades(self) -> bool:
        """Whether long trades are allowed by the default adapter."""
        adapter_config = self._default_adapter_config
        return adapter_config.get("directions", {}).get("allow_long", True) if adapter_config else True

    @property
    def allow_short_trades(self) -> bool:
        """Whether short trades (via margin) are allowed by the default adapter."""
        adapter_config = self._default_adapter_config
        return adapter_config.get("directions", {}).get("allow_short", True) if adapter_config else True

    @property
    def default_leverage(self) -> int:
        """Default leverage configured for the default spot margin adapter."""
        adapter_config = self._default_adapter_config
        return adapter_config.get("default_leverage", 3) if adapter_config else 3

    @property
    def max_leverage(self) -> int:
        """Maximum leverage configured for the default spot margin adapter."""
        adapter_config = self._default_adapter_config
        return adapter_config.get("max_leverage", 10) if adapter_config else 10

    @property
    def margin_type(self) -> str:
        """Margin type (CROSSED or ISOLATED) for the default spot margin adapter."""
        adapter_config = self._default_adapter_config
        return adapter_config.get("margin_type", "CROSSED").upper() if adapter_config else "CROSSED"

    @property
    def use_margin_for_longs(self) -> bool:
        """Whether LONG orders should use margin (MARGIN_BUY) based on config."""
        adapter_config = self._default_adapter_config
        return adapter_config.get("use_margin_for_longs", False) if adapter_config else False

    @property
    def chart_presets(self) -> Dict[str, Any]:
        """Get chart presets configuration."""
        return self.config.get("chart_presets", {})
    
    @property
    def risk_management(self) -> Dict[str, Any]:
        """Get risk management configuration."""
        return self.config.get("risk_management", {})

    @property
    def trading_parameters(self) -> Dict[str, Any]:
        """Get general trading parameters."""
        return self.config.get("trading_parameters", {})

    @property
    def shutdown_settings(self) -> Dict[str, Any]:
        """Get shutdown behavior settings."""
        return self.config.get("shutdown", {})

    @property
    def logging_settings(self) -> Dict[str, Any]:
        """Get logging configuration."""
        return self.config.get("logging", {})

    def get_exchange_adapter(self, api_key: Optional[str] = None, api_secret: Optional[str] = None) -> Optional[ExchangeAdapter]:
        """
        Get the configured default exchange adapter (only supports Binance Spot currently).
        
        Args:
            api_key: Optional API key to override the default from environment
            api_secret: Optional API secret to override the default from environment
            
        Returns:
            Configured exchange adapter, or None if not enabled or misconfigured.
        """
        if self._exchange_adapter is None:
            adapter_config = self._default_adapter_config
            adapter_name = self.default_adapter_name
            
            if not adapter_config or not adapter_name:
                 logger.error("Default adapter configuration not found.")
                 return None
                 
            if not adapter_config.get("enabled", False):
                logger.error(f"Default adapter '{adapter_name}' is not enabled in the configuration.")
                return None

            if adapter_name != "binance_spot":
                 logger.error(f"Unsupported default adapter configured: {adapter_name}. Only 'binance_spot' is currently supported.")
                 return None

            # We only support binance_spot which handles spot and spot margin
            try:
                self._exchange_adapter = create_binance_adapter(
                    # mode is always SPOT, as margin is part of the spot API
                    api_key=api_key, 
                    api_secret=api_secret,
                    testnet=self.is_testnet # Use the property which reads from adapter config
                )
                logger.info(f"Created Binance Spot exchange adapter (Testnet: {self.is_testnet})")
            except Exception as e:
                logger.error(f"Failed to create Binance Spot adapter: {e}", exc_info=True)
                return None # Return None on creation failure

        return self._exchange_adapter
    
    def is_trade_direction_allowed(self, direction: str) -> bool:
        """
        Check if a specific trade direction is allowed by the default adapter config.
        
        Args:
            direction: Trade direction to check ("LONG" or "SHORT")
            
        Returns:
            True if the direction is allowed, False otherwise
        """
        direction = direction.upper()
        if direction == "LONG":
            return self.allow_long_trades
        elif direction == "SHORT":
            # Shorting capability depends on the allow_short flag (implies margin usage)
            return self.allow_short_trades 
        else:
            logger.warning(f"Invalid trade direction checked: {direction}")
            return False
    
    def reload(self):
        """Reload configuration from file."""
        # Clear cached resources first
        self._exchange_adapter = None
        
        # Reload and revalidate
        self.config = self._load_config()
        self._validate_config()
        
        logger.info("Configuration reloaded and validated.")


# Create a global instance for easy imports
_settings = None


def get_settings(config_path: Optional[str] = None) -> UserSettings:
    """
    Get the user settings instance. Initializes if not already done.
    
    Args:
        config_path: Optional path to config file for first-time initialization.
        
    Returns:
        UserSettings instance
    """
    global _settings
    if _settings is None:
        logger.info(f"Initializing UserSettings singleton (config path: {config_path or DEFAULT_CONFIG_PATH}).")
        _settings = UserSettings(config_path)
    elif config_path and _settings.config_path != config_path:
         # This case should ideally not happen if used as a singleton,
         # but log a warning if an attempt is made to re-initialize with a different path.
         logger.warning(f"UserSettings already initialized with path '{_settings.config_path}'. Ignoring new path '{config_path}'.")

    return _settings 