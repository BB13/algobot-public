"""
File locking utilities for cross-platform concurrency control.
"""
import os
import errno
import logging
import platform
from contextlib import contextmanager

# Import platform-specific modules
if platform.system() == 'Windows':
    import msvcrt
    HAS_FCNTL = False
else:
    import fcntl
    HAS_FCNTL = True

logger = logging.getLogger(__name__)

class FileLockException(Exception):
    """Exception raised when file locking fails."""
    pass

@contextmanager
def file_lock(file_path, mode='r', timeout=30, retry_interval=0.1):
    """
    Cross-platform file locking context manager.
    
    Args:
        file_path: Path to the file to lock
        mode: File mode ('r' for shared/read lock, 'w' for exclusive/write lock)
        timeout: Maximum time to wait for lock acquisition in seconds
        retry_interval: Time between retries in seconds
        
    Yields:
        File object that has been locked
        
    Raises:
        FileLockException: If lock cannot be acquired within timeout
    """
    file_obj = None
    lock_file_path = f"{file_path}.lock"
    
    try:
        # Create parent directory if it doesn't exist
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        if HAS_FCNTL:
            # Unix-based systems - use fcntl
            logger.debug(f"Attempting to lock file {file_path} using fcntl")
            
            # Open the file (create if doesn't exist)
            file_obj = open(file_path, 'a+' if not os.path.exists(file_path) else 'r+')
            
            # Set lock type
            lock_operation = fcntl.LOCK_SH if mode == 'r' else fcntl.LOCK_EX
            
            # Try to acquire lock with timeout
            import time
            start_time = time.time()
            
            while True:
                try:
                    fcntl.flock(file_obj.fileno(), lock_operation | fcntl.LOCK_NB)
                    logger.debug(f"Lock acquired on {file_path}")
                    break
                except (IOError, OSError) as e:
                    if e.errno in (errno.EACCES, errno.EAGAIN):
                        if time.time() - start_time > timeout:
                            raise FileLockException(f"Timed out waiting for lock on {file_path}")
                        # Wait and retry
                        time.sleep(retry_interval)
                    else:
                        raise
        else:
            # Windows systems - use lock files and msvcrt
            logger.debug(f"Attempting to lock file {file_path} using msvcrt")
            
            # Try to create/open the lock file
            import time
            start_time = time.time()
            
            while True:
                try:
                    # For Windows, create a separate lock file
                    if not os.path.exists(os.path.dirname(lock_file_path)):
                        os.makedirs(os.path.dirname(lock_file_path), exist_ok=True)
                    
                    lock_file = open(lock_file_path, 'w')
                    
                    # Try to lock it
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    
                    # If we got here, we have the lock - open the actual file
                    file_mode = 'a+' if not os.path.exists(file_path) else 'r+'
                    file_obj = open(file_path, file_mode)
                    
                    logger.debug(f"Lock acquired on {file_path}")
                    break
                except (IOError, OSError) as e:
                    if e.errno == errno.EACCES:
                        if time.time() - start_time > timeout:
                            raise FileLockException(f"Timed out waiting for lock on {file_path}")
                        # Wait and retry
                        time.sleep(retry_interval)
                    else:
                        raise
        
        # Set file to beginning for reading
        if file_obj:
            file_obj.seek(0)
        
        # File is locked, yield it
        yield file_obj
    
    finally:
        # Release the lock
        if file_obj:
            if HAS_FCNTL:
                try:
                    fcntl.flock(file_obj.fileno(), fcntl.LOCK_UN)
                    logger.debug(f"Lock released on {file_path}")
                except Exception as e:
                    logger.error(f"Error releasing lock on {file_path}: {str(e)}")
                finally:
                    file_obj.close()
            else:
                try:
                    file_obj.close()
                    if os.path.exists(lock_file_path):
                        os.remove(lock_file_path)
                    logger.debug(f"Lock released on {file_path}")
                except Exception as e:
                    logger.error(f"Error releasing lock on {file_path}: {str(e)}")

@contextmanager
def read_lock(file_path, timeout=10, retry_interval=0.1):
    """
    Acquire a read (shared) lock on a file.
    
    Args:
        file_path: Path to the file to lock
        timeout: Maximum time to wait for lock acquisition in seconds
        retry_interval: Time between retries in seconds
        
    Yields:
        File object with a read lock
    """
    with file_lock(file_path, mode='r', timeout=timeout, retry_interval=retry_interval) as f:
        yield f

@contextmanager
def write_lock(file_path, timeout=30, retry_interval=0.1):
    """
    Acquire a write (exclusive) lock on a file.
    
    Args:
        file_path: Path to the file to lock
        timeout: Maximum time to wait for lock acquisition in seconds
        retry_interval: Time between retries in seconds
        
    Yields:
        File object with a write lock
    """
    with file_lock(file_path, mode='w', timeout=timeout, retry_interval=retry_interval) as f:
        yield f
