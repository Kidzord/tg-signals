"""Улучшенные health checks и мониторинг состояния системы.

Проверяет все критические компоненты и предоставляет детальную информацию.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import config as cfg
import logger_config


@dataclass
class HealthCheck:
    """Результат health check."""
    name: str
    status: str  # "healthy", "degraded", "unhealthy"
    message: str
    duration_ms: float
    extra: dict[str, Any] | None = None


class HealthMonitor:
    """Мониторинг состояния системы."""

    def __init__(self):
        self._checks: dict[str, Callable[[], HealthCheck]] = {}
        self._history: list[dict] = []
        self._max_history = 1000

    def register(self, name: str, check_func: Callable[[], HealthCheck]) -> None:
        """Зарегистрировать health check."""
        self._checks[name] = check_func

    def run_all(self) -> dict[str, Any]:
        """Запустить все проверки и вернуть сводку."""
        results = []
        overall_status = "healthy"
        total_duration = 0.0

        for name, check_func in self._checks.items():
            start = time.time()
            try:
                result = check_func()
                results.append({
                    "name": name,
                    "status": result.status,
                    "message": result.message,
                    "duration_ms": result.duration_ms,
                    "extra": result.extra,
                })
                total_duration += result.duration_ms

                # Определяем общий статус
                if result.status == "unhealthy":
                    overall_status = "unhealthy"
                elif result.status == "degraded" and overall_status != "unhealthy":
                    overall_status = "degraded"
            except Exception as e:
                results.append({
                    "name": name,
                    "status": "unhealthy",
                    "message": f"Exception: {str(e)}",
                    "duration_ms": (time.time() - start) * 1000,
                    "extra": None,
                })
                overall_status = "unhealthy"

        summary = {
            "status": overall_status,
            "timestamp": datetime.utcnow().isoformat(),
            "total_duration_ms": total_duration,
            "checks": results,
        }

        # Сохраняем в историю
        self._history.append(summary)
        if len(self._history) > self._max_history:
            self._history.pop(0)

        return summary

    def get_history(self, limit: int = 100) -> list[dict]:
        """Получить историю проверок."""
        return self._history[-limit:]


# Глобальный инстанс монитора
monitor = HealthMonitor()


# ------------------------------------------------------------------ проверка MT5
def check_mt5() -> HealthCheck:
    """Проверить подключение к MetaTrader 5."""
    start = time.time()
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return HealthCheck(
            name="mt5",
            status="degraded",
            message="MetaTrader5 package not installed",
            duration_ms=(time.time() - start) * 1000,
            extra={"installed": False}
        )

    if not mt5.initialize(timeout=5000):
        error = mt5.last_error()
        return HealthCheck(
            name="mt5",
            status="unhealthy",
            message=f"MT5 initialization failed: {error}",
            duration_ms=(time.time() - start) * 1000,
            extra={"error": str(error)}
        )

    try:
        ai = mt5.account_info()
        if ai is None:
            return HealthCheck(
                name="mt5",
                status="degraded",
                message="MT5 not logged in",
                duration_ms=(time.time() - start) * 1000,
                extra={"logged_in": False}
            )

        return HealthCheck(
            name="mt5",
            status="healthy",
            message=f"Connected to {ai.server}",
            duration_ms=(time.time() - start) * 1000,
            extra={
                "server": ai.server,
                "login_masked": f"****{str(ai.login)[-4:]}",
                "connected": ai.trade_mode == 0,  # demo
                "currency": ai.currency,
            }
        )
    finally:
        mt5.shutdown()


# ------------------------------------------------------------------ проверка Telegram
def check_telegram() -> HealthCheck:
    """Проверить подключение к Telegram API."""
    start = time.time()
    if not cfg.BOT_TOKEN:
        return HealthCheck(
            name="telegram",
            status="degraded",
            message="No bot token configured",
            duration_ms=(time.time() - start) * 1000,
            extra={"token_configured": False}
        )

    try:
        import requests
        r = requests.get(
            f"https://api.telegram.org/bot{cfg.BOT_TOKEN}/getMe",
            timeout=5
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("ok"):
                return HealthCheck(
                    name="telegram",
                    status="healthy",
                    message=f"Bot @{data['result']['username']}",
                    duration_ms=(time.time() - start) * 1000,
                    extra={"username": data["result"]["username"]}
                )
        return HealthCheck(
            name="telegram",
            status="unhealthy",
            message=f"Telegram API error: {r.status_code}",
            duration_ms=(time.time() - start) * 1000,
            extra={"status_code": r.status_code}
        )
    except Exception as e:
        return HealthCheck(
            name="telegram",
            status="unhealthy",
            message=f"Connection failed: {str(e)}",
            duration_ms=(time.time() - start) * 1000,
        )


# ------------------------------------------------------------------ проверка диска
def check_disk_space() -> HealthCheck:
    """Проверить свободное место на диске."""
    start = time.time()
    try:
        import shutil
        total, used, free = shutil.disk_usage(os.getcwd())
        free_pct = (free / total) * 100

        status = "healthy"
        if free_pct < 5:
            status = "unhealthy"
        elif free_pct < 10:
            status = "degraded"

        return HealthCheck(
            name="disk",
            status=status,
            message=f"{free / (1024**3):.1f} GB free ({free_pct:.1f}%)",
            duration_ms=(time.time() - start) * 1000,
            extra={
                "total_gb": total / (1024**3),
                "used_gb": used / (1024**3),
                "free_gb": free / (1024**3),
                "free_percent": free_pct,
            }
        )
    except Exception as e:
        return HealthCheck(
            name="disk",
            status="unhealthy",
            message=f"Failed to check disk: {str(e)}",
            duration_ms=(time.time() - start) * 1000,
        )


# ------------------------------------------------------------------ проверка памяти
def check_memory() -> HealthCheck:
    """Проверить использование памяти."""
    start = time.time()
    try:
        import psutil
        process = psutil.Process()
        mem_info = process.memory_info()
        mem_pct = process.memory_percent()

        status = "healthy"
        if mem_pct > 90:
            status = "unhealthy"
        elif mem_pct > 70:
            status = "degraded"

        return HealthCheck(
            name="memory",
            status=status,
            message=f"{mem_info.rss / (1024**2):.1f} MB ({mem_pct:.1f}%)",
            duration_ms=(time.time() - start) * 1000,
            extra={
                "rss_mb": mem_info.rss / (1024**2),
                "percent": mem_pct,
            }
        )
    except ImportError:
        return HealthCheck(
            name="memory",
            status="degraded",
            message="psutil not installed",
            duration_ms=(time.time() - start) * 1000,
        )
    except Exception as e:
        return HealthCheck(
            name="memory",
            status="unhealthy",
            message=f"Failed to check memory: {str(e)}",
            duration_ms=(time.time() - start) * 1000,
        )


# ------------------------------------------------------------------ проверка файлов состояния
def check_state_files() -> HealthCheck:
    """Проверить целостность файлов состояния."""
    start = time.time()
    issues = []

    # Проверяем bot_state файлы
    state_files = [
        "bot_state_XAUUSD.json",
        "bot_state_GOLD.json",
        "users.json",
        "sub_state.json",
    ]

    for filename in state_files:
        path = os.path.join(os.path.dirname(__file__), filename)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    json.load(f)
            except json.JSONDecodeError as e:
                issues.append(f"{filename}: invalid JSON - {str(e)}")
            except Exception as e:
                issues.append(f"{filename}: {str(e)}")

    status = "healthy"
    if issues:
        status = "unhealthy"

    return HealthCheck(
        name="state_files",
        status=status,
        message=f"{len(issues)} issues found" if issues else "All files valid",
        duration_ms=(time.time() - start) * 1000,
        extra={"issues": issues} if issues else None,
    )


# ------------------------------------------------------------------ регистрация проверок
def register_default_checks() -> None:
    """Зарегистрировать стандартные проверки."""
    monitor.register("mt5", check_mt5)
    monitor.register("telegram", check_telegram)
    monitor.register("disk", check_disk_space)
    monitor.register("memory", check_memory)
    monitor.register("state_files", check_state_files)


# Автоматическая регистрация при импорте
register_default_checks()
