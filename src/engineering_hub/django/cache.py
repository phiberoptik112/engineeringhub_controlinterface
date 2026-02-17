"""Simple TTL cache for Django API responses."""

import time
from functools import wraps
from typing import Any, Callable


class TTLCache:
    """Simple in-memory cache with TTL expiration."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        """Initialize cache with TTL in seconds."""
        self.ttl = ttl_seconds
        self._cache: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        """Get value from cache if not expired."""
        if key not in self._cache:
            return None

        value, timestamp = self._cache[key]
        if time.time() - timestamp > self.ttl:
            del self._cache[key]
            return None

        return value

    def set(self, key: str, value: Any) -> None:
        """Set value in cache with current timestamp."""
        self._cache[key] = (value, time.time())

    def invalidate(self, key: str) -> None:
        """Remove a specific key from cache."""
        self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear all cached values."""
        self._cache.clear()


def cached(cache: TTLCache, key_func: Callable[..., str]):
    """Decorator to cache function results.

    Args:
        cache: TTLCache instance to use
        key_func: Function that generates cache key from args/kwargs
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            key = key_func(*args, **kwargs)
            cached_value = cache.get(key)

            if cached_value is not None:
                return cached_value

            result = func(*args, **kwargs)
            cache.set(key, result)
            return result

        return wrapper

    return decorator
