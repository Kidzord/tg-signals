"""Rate limiting для защиты от спама и DOS-атак.

Реализует sliding window algorithm для точного контроля частоты запросов.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Optional


class RateLimiter:
    """Ограничитель частоты запросов с sliding window."""

    def __init__(self, max_requests: int, window_seconds: int):
        """max_requests: максимальное количество запросов
        window_seconds: окно времени в секундах
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._windows: dict[str, deque[float]] = {}

    def is_allowed(self, identifier: str) -> tuple[bool, int]:
        """Проверить, разрешён ли запрос.

        Args:
            identifier: уникальный идентификатор (user_id, IP и т.д.)

        Returns:
            (allowed, retry_after) - разрешён ли запрос и через сколько секунд можно повторить
        """
        now = time.time()
        window = self._windows.get(identifier)

        if window is None:
            window = deque()
            self._windows[identifier] = window

        # Удаляем старые записи вне окна
        while window and window[0] <= now - self.window_seconds:
            window.popleft()

        if len(window) >= self.max_requests:
            # Вычисляем, когда освободится слот
            oldest = window[0]
            retry_after = int(oldest + self.window_seconds - now) + 1
            return False, retry_after

        window.append(now)
        return True, 0

    def reset(self, identifier: str) -> None:
        """Сбросить лимиты для идентификатора."""
        self._windows.pop(identifier, None)

    def cleanup(self) -> int:
        """Удалить старые окна, вернуть количество удалённых."""
        now = time.time()
        before = len(self._windows)
        self._windows = {
            k: v for k, v in self._windows.items()
            if v and v[-1] > now - self.window_seconds
        }
        return before - len(self._windows)

    def get_stats(self, identifier: str) -> dict:
        """Получить статистику для идентификатора."""
        window = self._windows.get(identifier)
        if not window:
            return {"count": 0, "max": self.max_requests, "window": self.window_seconds}

        now = time.time()
        # Удаляем старые записи
        while window and window[0] <= now - self.window_seconds:
            window.popleft()

        return {
            "count": len(window),
            "max": self.max_requests,
            "window": self.window_seconds,
            "reset_at": int(window[0] + self.window_seconds - now) if window else 0
        }


# Преднастроенные лимитеры для разных целей
_webhook_limiter = RateLimiter(max_requests=10, window_seconds=60)  # 10 webhook'ов в минуту
_api_limiter = RateLimiter(max_requests=100, window_seconds=60)  # 100 API запросов в минуту
_bot_command_limiter = RateLimiter(max_requests=20, window_seconds=60)  # 20 команд бота в минуту


def check_webhook(identifier: str) -> tuple[bool, int]:
    """Проверить лимит для webhook."""
    return _webhook_limiter.is_allowed(identifier)


def check_api(identifier: str) -> tuple[bool, int]:
    """Проверить лимит для API."""
    return _api_limiter.is_allowed(identifier)


def check_bot_command(user_id: int) -> tuple[bool, int]:
    """Проверить лимит для команд бота."""
    return _bot_command_limiter.is_allowed(str(user_id))


def cleanup() -> int:
    """Очистить старые записи во всех лимитерах."""
    total = 0
    total += _webhook_limiter.cleanup()
    total += _api_limiter.cleanup()
    total += _bot_command_limiter.cleanup()
    return total
