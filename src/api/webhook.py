"""
Webhook API for handling trading signals.
"""
import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Optional
from urllib.parse import unquote

from fastapi import APIRouter, Request, HTTPException, Depends, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ValidationError, validator, Field

# Removed Asset import as it's not directly used here
from ..core.config import WEBHOOK_API_KEY
from ..services.signal_processor import SignalProcessor
from ..core.logging_config import log_incoming_signal


logger = logging.getLogger(__name__)

# --- Security Setup ---
API_KEY_HEADER_WEBHOOK = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_webhook_security(request: Request, api_key_header: Optional[str] = Security(API_KEY_HEADER_WEBHOOK)):
    """Dependency to verify webhook security via header or body key."""
    if not WEBHOOK_API_KEY:
        logger.critical("WEBHOOK_API_KEY is not set in the environment. Webhook endpoint is unprotected.")
        # Consider raising HTTPException here if you want to block access when key is missing
        # raise HTTPException(status_code=503, detail="Server configuration error: Webhook Key not set")
        return # Allow access if key is not configured server-side, but log critical warning

    # 1. Check Header
    if api_key_header and api_key_header == WEBHOOK_API_KEY:
        logger.debug("Webhook authenticated via X-API-Key header.")
        return # Authenticated via header

    # 2. Check Body (Need to read body here if header failed)
    # Read body bytes ONCE
    body_bytes = await request.body()
    # Store the body back on the request state so the route handler can reuse it without reading again.
    # This avoids "body already consumed" errors.
    request.state.body_bytes = body_bytes

    if not body_bytes:
        logger.warning("Webhook security check: Empty body and no valid header key.")
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")

    # Parse body to check for 'security_key'
    try:
        signal_data = _parse_webhook_data(body_bytes)
        api_key_body = signal_data.get("security_key") # Use .get() to avoid KeyError

        if api_key_body and api_key_body == WEBHOOK_API_KEY:
            logger.debug("Webhook authenticated via 'security_key' in body.")
            return # Authenticated via body key
    except Exception as e:
        # Log parsing error during security check, but treat as auth failure
        logger.error(f"Error parsing webhook body during security check: {e}", exc_info=True)
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")

    # If neither header nor body key is valid
    logger.warning(f"Unauthorized webhook access attempt. Header: '{api_key_header}', Body Key Present: {api_key_body is not None}")
    raise HTTPException(status_code=401, detail="Invalid or missing API Key")

# --- End Security Setup ---

# --- Pydantic Model for Signal Validation ---
class SignalData(BaseModel):
    command: str
    asset: str
    interval: str # Or consider specific Enum/Literal if intervals are fixed
    bot: str

    # Optional fields
    price: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    altTP: Optional[str] = None # Keep as string for now, processor handles parsing
    maxTP: Optional[int] = Field(None, ge=1) # Ensure maxTP is at least 1 if provided
    # Removed botSettings as it's combined into 'bot' earlier

    # Add a validator for command to handle case and spacing consistency
    @validator('command', pre=True, always=True)
    def clean_command(cls, v):
        if isinstance(v, str):
            return v.upper().replace(" ", "") # Convert to uppercase and remove spaces
        return v

    @validator('asset', pre=True, always=True)
    def clean_asset(cls, v):
        if isinstance(v, str):
            return v.upper() # Ensure asset is uppercase
        return v

    @validator('amount', 'price', pre=True)
    def parse_decimal(cls, v):
        if v is None or v == '':
            return None
        try:
            return Decimal(str(v))
        except InvalidOperation:
            raise ValueError(f"Invalid decimal value: {v}")
        return v

    class Config:
        extra = 'allow' # Allow extra fields (like security_key before it's removed)
        anystr_strip_whitespace = True
# --- End Pydantic Model ---

# Create API router
router = APIRouter()

# --- Updated Signal processor dependency ---
def get_signal_processor(request: Request) -> SignalProcessor:
    """
    Dependency function to get the singleton SignalProcessor instance.
    Retrieves the instance stored in app.state.
    """
    try:
        # Access the instance from app.state via the request
        processor = request.app.state.signal_processor
        if processor is None:
            # This check might be redundant if startup guarantees initialization
            raise RuntimeError("Signal Processor not found in app state")
        return processor
    except (AttributeError, RuntimeError) as e:
        # If the processor isn't initialized or state doesn't exist
        logger.critical(f"Signal Processor dependency error: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="Service not ready: Signal Processor unavailable")


# --- Updated Webhook Endpoint ---
@router.post("/webhook", tags=["Webhook"])
async def webhook(
    request: Request,
    # Inject the SignalProcessor instance using the dependency function
    signal_processor: SignalProcessor = Depends(get_signal_processor),
    # Apply the security dependency
    _security_check = Depends(verify_webhook_security)
):
    """
    Handle incoming webhook requests containing trading signals.

    Supports multiple formats:
    1. URL-encoded form data: `command=LONG&asset=BTCUSDT&interval=4h&bot=optibot_A1&amt=1000`
    2. TradingView alerts: `command={{strategy.order.alert_message}},asset={{ticker}},price={{close}},interval={{interval}},bot=Scalp,botSettings=A1,altTP=30-50-100,maxTP=3,security_key=xxxxx`
    
    Authentication can be via:
    1. `X-API-KEY` header matching `WEBHOOK_API_KEY`, or
    2. `security_key` parameter in the body matching `WEBHOOK_API_KEY`
    """
    logger.info(f"Webhook handler entered. Request Headers: {request.headers}")
    
    try:
        # Retrieve the body bytes read by the security dependency
        body_bytes = getattr(request.state, 'body_bytes', None)
        if body_bytes is None:
            # Fallback in case dependency somehow didn't run or store the body
            logger.warning("Body bytes not found in request state, reading again.")
            body_bytes = await request.body()

        if not body_bytes:
             # This case should ideally be caught by the security dependency if header also failed
             raise HTTPException(status_code=400, detail="Request body cannot be empty")
        
        # Log the raw request body for debugging
        logger.info(f"Raw webhook body: {body_bytes.decode('utf-8', errors='replace')}")
        
        # Parse the signal data
        raw_signal_data = _parse_webhook_data(body_bytes)
        if not raw_signal_data:
            logger.error("Failed to parse signal data from request body")
            raise HTTPException(status_code=400, detail="Failed to parse signal data from request body")

        # --- Security key cleanup (key already validated by dependency) ---
        if "security_key" in raw_signal_data:
            del raw_signal_data["security_key"]
        # --- End security key cleanup ---

        # --- Handle botSettings pre-validation --- (Moved earlier)
        if "botSettings" in raw_signal_data:
            bot_strategy = raw_signal_data.get("bot", "")
            bot_settings = raw_signal_data.pop("botSettings")
            logger.info(f"Found botSettings parameter. Bot: '{bot_strategy}', Settings: '{bot_settings}'")
            raw_signal_data["bot"] = f"{bot_strategy}_{bot_settings}"
            logger.info(f"Combined bot parameters: '{raw_signal_data['bot']}'")
        # --- End Handle botSettings ---

        request_id = str(uuid.uuid4())
        logger.info(f"Attempting to validate signal [ID: {request_id}]: {raw_signal_data}")

        # --- Validate parsed data using Pydantic --- 
        try:
            validated_signal = SignalData(**raw_signal_data)
            # Convert validated model back to dict for processing
            signal_data_to_process = validated_signal.dict(exclude_unset=True)
            logger.info(f"Signal validation successful [ID: {request_id}]: {signal_data_to_process}")
        except ValidationError as e:
            logger.warning(f"Signal validation failed [ID: {request_id}]: {e.errors()}")
            # Return a 422 error with validation details
            raise HTTPException(
                status_code=422, 
                detail={"message": "Webhook validation error", "errors": e.errors()}
            )
        # --- End Pydantic Validation ---

        # Log the incoming signal to the order log (using validated data)
        log_incoming_signal(signal_data_to_process)

        # Process Signal using the SignalProcessor
        logger.info(f"Calling signal_processor.process_signal with validated data [ID: {request_id}]")
        
        try:
            result = await signal_processor.process_signal(signal_data_to_process)
            logger.info(f"Signal processing result [ID: {request_id}]: {result}")
        except ValueError as ve:
            # Log the specific validation error (likely from SignalProcessor now)
            logger.warning(f"Signal processing error [ID: {request_id}]: {ve}")
            return JSONResponse(
                content={"success": False, "message": str(ve)},
                status_code=400
            )

        # Determine appropriate status code based on result
        status_code = 200 if result.get("success", False) else 400
        return JSONResponse(content=result, status_code=status_code)

    except ValueError as ve:
        # Handle validation errors raised directly by SignalProcessor
        logger.warning(f"Signal validation error: {ve}")
        return JSONResponse(
            content={"success": False, "message": str(ve)},
            status_code=400
        )
    except HTTPException as http_exc:
         # Re-raise HTTP exceptions
         raise http_exc
    except Exception as e:
        # Catch any other unexpected errors during processing
        logger.error(f"Unexpected error processing signal: {e}", exc_info=True)
        return JSONResponse(
            content={"success": False, "message": "Internal server error during signal processing."},
            status_code=500
        )

# Add this to explicitly reject GET requests with a clear message
@router.get("/webhook", tags=["Webhook"])
async def webhook_get():
    """Block GET requests to the webhook endpoint."""
    logger.warning(f"Rejected GET request to webhook endpoint")
    raise HTTPException(status_code=405, detail="Method Not Allowed. Use POST for webhook requests.")

# Add a root path handler to provide guidance
@router.get("/", tags=["Info"])
async def root():
    """Provide information about the API."""
    return {
        "message": "Trading Bot API",
        "endpoints": {
            "webhook": "/webhook (POST only)"
        }
    }

# --- Updated Parsing Helper ---
def _parse_webhook_data(body_bytes: bytes) -> Dict[str, Any]:
    """
    Parse webhook data into a dictionary.
    Supports both comma-delimited and ampersand-delimited formats.
    
    Args:
        body_bytes: Raw request body as bytes
        
    Returns:
        Dictionary of parsed parameters
    """
    try:
        # Decode bytes to string
        body_str = body_bytes.decode('utf-8').strip()
        
        # Determine delimiter - prioritize comma (TradingView style)
        delimiter = "&"
        if "," in body_str:
            delimiter = ","
            logger.debug("Using comma as delimiter for parsing")
        
        # URL Decode the string (handles %20 etc.)
        decoded_str = unquote(body_str)
        
        # Split into key-value pairs
        params = decoded_str.split(delimiter)
        data_dict = {}
        
        for param in params:
            # Skip empty parameters
            if not param.strip():
                continue
                
            # Ensure there's an '=' sign and the key is not empty
            if '=' in param and param.split('=', 1)[0].strip():
                key, value = param.split('=', 1)
                key = key.strip()
                value = value.strip()
                
                # Handle TradingView placeholders in double brackets
                # If the value is empty or still contains placeholder brackets,
                # we'll keep it as is for now and let the SignalProcessor handle it
                if value and value.startswith('{{') and value.endswith('}}'):
                    logger.warning(f"Parameter '{key}' contains placeholder value: {value}")
                
                # Store values as strings; SignalProcessor handles type conversion
                data_dict[key] = value
            elif param.strip():
                logger.warning(f"Webhook parameter without value ignored: '{param.strip()}'")
        
        # Check if any data was actually parsed
        if not data_dict:
            logger.warning("Parsing webhook body resulted in empty data dictionary")
            return {}
        
        return data_dict
        
    except UnicodeDecodeError as ude:
        logger.error(f"Encoding error parsing webhook body: {ude}", exc_info=True)
        return {}
    except Exception as e:
        logger.error(f"Error parsing webhook body: {e}", exc_info=True)
        return {}
