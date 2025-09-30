"""
Connection management utility for API clients.

This module provides connection pooling and reconnection logic for API clients.
"""
import asyncio
import logging
import time
from typing import Optional, Callable, Any, Dict, Union, List
from binance.client import Client
from binance.exceptions import BinanceAPIException

logger = logging.getLogger(__name__)

class ConnectionManager:
    """
    Manages connections to API services.
    
    Features:
    - Connection pooling for better resource usage
    - Automatic reconnection on connection failures
    - Health checks and monitoring
    
    Attributes:
        max_clients: Maximum number of client instances to maintain
        client_factory: Factory function to create new client instances
        clients: List of available client instances
        active_clients: Dict mapping client to last activity timestamp
        lock: Lock for thread-safe client access
        health_check_interval: Seconds between health checks
    """
    
    def __init__(
        self,
        client_factory: Callable[[], Any],
        max_clients: int = 3,
        max_idle_time: int = 300,
        health_check_interval: int = 60
    ):
        """
        Initialize the connection manager.
        
        Args:
            client_factory: Factory function that creates new client instances
            max_clients: Maximum number of client instances to maintain
            max_idle_time: Time in seconds before idle connections are closed
            health_check_interval: Seconds between health checks
        """
        self.client_factory = client_factory
        self.max_clients = max_clients
        self.max_idle_time = max_idle_time
        self.health_check_interval = health_check_interval
        
        # Connection pool
        self.clients: List[Any] = []
        self.active_clients: Dict[Any, float] = {}  # Maps client to last activity timestamp
        self.lock = asyncio.Lock()
        
        # Health check task
        self.health_check_task: Optional[asyncio.Task] = None
        self.running = False
        
        logger.info(f"Connection manager initialized with max {max_clients} clients")
    
    async def start(self) -> None:
        """Start the connection manager and health check task."""
        self.running = True
        self.health_check_task = asyncio.create_task(self._health_check_loop())
        logger.info("Connection manager started")
    
    async def stop(self) -> None:
        """Stop the connection manager and close all connections."""
        self.running = False
        
        # Cancel health check task
        if self.health_check_task:
            self.health_check_task.cancel()
            try:
                await self.health_check_task
            except asyncio.CancelledError:
                pass
        
        # Close all clients
        async with self.lock:
            for client in self.clients:
                # If client has a close method, call it
                if hasattr(client, 'close'):
                    await self._close_client(client)
            
            self.clients = []
            self.active_clients = {}
        
        logger.info("Connection manager stopped")
    
    async def get_client(self) -> Any:
        """
        Get a client from the pool or create a new one.
        
        Returns:
            A client instance ready to use
        """
        async with self.lock:
            # Try to find an existing client
            if self.clients:
                client = self.clients.pop(0)
                self.active_clients[client] = time.time()
                return client
            
            # Create a new client if under the limit
            if len(self.active_clients) < self.max_clients:
                client = self._create_client()
                self.active_clients[client] = time.time()
                return client
            
            # Wait for a client to become available
            logger.warning("All connections in use, waiting for available client")
        
        # Release the lock while waiting
        await asyncio.sleep(0.1)
        return await self.get_client()  # Recursively try again
    
    async def release_client(self, client: Any) -> None:
        """
        Release a client back to the pool.
        
        Args:
            client: The client instance to release
        """
        async with self.lock:
            # Update activity timestamp
            self.active_clients[client] = time.time()
            
            # Add back to available pool
            if client not in self.clients:
                self.clients.append(client)
    
    async def _health_check_loop(self) -> None:
        """Background task to perform periodic health checks."""
        try:
            while self.running:
                await self._perform_health_check()
                await asyncio.sleep(self.health_check_interval)
        except asyncio.CancelledError:
            logger.debug("Health check task cancelled")
        except Exception as e:
            logger.error(f"Error in health check loop: {str(e)}", exc_info=True)
    
    async def _perform_health_check(self) -> None:
        """
        Perform health check on clients and manage idle connections.
        
        This checks if clients are responsive and closes idle connections
        that exceed the max idle time.
        """
        current_time = time.time()
        clients_to_check = []
        
        # Gather clients for checking
        async with self.lock:
            # Check available clients
            for client in list(self.clients):
                last_active = self.active_clients.get(client, 0)
                
                # Close idle clients
                if current_time - last_active > self.max_idle_time:
                    self.clients.remove(client)
                    await self._close_client(client)
                    del self.active_clients[client]
                    logger.info(f"Closed idle client after {self.max_idle_time}s")
                else:
                    # Add to check list
                    clients_to_check.append(client)
            
            # Check active clients (not in the available pool)
            active_only = set(self.active_clients.keys()) - set(self.clients)
            for client in active_only:
                clients_to_check.append(client)
        
        # Test clients one by one (outside lock to avoid blocking)
        for client in clients_to_check:
            await self._test_client_health(client)
    
    async def _test_client_health(self, client: Any) -> bool:
        """
        Test if a client is healthy.
        
        Args:
            client: Client to test
            
        Returns:
            True if client is responsive, False otherwise
        """
        try:
            # Use a simple ping or status check endpoint
            if isinstance(client, Client):  # Binance client
                # Use ping method for Binance client
                await asyncio.get_event_loop().run_in_executor(None, client.ping)
            elif hasattr(client, 'ping'):
                # Generic ping method
                if asyncio.iscoroutinefunction(client.ping):
                    await client.ping()
                else:
                    await asyncio.get_event_loop().run_in_executor(None, client.ping)
            else:
                # No ping method, assume healthy
                logger.debug(f"No health check method for {type(client).__name__}")
                return True
                
            return True
        except Exception as e:
            logger.warning(f"Client health check failed: {str(e)}")
            
            # Replace unhealthy client
            await self._replace_client(client)
            return False
    
    async def _replace_client(self, client: Any) -> None:
        """
        Replace an unhealthy client.
        
        Args:
            client: Client to replace
        """
        async with self.lock:
            # Remove from collections
            if client in self.clients:
                self.clients.remove(client)
            
            if client in self.active_clients:
                del self.active_clients[client]
            
            # Close the client
            await self._close_client(client)
            
            # Create a new client if there's room
            if len(self.active_clients) < self.max_clients:
                new_client = self._create_client()
                self.clients.append(new_client)
                self.active_clients[new_client] = time.time()
                logger.info("Created new client to replace unhealthy one")
    
    def _create_client(self) -> Any:
        """
        Create a new client instance.
        
        Returns:
            New client instance
        """
        try:
            client = self.client_factory()
            logger.info(f"Created new {type(client).__name__} instance")
            return client
        except Exception as e:
            logger.error(f"Failed to create client: {str(e)}", exc_info=True)
            raise
    
    async def _close_client(self, client: Any) -> None:
        """
        Close a client connection.
        
        Args:
            client: Client to close
        """
        try:
            # Try different close methods
            if hasattr(client, 'close'):
                if asyncio.iscoroutinefunction(client.close):
                    await client.close()
                else:
                    await asyncio.get_event_loop().run_in_executor(None, client.close)
            elif hasattr(client, 'disconnect'):
                if asyncio.iscoroutinefunction(client.disconnect):
                    await client.disconnect()
                else:
                    await asyncio.get_event_loop().run_in_executor(None, client.disconnect)
            
            logger.debug(f"Closed client connection: {type(client).__name__}")
        except Exception as e:
            logger.warning(f"Error closing client: {str(e)}")


class BinanceConnectionManager(ConnectionManager):
    """
    Specialized connection manager for Binance API clients.
    
    This extends the base ConnectionManager with Binance-specific health checks
    and error handling.
    """
    
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        max_clients: int = 3,
        max_idle_time: int = 300,
        health_check_interval: int = 60
    ):
        """
        Initialize the Binance connection manager.
        
        Args:
            api_key: Binance API key
            api_secret: Binance API secret
            testnet: Whether to use testnet
            max_clients: Maximum number of client instances to maintain
            max_idle_time: Time in seconds before idle connections are closed
            health_check_interval: Seconds between health checks
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        
        # Define client factory function
        def create_binance_client():
            return Client(api_key, api_secret, testnet=testnet)
        
        super().__init__(
            client_factory=create_binance_client,
            max_clients=max_clients,
            max_idle_time=max_idle_time,
            health_check_interval=health_check_interval
        )
    
    async def _test_client_health(self, client: Client) -> bool:
        """
        Test if a Binance client is healthy.
        
        Args:
            client: Binance client to test
            
        Returns:
            True if client is responsive, False otherwise
        """
        try:
            # Use ping method which is lightweight
            await asyncio.get_event_loop().run_in_executor(None, client.ping)
            
            # Additionally check server time to verify data connection
            await asyncio.get_event_loop().run_in_executor(None, client.get_server_time)
            
            return True
        except BinanceAPIException as e:
            # Some API errors don't indicate connection problems
            if e.code in (-1022, -2014, -2015):  # Auth issues
                logger.error(f"Authentication error in health check: {e.message}")
                return False
            elif e.code == -1003:  # Too many requests
                logger.warning(f"Rate limit reached in health check: {e.message}")
                return True  # Client is still connected, just rate limited
            else:
                logger.warning(f"API error in health check: {e.message}")
                return False
        except Exception as e:
            logger.warning(f"Client health check failed: {str(e)}")
            return False


# Factory function to create a Binance connection manager
def create_binance_connection_manager(
    api_key: str,
    api_secret: str,
    testnet: bool = False
) -> BinanceConnectionManager:
    """
    Create a new Binance connection manager.
    
    Args:
        api_key: Binance API key
        api_secret: Binance API secret
        testnet: Whether to use testnet
        
    Returns:
        Configured BinanceConnectionManager instance
    """
    return BinanceConnectionManager(api_key, api_secret, testnet)
