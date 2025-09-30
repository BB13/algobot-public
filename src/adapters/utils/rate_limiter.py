"""
Rate limiting utility for API requests.

This module provides rate limiting capabilities for API requests, tracking
request timestamps per endpoint category and implementing exponential backoff
for rate limit errors.
"""
import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable, Any, List, Tuple

logger = logging.getLogger(__name__)

class RateLimiter:
    """
    Rate limiter for API requests.
    
    Manages request rates to avoid hitting API rate limits by tracking
    requests per endpoint/weight and implementing waiting mechanisms.
    
    Attributes:
        max_requests: Maximum number of requests per time window
        time_window: Time window in seconds
        request_history: Dictionary tracking request timestamps per endpoint
    """
    
    def __init__(
        self, 
        max_requests_per_minute: int = 1200,
        max_requests_per_second: int = 50,
        max_weight_per_minute: int = 6000
    ):
        """
        Initialize the rate limiter.
        
        Args:
            max_requests_per_minute: Maximum number of requests per minute
            max_requests_per_second: Maximum number of requests per second
            max_weight_per_minute: Maximum request weight per minute
        """
        self.max_requests_per_minute = max_requests_per_minute
        self.max_requests_per_second = max_requests_per_second
        self.max_weight_per_minute = max_weight_per_minute
        
        # Track request timestamps for different time windows
        self.request_history_second = []
        self.request_history_minute = []
        self.weight_history_minute = []
        
        # Per-endpoint tracking for more granular control
        self.endpoint_request_history: Dict[str, List[float]] = defaultdict(list)
        
        # Lock for thread safety
        self.lock = asyncio.Lock()
        
        # Track backoff state for rate limit errors
        self.backoff_until: Optional[datetime] = None
        self.backoff_multiplier = 1.0
        self.max_backoff = 60  # Maximum backoff in seconds
        
        logger.info(
            f"Rate limiter initialized with limits: "
            f"{max_requests_per_minute}/min, {max_requests_per_second}/sec, "
            f"{max_weight_per_minute} weight/min"
        )
    
    async def wait_if_needed(self, endpoint: Optional[str] = None, weight: int = 1) -> None:
        """
        Wait if necessary to comply with rate limits.
        
        Args:
            endpoint: Optional API endpoint identifier for more granular rate limiting
            weight: Request weight (some endpoints have higher weight)
        """
        async with self.lock:
            # Check if we're in a backoff period
            if self.backoff_until and datetime.now() < self.backoff_until:
                wait_time = (self.backoff_until - datetime.now()).total_seconds()
                logger.info(f"Rate limit backoff: waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
            
            # Clean up old timestamps
            current_time = time.time()
            self._clean_history(current_time)
            
            # Calculate waits for each constraint
            wait_times = [
                self._check_second_limit(current_time),
                self._check_minute_limit(current_time),
                self._check_weight_limit(current_time, weight)
            ]
            
            # If endpoint is provided, also check endpoint-specific limits
            if endpoint:
                wait_times.append(self._check_endpoint_limit(endpoint, current_time))
            
            # Get the max wait time needed
            wait_time = max(wait_times)
            
            if wait_time > 0:
                logger.debug(f"Rate limiting: waiting {wait_time:.2f}s before request")
                await asyncio.sleep(wait_time)
            
            # Record this request
            new_time = time.time()  # Get fresh timestamp after potential sleep
            self.request_history_second.append(new_time)
            self.request_history_minute.append(new_time)
            self.weight_history_minute.append((new_time, weight))
            
            if endpoint:
                self.endpoint_request_history[endpoint].append(new_time)
    
    def _clean_history(self, current_time: float) -> None:
        """
        Clean up old request timestamps.
        
        Args:
            current_time: Current timestamp
        """
        # Keep only requests in the last second
        self.request_history_second = [
            t for t in self.request_history_second 
            if current_time - t <= 1
        ]
        
        # Keep only requests in the last minute
        self.request_history_minute = [
            t for t in self.request_history_minute 
            if current_time - t <= 60
        ]
        
        # Keep only weights in the last minute
        self.weight_history_minute = [
            (t, w) for t, w in self.weight_history_minute 
            if current_time - t <= 60
        ]
        
        # Clean endpoint history
        for endpoint in list(self.endpoint_request_history.keys()):
            self.endpoint_request_history[endpoint] = [
                t for t in self.endpoint_request_history[endpoint]
                if current_time - t <= 60
            ]
            
            # Remove empty lists
            if not self.endpoint_request_history[endpoint]:
                del self.endpoint_request_history[endpoint]
    
    def _check_second_limit(self, current_time: float) -> float:
        """
        Check if we're approaching the per-second limit.
        
        Args:
            current_time: Current timestamp
            
        Returns:
            Time to wait in seconds, or 0 if no wait needed
        """
        # If we're already at the limit, calculate wait time for oldest to expire
        if len(self.request_history_second) >= self.max_requests_per_second:
            # Sort timestamps and find the oldest that would need to expire
            oldest_to_expire = sorted(self.request_history_second)[
                -(self.max_requests_per_second) + 1
            ]
            return max(0, (oldest_to_expire + 1) - current_time)
        return 0
    
    def _check_minute_limit(self, current_time: float) -> float:
        """
        Check if we're approaching the per-minute limit.
        
        Args:
            current_time: Current timestamp
            
        Returns:
            Time to wait in seconds, or 0 if no wait needed
        """
        # Use 95% of the limit to be safe
        safe_limit = int(self.max_requests_per_minute * 0.95)
        
        # If we're already near the limit, calculate wait time for oldest to expire
        if len(self.request_history_minute) >= safe_limit:
            # Sort timestamps and find the oldest that would need to expire
            oldest_to_expire = sorted(self.request_history_minute)[
                -(safe_limit) + 1
            ]
            return max(0, (oldest_to_expire + 60) - current_time)
        return 0
    
    def _check_weight_limit(self, current_time: float, weight: int) -> float:
        """
        Check if we're approaching the weight limit.
        
        Args:
            current_time: Current timestamp
            weight: Request weight
            
        Returns:
            Time to wait in seconds, or 0 if no wait needed
        """
        # Calculate total weight in the last minute
        total_weight = sum(w for _, w in self.weight_history_minute)
        
        # Use 95% of the limit to be safe
        safe_limit = int(self.max_weight_per_minute * 0.95)
        
        # If adding this request would exceed the limit
        if total_weight + weight > safe_limit:
            # Calculate time needed for enough weight to expire
            need_to_expire = total_weight + weight - safe_limit
            
            # Sort by time and find the oldest requests we need to wait for
            sorted_weights = sorted(self.weight_history_minute, key=lambda x: x[0])
            
            weight_so_far = 0
            for timestamp, w in sorted_weights:
                weight_so_far += w
                if weight_so_far >= need_to_expire:
                    # Found the timestamp that needs to expire
                    return max(0, (timestamp + 60) - current_time)
            
            # Fallback - wait a full minute
            return 60
        
        return 0
    
    def _check_endpoint_limit(self, endpoint: str, current_time: float) -> float:
        """
        Check endpoint-specific rate limits.
        
        Args:
            endpoint: API endpoint identifier
            current_time: Current timestamp
            
        Returns:
            Time to wait in seconds, or 0 if no wait needed
        """
        # Default limits per endpoint per minute
        DEFAULT_ENDPOINT_LIMIT = 100
        
        # Specific limits for certain endpoints
        endpoint_limits = {
            'order': 50,           # Order-related endpoints
            'trade': 30,           # Trade history endpoints
            'account': 30,         # Account endpoints
            'market_data': 200     # Market data endpoints
        }
        
        # Determine limit for this endpoint
        limit = endpoint_limits.get(endpoint, DEFAULT_ENDPOINT_LIMIT)
        
        # Safety factor (90% of actual limit)
        safe_limit = int(limit * 0.9)
        
        # Get history for this endpoint
        history = self.endpoint_request_history.get(endpoint, [])
        
        if len(history) >= safe_limit:
            # Calculate wait time
            oldest_to_expire = sorted(history)[-(safe_limit) + 1]
            return max(0, (oldest_to_expire + 60) - current_time)
        
        return 0
    
    async def handle_rate_limit_error(self, retry_after: Optional[int] = None) -> float:
        """
        Handle a rate limit error with exponential backoff.
        
        Args:
            retry_after: Optional retry-after time in seconds from API response
            
        Returns:
            Backoff time in seconds
        """
        async with self.lock:
            # If API provides a retry-after time, use that
            if retry_after is not None:
                backoff_seconds = retry_after
            else:
                # Otherwise use exponential backoff
                backoff_seconds = min(self.max_backoff, 2 ** (self.backoff_multiplier - 1))
                self.backoff_multiplier = min(6, self.backoff_multiplier + 1)  # Cap at 2^5 = 32 seconds
            
            # Set backoff until time
            self.backoff_until = datetime.now() + timedelta(seconds=backoff_seconds)
            
            logger.warning(f"Rate limit hit, backing off for {backoff_seconds:.2f}s")
            return backoff_seconds
    
    def reset_backoff(self) -> None:
        """Reset the backoff multiplier after successful requests."""
        if self.backoff_multiplier > 1.0:
            self.backoff_multiplier = max(1.0, self.backoff_multiplier * 0.8)  # Gradually reduce
            
    @property
    def requests_in_last_minute(self) -> int:
        """Get the number of requests made in the last minute."""
        self._clean_history(time.time())
        return len(self.request_history_minute)
    
    @property
    def requests_in_last_second(self) -> int:
        """Get the number of requests made in the last second."""
        self._clean_history(time.time())
        return len(self.request_history_second)
    
    @property
    def weight_in_last_minute(self) -> int:
        """Get the total weight of requests made in the last minute."""
        self._clean_history(time.time())
        return sum(w for _, w in self.weight_history_minute)


# Decorator for rate-limited methods
def rate_limited(endpoint: Optional[str] = None, weight: int = 1):
    """
    Decorator to apply rate limiting to a method.
    
    Args:
        endpoint: API endpoint category for this method
        weight: Request weight for this method
        
    Returns:
        Decorated method with rate limiting
    """
    def decorator(func):
        async def wrapper(self, *args, **kwargs):
            # Find rate_limiter attribute in the instance
            rate_limiter = getattr(self, 'rate_limiter', None)
            if rate_limiter is None:
                logger.warning(f"No rate_limiter found in {self.__class__.__name__}, proceeding without rate limiting")
                return await func(self, *args, **kwargs)
                
            # Wait if needed before making the request
            await rate_limiter.wait_if_needed(endpoint, weight)
            
            try:
                # Execute the original method
                result = await func(self, *args, **kwargs)
                
                # Reset backoff on success
                rate_limiter.reset_backoff()
                
                return result
            except Exception as e:
                # If it's a rate limit error, handle it specially
                if hasattr(e, 'code') and getattr(e, 'code', 0) in (429, -1015):
                    # Extract retry-after if available
                    retry_after = None
                    if hasattr(e, 'response') and e.response:
                        retry_after = e.response.headers.get('Retry-After')
                        if retry_after:
                            retry_after = int(retry_after)
                    
                    # Handle rate limit with backoff
                    await rate_limiter.handle_rate_limit_error(retry_after)
                
                # Re-raise the exception
                raise
                
        return wrapper
    return decorator
