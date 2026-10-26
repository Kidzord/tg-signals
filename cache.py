"""Кеширование для ускорения частоиспользуемых операций.

Использует TTL-кеш в памяти для быстрых запросов.
Для продакшена можно заменить на Redis.
"""
from __future__ import annotations

import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class Cache:
    """Простой TTL-кеш в памяти."""

    def __init__(self, default_ttl: int = 300):
        """default_ttl: время жизни в секундах"""
        self._store: dict[str, tuple[Any, float]] = {}
        self._default_ttl = default_ttl

    def get(self, key: str) -> Any | None:
        """Получить значение из кеша."""
        if key not in self._store:
            return None
        value, expiry = self._store[key]
        if time.time() > expiry:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Сохранить значение в кеш."""
        expiry = time.time() + (ttl or self._default_ttl)
        self._store[key] = (value, expiry)

    def delete(self, key: str) -> None:
        """Удалить значение из кеша."""
        self._store.pop(key, None)

    def clear(self) -> None:
        """Очистить весь кеш."""
        self._store.clear()

    def cleanup(self) -> int:
        """Удалить просроченные записи, вернуть количество удалённых."""
        now = time.time()
        before = len(self._store)
        self._store = {k: v for k, v in self._store.items() if v[1] > now}
        return before - len(self._store)


# Глобальный инстанс кеша
_global_cache = Cache(default_ttl=300)


def cached(ttl: int = 300, key_prefix: str = ""):
    """Декоратор для кеширования результатов функций.

    Args:
        ttl: время жизни в секундах
        key_prefix: префикс для ключа кеша
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args: Any, **kwargs: Any) -> T:
            # Формируем ключ из аргументов
            cache_key = f"{key_prefix}{func.__name__}{str(args)}{str(sorted(kwargs.items()))}"
            cached_value = _global_cache.get(cache_key)
            if cached_value is not None:
                return cached_value

            result = func(*args, **kwargs)
            _global_cache.set(cache_key, result, ttl)
            return result
        return wrapper
    return decorator


def get_cache() -> Cache:
    """Получить глобальный инстанс кеша."""
    return _global_cache


def invalidate_pattern(pattern: str) -> int:
    """Инвалидировать все ключи, начинающиеся с pattern."""
    keys_to_delete = [k for k in _global_cache._store.keys() if k.startswith(pattern)]
    for key in keys_to_delete:
        _global_cache.delete(key)
    return len(keys_to_delete)
