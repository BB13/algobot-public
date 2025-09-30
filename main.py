"""
Main application entry point.
"""
import os
import logging
import asyncio
import multiprocessing
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Any

# Add dotenv import
from dotenv import load_dotenv

import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# Load environment variables from .env file
load_dotenv()

# Change relative imports to absolute imports
from src.api.webhook import router as webhook_router
from src.api.settings_api import router as settings_router
from src.api.settings_web_ui import router as settings_web_ui_router
from src.adapters.binance_adapter import create_binance_adapter, BinanceAdapter
from src.repositories.file_position_repository import create_file_position_repository, FilePositionRepository
from src.services.position_service import PositionService
from src.services.signal_processor import SignalProcessor
# Import tasks
from src.tasks.maintenance_tasks import create_maintenance_tasks, MaintenanceTasks
from src.tasks.safety_tasks import create_safety_tasks, SafetyTasks

# Import position race condition fix
from src.tasks.fix_position_race import fix_position_race

# Import Telegram bot modules
from src.telegram.telegram_bot import get_telegram_bot

# Import the new logging configuration
from src.core.logging_config import configure_logging, get_order_logger

from src.core.config import (
    DEFAULT_TP_PERCENTAGES_3,
    DEFAULT_TP_PERCENTAGES_4,
    DEFAULT_TRADE_AMOUNT,
    HOST,
    PORT,
    SAFETY_MEASURES_INTERVAL,
    STOP_LOSS_PERCENTAGE,
    MAX_STOP_LOSS_PERCENTAGE,
    LONG_TERM_TRADE_HRS,
    TELEGRAM_SECRET,
    get_adapter_settings
)

# Import the new shutdown tasks
from src.tasks.shutdown_tasks import close_all_positions_on_shutdown

# Get API keys and secrets from environment variables
BINANCE_SPOT_API_KEY = os.getenv("BINANCE_SPOT_API_KEY")
BINANCE_SPOT_API_SECRET = os.getenv("BINANCE_SPOT_API_SECRET")
TELEGRAM_SECRET = os.getenv("TELEGRAM_SECRET")

# Create necessary directories - do this FIRST
print(f"Creating logs directory at: {os.path.join(os.getcwd(), 'logs')}")
logs_dir = os.path.join(os.getcwd(), 'logs')
os.makedirs(logs_dir, exist_ok=True)
print(f"Logs directory exists: {os.path.exists(logs_dir)}")

# Configure logging using the new configuration
print("About to configure logging...")
configure_logging(log_level="WARNING")
logger = logging.getLogger(__name__)

# Set specific loggers to more verbose levels
logging.getLogger("src.tasks").setLevel(logging.INFO)  # Ensure task logs are always visible
logging.getLogger("src.repositories").setLevel(logging.INFO)  # Might help with debugging

# Set httpx log level to WARNING to reduce verbosity
logging.getLogger("httpx").setLevel(logging.WARNING)

# Create FastAPI app
app = FastAPI(
    title="Trading Bot API",
    description="Handles trading signals and manages positions.",
    version="1.0.0"
)

# --- Add Static Files Mounting ---
# Define the path to your frontend/static directory relative to main.py
# Assuming main.py is in the root directory alongside the 'src' folder
static_dir_path = os.path.join(os.path.dirname(__file__), "src", "frontend", "static")

# Check if the directory exists before mounting
if os.path.exists(static_dir_path) and os.path.isdir(static_dir_path):
    app.mount("/static", StaticFiles(directory=static_dir_path), name="static")
    logger.info(f"Mounted static files directory: {static_dir_path}")
else:
    logger.warning(f"Static files directory not found at {static_dir_path}, skipping mount.")
# --- End Static Files Mounting ---

# Global instances (initialized during startup)
_exchange_adapter: Optional[BinanceAdapter] = None
_position_repository: Optional[FilePositionRepository] = None
_position_service: Optional[PositionService] = None
_signal_processor: Optional[SignalProcessor] = None
_maintenance_tasks: Optional[MaintenanceTasks] = None
_maintenance_task: Optional[asyncio.Task] = None
_safety_tasks: Optional[SafetyTasks] = None
_safety_task: Optional[asyncio.Task] = None


# Accessor functions for dependencies
def get_exchange_adapter() -> BinanceAdapter:
    """Get the global exchange adapter instance."""
    if _exchange_adapter is None:
        raise RuntimeError("Exchange adapter not initialized")
    return _exchange_adapter

def get_position_repository() -> FilePositionRepository:
    """Get the global position repository instance."""
    if _position_repository is None:
        raise RuntimeError("Position repository not initialized")
    return _position_repository

def get_position_service() -> PositionService:
    """Get the global position service instance."""
    if _position_service is None:
        raise RuntimeError("Position service not initialized")
    return _position_service

def get_maintenance_tasks() -> MaintenanceTasks:
    """Get the global maintenance tasks instance."""
    if _maintenance_tasks is None:
        raise RuntimeError("Maintenance tasks not initialized")
    return _maintenance_tasks

def get_safety_tasks() -> SafetyTasks:
    """Get the global safety tasks instance."""
    if _safety_tasks is None:
        raise RuntimeError("Safety tasks not initialized")
    return _safety_tasks


# Startup event
@app.on_event("startup")
async def startup_event():
    """Initialize resources on application startup."""
    global _exchange_adapter, _position_repository, _position_service, _signal_processor
    global _maintenance_tasks, _maintenance_task, _safety_tasks, _safety_task
    
    logger.info("Application startup...")
    
    # Run the position race condition fix at startup
    logger.info("Running position race condition fix at startup...")
    try:
        await fix_position_race()
        logger.info("Position race condition fix completed")
    except Exception as e:
        logger.error(f"Error running position race condition fix: {str(e)}", exc_info=True)
    
    # --- Initialize adapter using config ---
    logger.info("Initializing Exchange Adapter...")
    try:
        adapter_settings = get_adapter_settings() # Get settings for default adapter
        adapter_id = adapter_settings.get('id', 'binance_spot') # Get adapter id or default
        testnet_mode = adapter_settings.get('testnet', False) # Default to False (live) if missing
        logger.info(f"Using adapter '{adapter_id}' in {'Testnet' if testnet_mode else 'Live'} mode.")
    
        _exchange_adapter = create_binance_adapter(
            # Mode will default to SPOT if not specified, or read from config if available
            api_key=BINANCE_SPOT_API_KEY,
            api_secret=BINANCE_SPOT_API_SECRET,
            testnet=testnet_mode 
        )
        # Assuming adapter has an async initialization method if needed
        # await _exchange_adapter.initialize()
        logger.info("Exchange Adapter created successfully.")
    except Exception as e:
        logger.error(f"Failed during Exchange Adapter setup: {e}", exc_info=True)
        _exchange_adapter = None # Ensure adapter is None if setup fails
        # Decide if the app should exit or continue
        # raise RuntimeError("Exchange Adapter initialization failed") from e
    # --- End Adapter Init ---
    
    # Initialize the position repository
    logger.info("Initializing File Position Repository...")
    _position_repository = create_file_position_repository()
    try:
        # Assuming repo has an async initialization method if needed
        # await _position_repository.initialize()
        logger.info("Position Repository created.") # Simplified log
    except Exception as e:
        logger.error(f"Failed during Position Repository setup: {e}", exc_info=True)
        # Decide if the app should exit or continue
        # raise RuntimeError("Position Repository initialization failed") from e
    
    # Initialize the position service
    logger.info("Initializing Position Service...")
    if _exchange_adapter and _position_repository:
        _position_service = PositionService(
            exchange_adapter=_exchange_adapter,
            position_repository=_position_repository
        )
        logger.info("Position Service initialized successfully.")
    else:
         logger.error("Cannot initialize Position Service due to missing adapter or repository.")
         # raise RuntimeError("Position Service initialization failed")
    
    # Initialize the signal processor
    logger.info("Initializing Signal Processor with dynamic setting access")
    if _position_service and _exchange_adapter:
        _signal_processor = SignalProcessor( # Create the instance
            position_service=_position_service,
            exchange_adapter=_exchange_adapter,
            # Pass default configs explicitly
            default_tp_config_3=DEFAULT_TP_PERCENTAGES_3,
            default_tp_config_4=DEFAULT_TP_PERCENTAGES_4,
            default_trade_amount=DEFAULT_TRADE_AMOUNT
            # Direction controls are now fetched dynamically
        )
        # Store the instance on app.state
        app.state.signal_processor = _signal_processor
        logger.info("Signal Processor initialized successfully.")
    else:
        logger.error("Cannot initialize Signal Processor due to missing position service or adapter.")
        # raise RuntimeError("Signal Processor initialization failed")
    
    # Initialize maintenance tasks
    logger.info("Initializing Maintenance Tasks...")
    if _position_repository:
        _maintenance_tasks = create_maintenance_tasks(_position_repository)
        
        # Run clean_closed_positions once at startup to fix any existing issues
        try:
            cleaned_count = await _maintenance_tasks.clean_closed_positions()
            if cleaned_count > 0:
                logger.info(f"Startup maintenance: Moved {cleaned_count} closed positions")
            else:
                logger.info("Startup maintenance: No closed positions needed to be moved")
        except Exception as e:
            logger.error(f"Error in startup maintenance: {str(e)}", exc_info=True)
        
        # Start scheduled maintenance tasks (runs every hour)
        try:
            logger.info("Starting scheduled maintenance tasks...")
            _maintenance_task = await _maintenance_tasks.start_scheduled_tasks(3600)
            if _maintenance_task and not _maintenance_task.done():
                logger.info(f"Maintenance tasks scheduled to run hourly - task is running (ID: {id(_maintenance_task)})")
            else:
                logger.error("Failed to start maintenance tasks - task is not running")
        except Exception as e:
            logger.error(f"Error starting maintenance tasks: {str(e)}", exc_info=True)
    else:
        logger.error("Cannot initialize Maintenance Tasks due to missing position repository")
    
    # Initialize safety tasks
    logger.info("Initializing Safety Tasks...")
    if _position_service and _exchange_adapter:
        _safety_tasks = create_safety_tasks(_position_service, _exchange_adapter)
        
        # Start scheduled safety tasks
        try:
            logger.info(f"Starting scheduled safety tasks (interval: {SAFETY_MEASURES_INTERVAL} seconds)...")
            _safety_task = await _safety_tasks.start_scheduled_tasks(SAFETY_MEASURES_INTERVAL)
            if _safety_task and not _safety_task.done():
                logger.info(f"Safety tasks scheduled - task is running (ID: {id(_safety_task)})")
            else:
                logger.error("Failed to start safety tasks - task is not running")
        except Exception as e:
            logger.error(f"Error starting safety tasks: {str(e)}", exc_info=True)
    else:
        logger.error("Cannot initialize Safety Tasks due to missing services")


# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on application shutdown."""
    global _maintenance_task, _safety_task, _position_service
    
    logger.info("Application shutting down...")
    
    # Close all open positions if configured to do so
    if _position_service:
        try:
            logger.info("Checking if positions need to be closed on shutdown...")
            shutdown_results = await close_all_positions_on_shutdown(_position_service)
            
            if shutdown_results.get("skipped", False):
                logger.info("Skipped closing positions on shutdown (disabled in config).")
            elif shutdown_results.get("closed_positions", 0) > 0:
                logger.info(f"Successfully closed {shutdown_results['closed_positions']} positions on shutdown.")
                if shutdown_results.get("errors", []):
                    logger.warning(f"Encountered {len(shutdown_results['errors'])} errors during position closure.")
        except Exception as e:
            logger.error(f"Error closing positions on shutdown: {str(e)}", exc_info=True)
    
    # Cancel maintenance tasks
    if _maintenance_task:
        logger.info("Stopping maintenance tasks...")
        _maintenance_task.cancel()
        try:
            await _maintenance_task
        except asyncio.CancelledError:
            logger.info("Maintenance tasks stopped successfully")
    
    # Cancel safety tasks
    if _safety_task:
        logger.info("Stopping safety tasks...")
        _safety_task.cancel()
        try:
            await _safety_task
        except asyncio.CancelledError:
            logger.info("Safety tasks stopped successfully")
    
    # Close other resources
    if _exchange_adapter and hasattr(_exchange_adapter, 'close'):
        try:
            await _exchange_adapter.close() # Assuming adapter has a close method
            logger.info("Exchange Adapter closed.")
        except Exception as e:
            logger.error(f"Error closing Exchange Adapter: {e}", exc_info=True)
    
    # Add cleanup for other resources if needed
    logger.info("Application shutdown complete.")


# Include routers
app.include_router(webhook_router, prefix="/api") # Add prefix for clarity
app.include_router(settings_router) # No additional prefix needed as it already has /api/settings
app.include_router(settings_web_ui_router) # Add the settings web UI router


# Exception handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions with proper logging."""
    logger.error(f"HTTP {exc.status_code} error occurred", extra={
        'status_code': exc.status_code,
        'detail': exc.detail,
        'path': request.url.path
    })
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle request validation errors with proper logging."""
    logger.error("Request validation error", extra={
        'errors': exc.errors(),
        'body': await request.body(),
        'path': request.url.path
    })
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


# Define telegram bot runner outside of start_telegram_bot function to avoid pickling issues
async def _telegram_bot_async_runner():
    """
    Async function to start and run the Telegram bot.
    """
    # --- Start: Added Service Initialization for Telegram Process ---
    tg_logger = logging.getLogger("telegram") # Use the logger defined later
    position_service_instance = None
    exchange_adapter_instance = None
    signal_processor_instance = None

    try:
        tg_logger.info("Initializing services for Telegram process...")
        # --- Initialize adapter using config for Telegram ---
        try:
            tg_adapter_settings = get_adapter_settings() # Get settings for default adapter
            tg_adapter_id = tg_adapter_settings.get('id', 'binance_spot')
            tg_testnet_mode = tg_adapter_settings.get('testnet', False) # Default to False (live)
            tg_logger.info(f"Telegram process using adapter '{tg_adapter_id}' in {'Testnet' if tg_testnet_mode else 'Live'} mode.")
            
            exchange_adapter_instance = create_binance_adapter(
                api_key=BINANCE_SPOT_API_KEY,
                api_secret=BINANCE_SPOT_API_SECRET,
                testnet=tg_testnet_mode
            )
            # Assuming adapter has an async initialization method if needed
            # await exchange_adapter_instance.initialize()
            tg_logger.info("Exchange Adapter initialized successfully for Telegram.")
        except Exception as e:
            tg_logger.error(f"Failed during Exchange Adapter setup for Telegram: {e}", exc_info=True)
            exchange_adapter_instance = None # Ensure adapter is None if setup fails
            # Optionally, re-raise or handle the error to prevent starting Telegram bot without an adapter
            # raise RuntimeError("Telegram Exchange Adapter initialization failed") from e
        # --- End Adapter Init for Telegram ---

        # Check if adapter initialization succeeded before proceeding
        if not exchange_adapter_instance:
             tg_logger.error("Cannot proceed with Telegram service initialization, adapter setup failed.")
             return

        # Initialize Position Repository for Telegram
        position_repository_instance = create_file_position_repository()
        tg_logger.info("Position Repository initialized for Telegram.")

        # Initialize Position Service for Telegram
        position_service_instance = PositionService(
            exchange_adapter=exchange_adapter_instance,
            position_repository=position_repository_instance
        )
        tg_logger.info("Position Service initialized for Telegram.")

        # Initialize Signal Processor for Telegram (optional, if needed by commands)
        # --- Get direction settings for Telegram Signal Processor ---
        tg_adapter_settings = get_adapter_settings()
        tg_directions_config = tg_adapter_settings.get('directions', {})
        tg_allow_long = tg_directions_config.get('allow_long', True)
        tg_allow_short = tg_directions_config.get('allow_short', True)
        tg_logger.info(f"Telegram Signal Processor will use dynamic settings (current allow_long: {tg_allow_long}, allow_short: {tg_allow_short})")
        # --- End direction settings ---
        
        signal_processor_instance = SignalProcessor( 
            position_service=position_service_instance,
            exchange_adapter=exchange_adapter_instance,
            default_tp_config_3=DEFAULT_TP_PERCENTAGES_3,
            default_tp_config_4=DEFAULT_TP_PERCENTAGES_4,
            default_trade_amount=DEFAULT_TRADE_AMOUNT
            # Direction controls are now fetched dynamically
        )
        tg_logger.info("Signal Processor initialized for Telegram.")

        # Set the services for the Telegram command handlers
        from src.telegram.telegram_commands import set_services
        set_services(
            position_service=position_service_instance,
            exchange_adapter=exchange_adapter_instance,
            signal_processor=signal_processor_instance
        )
        tg_logger.info("Services injected into Telegram command handlers.")

    except Exception as init_err:
        tg_logger.error(f"Failed to initialize services for Telegram process: {init_err}", exc_info=True)
        # Exit if essential services failed
        return
    # --- End: Added Service Initialization ---

    try:
        # Set up separate logger for the Telegram process with output to a dedicated file
        # tg_logger = logging.getLogger("telegram") # Logger is already defined above
        tg_logger.setLevel(logging.INFO)  # Changed from DEBUG to INFO
        
        # Add a file handler specifically for Telegram logs
        os.makedirs('logs', exist_ok=True)
        file_handler = logging.FileHandler('logs/telegram_debug.log')
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        # Avoid adding handlers multiple times if process restarts
        if not any(isinstance(h, logging.FileHandler) and h.baseFilename.endswith('telegram_debug.log') for h in tg_logger.handlers):
            tg_logger.addHandler(file_handler)
        
        # Also log to console
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        if not any(isinstance(h, logging.StreamHandler) for h in tg_logger.handlers):
             tg_logger.addHandler(console_handler)
        
        # Set httpx logger level to WARNING for this process
        logging.getLogger("httpx").setLevel(logging.WARNING)
        
        tg_logger.info("Initializing Telegram bot...")
        tg_logger.info(f"TELEGRAM_SECRET available: {bool(TELEGRAM_SECRET)}")
        tg_logger.info(f"APPROVED_CHAT_IDS from env: {os.getenv('APPROVED_CHAT_IDS')}")

        # Import here to avoid circular imports
        from src.telegram.telegram_bot import get_telegram_bot
        from src.telegram.telegram_users import CHAT_ID_TO_USE, get_all_users, get_admin_users
        
        # Log the chat ID we'll be using
        tg_logger.info(f"Will use chat ID: {CHAT_ID_TO_USE}")
        
        # Get the bot instance
        tg_logger.info("Getting Telegram bot instance...")
        bot = get_telegram_bot()
        
        # Verify that authorized users are configured
        tg_logger.info(f"Authorized users: {get_all_users()}")
        tg_logger.info(f"Admin users: {get_admin_users()}")
        
        # Start the bot
        tg_logger.info("Starting Telegram bot...")
        await bot.start()
        tg_logger.info("Telegram bot started successfully")
        
        # Keep the bot running until manually stopped
        tg_logger.info("Telegram bot is running. Press Ctrl+C to stop.")
        
        # Run indefinitely
        while True:
            await asyncio.sleep(3600)  # Sleep for an hour at a time
    except Exception as e:
        tg_logger.error(f"Error in Telegram bot: {e}", exc_info=True)
    finally:
        # Stop the bot gracefully
        try:
            if 'bot' in locals():
                tg_logger.info("Stopping Telegram bot...")
                await bot.stop()
        except Exception as e:
            tg_logger.error(f"Error stopping Telegram bot: {e}", exc_info=True)
        
        tg_logger.info("Telegram bot process finished")


def run_telegram_bot():
    """
    Function that runs in a separate process to start the Telegram bot.
    Uses asyncio to run the async function.
    """
    asyncio.run(_telegram_bot_async_runner())


def start_telegram_bot():
    """
    Start the Telegram bot in a separate process.
    """
    if not TELEGRAM_SECRET:
        logger.warning("Telegram bot token not set. Skipping Telegram bot startup.")
        return
    
    # --- REMOVED: Service injection moved to the child process ---
    # from src.telegram.telegram_commands import set_services
    # set_services(
    #     position_service=_position_service,
    #     exchange_adapter=_exchange_adapter,
    #     signal_processor=_signal_processor
    # )
    # ------------------------------------------------------------
    
    # Start in a separate process using the globally defined function
    logger.info("Starting Telegram bot in a separate process")
    process = multiprocessing.Process(target=run_telegram_bot)
    process.daemon = True  # Process will terminate when main process exits
    process.start()
    logger.info(f"Telegram bot process started (PID: {process.pid})")


def run_server():
    """Run the FastAPI server using Uvicorn."""
    logger.info(f"Starting server on {HOST}:{PORT}")
    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="info")
    server = uvicorn.Server(config)

    # Use asyncio.run to run the server
    asyncio.run(server.serve())


def main():
    """Main entry point for the application."""
    # Start the Telegram bot in a separate process
    logger.info("Starting application...")
    
    # Start the Telegram bot first
    start_telegram_bot()
    
    # Then start the API server
    run_server()
    
    logger.info("Application shutdown sequence initiated...") # Log when run_server returns


if __name__ == "__main__":
    # This block runs when the script is executed directly.
    main()
