import hashlib
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from functools import wraps

log = logging.getLogger(__name__)


class Cache(ABC):
    """
    This is the abstract base class for all cache implementations.
    Concrete subclasses must provide implementations for the abstract methods that define how caching is handled.


    When a cache instance is attached to a client, it will automatically store the results of the client`s method calls.
    If the same method is invoked multiple times with identical arguments, the cache returns the stored result instead of re-executing the method.
    """

    @abstractmethod
    def get(self, key: str) -> object:
        """
        Retrieve an object from the cache.

        Args:
            key (str): The key to retrieve the object for.

        Returns:
            The object stored in the cache.
        """

    @abstractmethod
    def set(self, key: str, value: str):
        """
        Store an object in the cache.

        Args:
            key (str): The key to store the object for.
            value (str): The object to store in the cache.
        """


def cacheable(key_func: Callable):
    """
    Decorator that caches function results based on key functions.

    Args:
        *key_funcs: Functions that take the function's self and arguments and return a value for the cache key
    """

    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if not self.cache:
                return func(self, *args, **kwargs)

            try:
                # Get all arguments as a dictionary
                bound_args = kwargs.copy()
                func_args = func.__code__.co_varnames[: func.__code__.co_argcount]
                for i, arg_name in enumerate(func_args[1:]):  # Skip 'self'
                    if i < len(args):
                        bound_args[arg_name] = args[i]

                # Generate cache key using the provided functions

                cache_key = key_func(self, bound_args)
                # hash the string
                cache_key = hashlib.sha256(cache_key.encode()).hexdigest()

                cached_result = self.cache.get(cache_key)
                if cached_result is not None:
                    log.info(f"Cache hit for {cache_key}")
                    return cached_result

            except Exception as e:
                log.warning(f"Error generating cache key for {func.__name__}: {e}")
                return func(self, *args, **kwargs)

            # Execute function if not cached
            result = func(self, *args, **kwargs)
            try:
                self.cache.set(cache_key, result)
            except Exception as e:
                log.error(f"Error setting cache for {cache_key}: {e}")
            return result

        return wrapper

    return decorator


class MemoryCache(Cache):
    """
    A simple in-memory cache implementation.
    """

    def __init__(self):
        self.cache = {}

    def get(self, key: str) -> object:
        """Retrieve an object from the cache."""
        return self.cache.get(key)

    def set(self, key: str, value: str):
        """Set an object in the cache."""
        self.cache[key] = value
