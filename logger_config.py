"""Конфигурация структурированного логирования с метриками.

Поддерживает JSON-логи для парсинга и красивый вывод для разработки.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any

# Директория для логов
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)


class JSONFormatter(logging.Formatter):
    """Форматтер для JSON-логов."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Добавляем extra поля
        for key, value in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "created", "filename", "funcName",
                "levelname", "levelno", "lineno", "module", "msecs",
                "message", "pathname", "process", "processName", "relativeCreated",
                "stack_info", "thread", "threadName", "exc_info", "exc_text",
                "asctime", "coloredname"
            }:
                log_entry[key] = value

        return json.dumps(log_entry, ensure_ascii=False)


class Metrics:
    """Сборщик метрик приложения."""

    def __init__(self):
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}
        self._start_time = time.time()

    def increment(self, name: str, value: int = 1) -> None:
        """Увеличить счётчик."""
        self._counters[name] = self._counters.get(name, 0) + value

    def set_gauge(self, name: str, value: float) -> None:
        """Установить значение gauge."""
        self._gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        """Добавить значение в гистограмму."""
        if name not in self._histograms:
            self._histograms[name] = []
        self._histograms[name].append(value)

    def get_counter(self, name: str) -> int:
        """Получить значение счётчика."""
        return self._counters.get(name, 0)

    def get_gauge(self, name: str) -> float:
        """Получить значение gauge."""
        return self._gauges.get(name, 0.0)

    def get_histogram_stats(self, name: str) -> dict[str, float] | None:
        """Получить статистику гистограммы."""
        if name not in self._histograms:
            return None
        values = self._histograms[name]
        if not values:
            return None
        values_sorted = sorted(values)
        n = len(values_sorted)
        return {
            "count": n,
            "min": values_sorted[0],
            "max": values_sorted[-1],
            "avg": sum(values_sorted) / n,
            "p50": values_sorted[int(n * 0.5)],
            "p95": values_sorted[int(n * 0.95)],
            "p99": values_sorted[int(n * 0.99)],
        }

    def get_all_metrics(self) -> dict[str, Any]:
        """Получить все метрики."""
        return {
            "uptime_seconds": time.time() - self._start_time,
            "counters": self._counters.copy(),
            "gauges": self._gauges.copy(),
            "histograms": {k: self.get_histogram_stats(k) for k in self._histograms},
        }

    def reset(self) -> None:
        """Сбросить все метрики."""
        self._counters.clear()
        self._gauges.clear()
        self._histograms.clear()
        self._start_time = time.time()


# Глобальный инстанс метрик
metrics = Metrics()


def setup_logging(
    level: str = "INFO",
    json_logs: bool = False,
    log_file: str | None = None
) -> None:
    """Настроить логирование.

    Args:
        level: уровень логирования (DEBUG, INFO, WARNING, ERROR)
        json_logs: использовать JSON формат для логов
        log_file: путь к файлу логов (если None - только stdout)
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    handlers: list[logging.Handler] = []

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    if json_logs:
        console_handler.setFormatter(JSONFormatter())
    else:
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    handlers.append(console_handler)

    # File handler
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        if json_logs:
            file_handler.setFormatter(JSONFormatter())
        else:
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            )
        handlers.append(file_handler)

    # Configure root logger
    logging.basicConfig(
        level=log_level,
        handlers=handlers,
        force=True
    )

    # Уменьшаем шум от внешних библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Получить логгер с именем."""
    return logging.getLogger(name)


# Функции для удобного логирования с контекстом
def log_with_context(logger: logging.Logger, level: str, message: str, **context: Any) -> None:
    """Залогировать сообщение с дополнительным контекстом."""
    log_func = getattr(logger, level.lower())
    extra = {f"ctx_{k}": v for k, v in context.items()}
    log_func(message, extra=extra)
