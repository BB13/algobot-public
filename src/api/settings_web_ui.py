"""
Web UI for configuration management.

This module provides a simple HTML-based interface for viewing and updating settings.
It's designed to be used locally on the server for security reasons.
"""
import os
import secrets
import pandas as pd
import json # To serialize data for JavaScript
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Request, Depends, HTTPException, Form, Cookie, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.status import HTTP_401_UNAUTHORIZED
from fastapi.staticfiles import StaticFiles

from src.core.user_settings import get_settings
from src.api.settings_api import _update_config_value, _save_config_to_file

# Create router
router = APIRouter(prefix="/algobot-hub", tags=["Settings UI"])

# Configure basic auth
security = HTTPBasic()

# Set up templates directory - Point to the new frontend directory
templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")

# Create templates
templates = Jinja2Templates(directory=templates_dir)

# Mount static files directory
static_dir = os.path.join(templates_dir, "static")

# Mount static files - REMOVED FROM ROUTER
# router.mount("/static", StaticFiles(directory=static_dir), name="static_frontend")

# Define the path to the CSV file relative to the project root
# Assuming main.py is in the root, and the data folder is in src
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "src", "data")
TRADE_OUTCOMES_CSV = os.path.join(DATA_DIR, "trade_outcomes.csv")

# Generate a secure session token (regenerated on restart for extra security)
SESSION_TOKEN = secrets.token_hex(32)

# Environment variables for authentication
ADMIN_USERNAME = os.getenv("SETTINGS_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("SETTINGS_ADMIN_PASSWORD")  # Must be set in environment

# Authentication helper functions
def verify_password(username: str, password: str) -> bool:
    """Verify username and password."""
    if not ADMIN_PASSWORD:
        # If no password is set, deny all access for security
        print("WARNING: SETTINGS_ADMIN_PASSWORD environment variable is not set. Settings UI access disabled.")
        return False
    
    correct_username = secrets.compare_digest(username, ADMIN_USERNAME)
    correct_password = secrets.compare_digest(password, ADMIN_PASSWORD)
    return correct_username and correct_password

def get_current_user(credentials: HTTPBasicCredentials = Depends(security)):
    """Validate user credentials."""
    authenticated = verify_password(credentials.username, credentials.password)
    if not authenticated:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic realm=\"AlgoBot Settings Access\""},
        )
    return credentials.username

def verify_session(session: Optional[str] = Cookie(None)):
    """Verify session cookie."""
    if not session or session != SESSION_TOKEN:
        # Instead of raising HTTPException directly, redirect to login
        raise HTTPException(status_code=307, detail="Not authenticated", headers={"Location": "/algobot-hub/login"}) # Use 307 for temporary redirect
    return session

# Get nested value from dictionary using dot notation
def get_nested_value(d: Dict[str, Any], path: str) -> Any:
    """Get a nested value from a dictionary using dot notation path."""
    keys = path.split('.')
    value = d
    for key in keys:
        # Handle cases where intermediate keys might be missing or not dicts
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
             value = value[int(key)] # Basic list index support if needed
        else:
            # Path segment not found or type mismatch
            print(f"Warning: Could not find path segment '{key}' in path '{path}' within {value}")
            return None # Or raise an error, depending on desired behavior
    return value

# Helper to check if a setting path corresponds to a boolean/checkbox type
# This might need refinement based on actual setting structure/types
def is_boolean_setting(settings_dict: Dict[str, Any], path: str) -> bool:
    """ Check if the setting at the given path is expected to be a boolean. """
    value = get_nested_value(settings_dict, path)
    return isinstance(value, bool)

# Routes
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, message: str = None, message_type: str = None):
    """Render the login page."""
    # Check if ADMIN_PASSWORD is set, otherwise show an error message maybe?
    if not ADMIN_PASSWORD:
         return templates.TemplateResponse(
            "login.html", # Use the new template name
            {
                "request": request,
                "message": "Settings UI is disabled. Administrator password not configured.",
                "message_type": "error",
                "active_page": "login"
            }
        )
    return templates.TemplateResponse(
        "login.html", # Use the new template name
        {
            "request": request,
            "message": message,
            "message_type": message_type,
            "active_page": "login"
        }
    )

@router.get("/", response_class=HTMLResponse)
async def settings_ui(
    request: Request,
    message: str = None,
    message_type: str = None,
    session: str = Depends(verify_session) # Use dependency to enforce auth
):
    """Render the main settings UI page."""
    # verify_session will raise HTTPException if not authenticated, redirecting to login
    settings = get_settings()
    response = templates.TemplateResponse(
        "settings.html", # Use the new template name
        {
            "request": request,
            "settings": settings.config, # Pass the raw config dict
            "message": message,
            "message_type": message_type,
            "active_page": "settings" # For navbar highlighting
        }
    )
    # Refresh cookie on access
    response.set_cookie(key="session", value=session, httponly=True, secure=False, samesite="lax") # Use lax/secure=False for http dev
    return response

@router.get("/webhook", response_class=HTMLResponse)
async def webhook_generator_page(
    request: Request,
    session: str = Depends(verify_session) # Protect this page too
):
    """Render the webhook generator page."""
    response = templates.TemplateResponse(
        "webhook.html", # Use the new template name
        {
            "request": request,
            "active_page": "webhook" # For navbar highlighting
        }
    )
    # Refresh cookie on access
    response.set_cookie(key="session", value=session, httponly=True, secure=False, samesite="lax") # Use lax/secure=False for http dev
    return response

@router.get("/charts", response_class=HTMLResponse)
async def charts_page(
    request: Request,
    session: str = Depends(verify_session) # Protect this page
):
    """Render the charts page."""
    try:
        # Read the CSV data
        if not os.path.exists(TRADE_OUTCOMES_CSV):
             raise FileNotFoundError(f"Trade outcomes file not found at {TRADE_OUTCOMES_CSV}")

        df = pd.read_csv(TRADE_OUTCOMES_CSV)

        # Convert timestamp to datetime objects for potential filtering/analysis
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        # Get unique values for dropdowns
        unique_strategies = sorted(df['bot_strategy'].astype(str).unique().tolist())
        unique_settings = sorted(df['bot_settings'].astype(str).unique().tolist())
        unique_timeframes = sorted(df['timeframe'].astype(str).unique().tolist())
        unique_assets = sorted(df['asset'].astype(str).unique().tolist())

        # Prepare initial data or context for the template
        context = {
            "request": request,
            "active_page": "charts",
            "unique_strategies": unique_strategies,
            "unique_settings": unique_settings,
            "unique_timeframes": unique_timeframes,
            "unique_assets": unique_assets,
            "message": None, # Placeholder for potential messages
            "message_type": None
        }

        response = templates.TemplateResponse("charts.html", context)
        # Refresh cookie on access
        response.set_cookie(key="session", value=session, httponly=True, secure=False, samesite="lax")
        return response

    except FileNotFoundError as e:
         # Handle case where CSV doesn't exist
         # You might want a dedicated error template
         print(f"Error rendering charts page: {e}") # Log the error
         # Render a simple error message or redirect
         # For now, let's re-raise as an HTTPException handled by FastAPI
         raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        # Handle other potential errors (e.g., pandas read error)
        print(f"Unexpected error rendering charts page: {e}") # Log the error
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

# --- Add a new route to fetch filtered chart data ---
@router.get("/chart-data")
async def get_chart_data(
    request: Request,
    bot_strategy: Optional[str] = None,
    bot_settings: Optional[str] = None,
    timeframe: Optional[str] = None,
    asset: Optional[str] = None,
    session: str = Depends(verify_session) # Protect endpoint
):
    """API endpoint to fetch filtered trade outcome data for charts."""
    try:
        if not os.path.exists(TRADE_OUTCOMES_CSV):
            raise HTTPException(status_code=404, detail=f"Trade outcomes file not found: {TRADE_OUTCOMES_CSV}")

        df = pd.read_csv(TRADE_OUTCOMES_CSV)
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        # Filter based on query parameters
        if bot_strategy and bot_strategy != "all":
            df = df[df['bot_strategy'].astype(str) == bot_strategy]
        if bot_settings and bot_settings != "all":
            df = df[df['bot_settings'].astype(str) == bot_settings]
        if timeframe and timeframe != "all":
            # Ensure comparison is type-consistent if timeframe can be numeric
            df = df[df['timeframe'].astype(str) == str(timeframe)]
        if asset and asset != "all":
            df = df[df['asset'].astype(str) == asset]

        # Select relevant columns for charting (e.g., timestamp and profit_percentage)
        # Sort by timestamp for time-series plotting
        # Convert NaN profit percentages to 0 or handle appropriately
        df['profit_percentage'] = pd.to_numeric(df['profit_percentage'].str.replace('%', ''), errors='coerce').fillna(0)
        chart_data = df[['timestamp', 'profit_percentage']].sort_values(by='timestamp').to_dict('records')

        # Convert Timestamp objects to ISO 8601 strings for JSON serialization
        for record in chart_data:
            record['timestamp'] = record['timestamp'].isoformat()

        return {"success": True, "data": chart_data}

    except FileNotFoundError as e:
         raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        # Log the error details
        print(f"Error fetching chart data: {str(e)}") # Use logger in real app
        raise HTTPException(status_code=500, detail=f"An error occurred while fetching chart data: {str(e)}")

@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    """Process login form."""
    if verify_password(username, password):
        # Login success - redirect to the main settings page
        response = RedirectResponse(url="/algobot-hub/", status_code=303)
        # Set secure=False and samesite='lax' for local HTTP development if needed
        response.set_cookie(key="session", value=SESSION_TOKEN, httponly=True, secure=False, samesite="lax")
        return response
    else:
        # Login failed - re-render login page with error
        return templates.TemplateResponse(
            "login.html", # Use the new template name
            {
                "request": request,
                "message": "Invalid username or password",
                "message_type": "error",
                "active_page": "login"
            }
        )

@router.post("/update")
async def update_setting(
    request: Request,
    path: str = Form(...),
    session: str = Depends(verify_session) # Protect endpoint
):
    """Update a setting from the web UI."""
    form_data = await request.form()
    value = form_data.get("value") # Use .get() for optional fields

    # Get current settings to check type
    settings = get_settings()
    is_bool = is_boolean_setting(settings.config, path)

    # Handle boolean/checkbox fields specifically
    if is_bool:
        # Checkboxes/switches only send 'value' when checked.
        # If 'value' is present (and usually 'true' or 'on'), it's True.
        # If 'value' is *not* present in the form data, it means the checkbox was unchecked, so False.
        converted_value = value is not None # True if 'value' key exists in form, False otherwise
    else:
        # Handle non-boolean types (number, string, select)
        if value is None:
             converted_value = "" # Or handle as error? Assume empty string for now.
        else:
            try:
                # Try converting to number if appropriate
                if "." in value:
                    converted_value = float(value)
                else:
                    converted_value = int(value)
            except ValueError:
                # Keep as string if not a number or empty
                converted_value = value

    # Convert path from dot notation to list
    path_list = path.split(".")

    # Update logic using settings_api helpers
    try:
        # Update the config in memory
        success = _update_config_value(settings.config, path_list, converted_value)
        if not success:
            # Return error as a redirect with query parameters
            message = f"Failed to update setting at path {path}. Path might be invalid."
            message_type = "error"
            return RedirectResponse(
                url=f"/algobot-hub/?message={message}&message_type={message_type}",
                status_code=303
            )

        # Save to file
        _save_config_to_file(settings.config, settings.config_path)

        # Redirect to the main page with a success message
        display_value = converted_value if not isinstance(converted_value, str) else f'"{converted_value}"' # Add quotes for strings
        message = f"Setting '{path}' updated to {display_value}."
        message_type = "success"
        redirect_url = f"/algobot-hub/?message={message}&message_type={message_type}"
        response = RedirectResponse(url=redirect_url, status_code=303)
        # Refresh cookie
        response.set_cookie(key="session", value=session, httponly=True, secure=False, samesite="lax")
        return response

    except Exception as e:
        # Return error as a redirect with query parameters
        message = f"Error updating setting '{path}': {str(e)}"
        message_type = "error"
        return RedirectResponse(
            url=f"/algobot-hub/?message={message}&message_type={message_type}",
            status_code=303
        )

@router.post("/logout")
async def logout(request: Request):
    """Logout and invalidate session."""
    # Redirect to login page after logout
    response = RedirectResponse(url="/algobot-hub/login?message=Logged out successfully&message_type=success", status_code=303)
    response.delete_cookie(key="session")
    return response 