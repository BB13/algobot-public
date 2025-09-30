"""
chart_capture.py

Handles capturing screenshots from TradingView charts.
"""
import os
import logging
from playwright.async_api import async_playwright
from typing import Optional
import asyncio
import uuid
import tempfile

from ..core.config import (
    ENABLE_CHART_SNAPSHOTS,
)

logger = logging.getLogger(__name__)


async def capture_chart_screenshot(
    target_url: str, # Now required
    output_path: Optional[str] = None,
    # Removed asset_symbol and timeframe parameters
) -> Optional[str]:
    """
    Captures a screenshot of a specific URL (e.g., MultiCoinCharts).

    Args:
        target_url: Specific URL to capture.
        output_path: Optional path to save the screenshot. If None, uses a temporary file.

    Returns:
        The path to the saved screenshot file, or None if capture failed or is disabled.
    """
    if not ENABLE_CHART_SNAPSHOTS:
        logger.info("Chart snapshots are disabled.")
        return None

    if not target_url:
         logger.error("Cannot capture screenshot: target_url must be provided.")
         return None

    # Define filename base - always using target_url now
    url_to_capture = target_url
    filename_base = "multichart_screenshot" 
    logger.info(f"Attempting to capture screenshot from specific URL: {url_to_capture}")

    # --- Removed the fallback logic block that used asset_symbol and TRADINGVIEW_CHART_URLS --- 

    try:
        async with async_playwright() as p:
            logger.debug("Initializing Playwright for chart capture")
            
            try:
                # Launch with more permissive options to handle server environments
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        '--disable-gpu',
                        '--no-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-setuid-sandbox',
                    ]
                )
                logger.debug("Chromium browser launched")
            except Exception as browser_error:
                logger.error(f"Failed to launch browser: {browser_error}", exc_info=True)
                return None
                
            try:
                # Use more permissive context settings
                context = await browser.new_context(ignore_https_errors=True)
                page = await context.new_page()
                logger.debug("Browser page created")
            except Exception as page_error:
                logger.error(f"Failed to create browser page: {page_error}", exc_info=True)
                await browser.close()
                return None
            
            # Define screenshot path earlier
            temp_dir = None
            if output_path is None:
                temp_dir = tempfile.mkdtemp() 
                # Make filename unique
                unique_id = str(uuid.uuid4())[:8] 
                output_path = os.path.join(temp_dir, f"{filename_base}_{unique_id}.png")
            
            # Navigate to the URL with better error handling
            try:
                logger.info(f"Navigating to URL: {url_to_capture}")
                
                # IMPORTANT CHANGE: Use 'commit' instead of 'load' - this will complete much faster
                # and doesn't wait for all resources to finish loading
                response = await page.goto(url_to_capture, timeout=30000, wait_until="commit")
                
                if response:
                    logger.info(f"Initial navigation committed with status: {response.status}")
                    
                    # Give the page a moment to render more content after initial commit
                    logger.info("Waiting 8 seconds for additional content to load...")
                    await asyncio.sleep(8)
                else:
                    logger.warning("No response received from navigation, but continuing...")
            except Exception as navigation_error:
                logger.error(f"Failed to navigate to URL {url_to_capture}: {navigation_error}", exc_info=True)
                await browser.close()
                # Clean up the temp directory if it was created
                if temp_dir and os.path.exists(temp_dir):
                    try:
                        import shutil
                        shutil.rmtree(temp_dir)
                    except Exception as cleanup_err:
                        logger.warning(f"Error cleaning temp dir after navigation failure: {cleanup_err}")
                return None

            # Take the screenshot with better error handling
            try:
                logger.info(f"Attempting to save screenshot to: {output_path}")
                # Take a full page screenshot
                await page.screenshot(path=output_path, full_page=True)
                logger.info("Screenshot taken successfully")
            except Exception as screenshot_error:
                logger.error(f"Error taking screenshot: {screenshot_error}", exc_info=True)
                await browser.close()
                # Clean up the temp directory
                if temp_dir and os.path.exists(temp_dir):
                    try:
                        import shutil
                        shutil.rmtree(temp_dir)
                    except Exception as cleanup_err:
                        logger.warning(f"Error cleaning temp dir after screenshot failure: {cleanup_err}")
                return None
                
            # Close the browser
            try:
                await browser.close()
                logger.debug("Browser closed successfully")
            except Exception as close_error:
                logger.warning(f"Error closing browser: {close_error}")

            # Check if the file was actually created
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                 logger.info(f"Screenshot saved successfully to {output_path}")
                 return output_path
            else:
                 logger.error(f"Screenshot file was not created or is empty at {output_path}")
                 # Clean up temp dir if created
                 if temp_dir and os.path.exists(temp_dir):
                     try:
                         os.rmdir(temp_dir) # Only removes if empty
                     except OSError:
                         pass # Ignore if not empty
                 return None

    except Exception as e:
        # Log the specific exception
        logger.error(f"Error capturing screenshot from {url_to_capture}: {str(e)}", exc_info=True)
        # Clean up temp dir if created and exception occurred
        if 'temp_dir' in locals() and temp_dir and os.path.exists(temp_dir):
            import shutil
            try:
                shutil.rmtree(temp_dir) # Force remove directory and contents
                logger.info(f"Cleaned up temporary directory {temp_dir} after error.")
            except Exception as cleanup_err:
                logger.warning(f"Error cleaning up temporary directory {temp_dir}: {cleanup_err}")
        return None

# Example usage (for testing)
# async def main():
#     import asyncio
#     path = await capture_chart_screenshot("BTCUSDT")
#     if path:
#         print(f"Screenshot saved at: {path}")
#     else:
#         print("Failed to capture screenshot.")

# if __name__ == "__main__":
#     asyncio.run(main()) 