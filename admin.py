"""Админ-панель: управление пользователями, мониторинг, A/B тесты.

Запуск:
    python admin.py        — поднять админ-панель на порту 8081
    или включить в app.py через /admin endpoints

Аутентификация через простой токен в заголовке X-Admin-Token.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import FileResponse

import config as cfg
import logger_config
import health
import gallery
import analytics
import gamification
import ai_features
import advanced_analytics

log = logger_config.get_logger("admin")

# Пути к файлам данных
USERS_PATH = os.path.join(os.path.dirname(__file__), "users.json")
TRADES_PATH = os.path.join(os.path.dirname(__file__), "trades.jsonl")
AB_TESTS_PATH = os.path.join(os.path.dirname(__file__), "ab_tests.json")

# Простой токен для админки (в продакшене использовать более надёжную аутентификацию)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "change-me-admin-token")


@dataclass
class ABTest:
    """Конфигурация A/B теста."""
    id: str
    name: str
    description: str
    enabled: bool
    variants: list[dict]  # [{"name": "A", "value": "..."}, ...]
    traffic_split: list[float]  # [0.5, 0.5] - доли трафика
    start_date: str
    end_date: str | None = None
    metrics: dict[str, Any] | None = None


class AdminPanel:
    """Основной класс админ-панели."""

    def __init__(self):
        self.app = FastAPI(title="KidzordFX Admin")
        self._setup_routes()

    def _setup_routes(self):
        """Настроить маршруты."""

        @self.app.get("/")
        async def dashboard():
            """Главная страница админки - HTML интерфейс."""
            html_path = os.path.join(os.path.dirname(__file__), "admin.html")
            if os.path.exists(html_path):
                return FileResponse(html_path)
            return {
                "message": "KidzordFX Admin Panel",
                "endpoints": {
                    "stats": "/api/stats",
                    "users": "/api/users",
                    "trades": "/api/trades",
                    "health": "/api/health",
                    "gallery": "/api/gallery",
                    "analytics": "/api/analytics",
                }
            }

        @self.app.get("/api/users")
        async def get_users(x_admin_token: str = Header(...)):
            """Получить список всех пользователей."""
            self._check_auth(x_admin_token)
            users = self._load_users()
            return {"users": users, "total": len(users)}

        @self.app.get("/api/users/{user_id}")
        async def get_user(user_id: int, x_admin_token: str = Header(...)):
            """Получить информацию о пользователе."""
            self._check_auth(x_admin_token)
            users = self._load_users()
            user = users.get(str(user_id))
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            return user

        @self.app.put("/api/users/{user_id}")
        async def update_user(user_id: int, data: dict, x_admin_token: str = Header(...)):
            """Обновить данные пользователя."""
            self._check_auth(x_admin_token)
            users = self._load_users()
            if str(user_id) not in users:
                raise HTTPException(status_code=404, detail="User not found")

            users[str(user_id)].update(data)
            self._save_users(users)
            logger_config.metrics.increment("admin_user_updated")
            return {"ok": True}

        @self.app.delete("/api/users/{user_id}")
        async def delete_user(user_id: int, x_admin_token: str = Header(...)):
            """Удалить пользователя."""
            self._check_auth(x_admin_token)
            users = self._load_users()
            if str(user_id) not in users:
                raise HTTPException(status_code=404, detail="User not found")

            del users[str(user_id)]
            self._save_users(users)
            logger_config.metrics.increment("admin_user_deleted")
            return {"ok": True}

        @self.app.get("/api/stats")
        async def get_stats(x_admin_token: str = Header(...)):
            """Получить статистику системы."""
            self._check_auth(x_admin_token)
            users = self._load_users()
            trades = self._load_trades()

            # Подсчёт активных подписчиков
            now = datetime.utcnow()
            active_subs = sum(
                1 for u in users.values()
                if u.get("paid_until") and datetime.fromtimestamp(float(u["paid_until"])) > now
            )

            # Статистика сделок
            if trades:
                total_trades = len(trades)
                wins = sum(1 for t in trades if t.get("r", 0) > 0)
                total_r = sum(t.get("r", 0) for t in trades)
            else:
                total_trades = 0
                wins = 0
                total_r = 0

            return {
                "users": {
                    "total": len(users),
                    "active_subs": active_subs,
                    "trial": sum(1 for u in users.values() if u.get("is_trial", False)),
                },
                "trades": {
                    "total": total_trades,
                    "wins": wins,
                    "winrate": wins / total_trades if total_trades > 0 else 0,
                    "total_r": total_r,
                },
                "health": health.monitor.run_all(),
                "metrics": logger_config.metrics.get_all_metrics(),
            }

        @self.app.get("/api/ab-tests")
        async def get_ab_tests(x_admin_token: str = Header(...)):
            """Получить список A/B тестов."""
            self._check_auth(x_admin_token)
            tests = self._load_ab_tests()
            return {"tests": tests}

        @self.app.post("/api/ab-tests")
        async def create_ab_test(test: ABTest, x_admin_token: str = Header(...)):
            """Создать новый A/B тест."""
            self._check_auth(x_admin_token)
            tests = self._load_ab_tests()
            tests[test.id] = asdict(test)
            self._save_ab_tests(tests)
            logger_config.metrics.increment("admin_ab_test_created")
            return {"ok": True}

        @self.app.put("/api/ab-tests/{test_id}")
        async def update_ab_test(test_id: str, data: dict, x_admin_token: str = Header(...)):
            """Обновить A/B тест."""
            self._check_auth(x_admin_token)
            tests = self._load_ab_tests()
            if test_id not in tests:
                raise HTTPException(status_code=404, detail="Test not found")

            tests[test_id].update(data)
            self._save_ab_tests(tests)
            logger_config.metrics.increment("admin_ab_test_updated")
            return {"ok": True}

        @self.app.delete("/api/ab-tests/{test_id}")
        async def delete_ab_test(test_id: str, x_admin_token: str = Header(...)):
            """Удалить A/B тест."""
            self._check_auth(x_admin_token)
            tests = self._load_ab_tests()
            if test_id not in tests:
                raise HTTPException(status_code=404, detail="Test not found")

            del tests[test_id]
            self._save_ab_tests(tests)
            logger_config.metrics.increment("admin_ab_test_deleted")
            return {"ok": True}

        @self.app.post("/api/ab-tests/{test_id}/assign")
        async def assign_variant(test_id: str, user_id: int, x_admin_token: str = Header(...)):
            """Принудительно назначить вариант пользователю (для тестирования)."""
            self._check_auth(x_admin_token)
            tests = self._load_ab_tests()
            if test_id not in tests:
                raise HTTPException(status_code=404, detail="Test not found")

            # В реальной системе это бы записывалось в user profile
            # Здесь просто возвращаем вариант
            test = tests[test_id]
            variant_idx = (user_id % len(test["variants"]))
            return {"variant": test["variants"][variant_idx]}

        @self.app.get("/api/gallery")
        async def get_gallery(
            symbol: str | None = None,
            side: str | None = None,
            days: int = 30,
            x_admin_token: str = Header(...),
        ):
            """Получить галерею сделок."""
            self._check_auth(x_admin_token)
            end = datetime.utcnow()
            start = end - timedelta(days=days)

            trades, total_trades = gallery.gallery.get_trades(
                symbol=symbol,
                side=side,
                start_date=start,
                end_date=end,
                limit=100,
            )

            signals, total_signals = gallery.gallery.get_signals(
                symbol=symbol,
                side=side,
                start_date=start,
                end_date=end,
                limit=100,
            )

            stats = gallery.gallery.get_stats(symbol=symbol, start_date=start, end_date=end)

            return {
                "trades": [
                    {
                        "ts": t.ts,
                        "symbol": t.symbol,
                        "side": t.side,
                        "kind": t.kind,
                        "r": t.r,
                        "when": t.when.isoformat(),
                    }
                    for t in trades
                ],
                "total_trades": total_trades,
                "signals": [
                    {
                        "ts": s.ts,
                        "symbol": s.symbol,
                        "side": s.side,
                        "entry": s.entry,
                        "sl": s.sl,
                        "tp1": s.tp1,
                        "when": s.when.isoformat(),
                    }
                    for s in signals
                ],
                "total_signals": total_signals,
                "stats": stats,
            }

        @self.app.get("/api/analytics")
        async def get_analytics(days: int = 30, x_admin_token: str = Header(...)):
            """Получить аналитику."""
            self._check_auth(x_admin_token)
            return analytics.analytics.get_dashboard_data(days=days)

        @self.app.get("/api/gamification")
        async def get_gamification(x_admin_token: str = Header(...)):
            """Получить геймификацию."""
            self._check_auth(x_admin_token)
            leaderboard = gamification.gamification.get_leaderboard(10)
            return {"leaderboard": [asdict(s) for s in leaderboard]}

        @self.app.get("/api/ai-alerts")
        async def get_ai_alerts(hours: int = 24, x_admin_token: str = Header(...)):
            """Получить AI alerts."""
            self._check_auth(x_admin_token)
            alerts = ai_features.market_analyzer.get_recent_alerts(hours)
            return {"alerts": [asdict(a) for a in alerts]}

        @self.app.get("/api/advanced-metrics")
        async def get_advanced_metrics(days: int = 30, x_admin_token: str = Header(...)):
            """Получить продвинутые метрики."""
            self._check_auth(x_admin_token)
            metrics = advanced_analytics.advanced_analytics.calculate_advanced_metrics(days=days)
            return asdict(metrics)

        @self.app.get("/api/heatmap")
        async def get_heatmap(days: int = 30, x_admin_token: str = Header(...)):
            """Получить heatmap символов."""
            self._check_auth(x_admin_token)
            heatmap = advanced_analytics.advanced_analytics.get_symbol_heatmap(days)
            return {"heatmap": [asdict(h) for h in heatmap]}

        @self.app.get("/api/trades")
        async def get_trades(x_admin_token: str = Header(...)):
            """Получить все сделки."""
            self._check_auth(x_admin_token)
            trades = self._load_trades()
            return {"trades": trades, "total": len(trades)}

    def _check_auth(self, token: str):
        """Проверить токен админа."""
        if token != ADMIN_TOKEN:
            raise HTTPException(status_code=403, detail="Invalid admin token")

    def _load_users(self) -> dict:
        """Загрузить пользователей из файла."""
        try:
            with open(USERS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_users(self, users: dict):
        """Сохранить пользователей в файл."""
        with open(USERS_PATH, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

    def _load_trades(self) -> list[dict]:
        """Загрузить сделки из журнала."""
        try:
            with open(TRADES_PATH, encoding="utf-8") as f:
                return [json.loads(line) for line in f]
        except FileNotFoundError:
            return []

    def _load_ab_tests(self) -> dict:
        """Загрузить A/B тесты."""
        try:
            with open(AB_TESTS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_ab_tests(self, tests: dict):
        """Сохранить A/B тесты."""
        with open(AB_TESTS_PATH, "w", encoding="utf-8") as f:
            json.dump(tests, f, ensure_ascii=False, indent=2)


def get_variant_for_user(test_id: str, user_id: int) -> dict | None:
    """Получить вариант A/B теста для пользователя (для использования в боте)."""
    tests = {}
    try:
        with open(AB_TESTS_PATH, encoding="utf-8") as f:
            tests = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    if test_id not in tests:
        return None

    test = tests[test_id]
    if not test.get("enabled", False):
        return None

    # Проверяем даты
    start = datetime.fromisoformat(test["start_date"])
    if datetime.utcnow() < start:
        return None

    if test.get("end_date"):
        end = datetime.fromisoformat(test["end_date"])
        if datetime.utcnow() > end:
            return None

    # Детерминированный выбор варианта по user_id
    variant_idx = (user_id % len(test["variants"]))
    return test["variants"][variant_idx]


# Создаём директорию для шаблонов
os.makedirs("admin_templates", exist_ok=True)

# HTML шаблон для дашборда
dashboard_html = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KidzordFX Admin</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        .header { background: #2563eb; color: white; padding: 20px; margin-bottom: 20px; border-radius: 8px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 20px; }
        .card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .card h3 { color: #1e40af; margin-bottom: 10px; }
        .stat-value { font-size: 2em; font-weight: bold; color: #2563eb; }
        .stat-label { color: #6b7280; font-size: 0.9em; }
        .status-healthy { color: #10b981; }
        .status-degraded { color: #f59e0b; }
        .status-unhealthy { color: #ef4444; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>KidzordFX Admin Panel</h1>
            <p>Управление пользователями, мониторинг, A/B тесты</p>
        </div>

        <div class="grid">
            <div class="card">
                <h3>Пользователи</h3>
                <div class="stat-value" id="users-total">-</div>
                <div class="stat-label">Всего</div>
            </div>
            <div class="card">
                <h3>Активные подписки</h3>
                <div class="stat-value" id="users-active">-</div>
                <div class="stat-label">Премиум</div>
            </div>
            <div class="card">
                <h3>Сделки</h3>
                <div class="stat-value" id="trades-total">-</div>
                <div class="stat-label">Всего</div>
            </div>
            <div class="card">
                <h3>Winrate</h3>
                <div class="stat-value" id="trades-winrate">-</div>
                <div class="stat-label">% прибыльных</div>
            </div>
        </div>

        <div class="card">
            <h3>Health Status</h3>
            <div id="health-status">Загрузка...</div>
        </div>
    </div>

    <script>
        async function loadStats() {
            const token = localStorage.getItem('adminToken') || prompt('Enter admin token:');
            if (!token) return;
            localStorage.setItem('adminToken', token);

            try {
                const res = await fetch('/api/stats', {
                    headers: { 'X-Admin-Token': token }
                });
                const data = await res.json();

                document.getElementById('users-total').textContent = data.users.total;
                document.getElementById('users-active').textContent = data.users.active_subs;
                document.getElementById('trades-total').textContent = data.trades.total;
                document.getElementById('trades-winrate').textContent = (data.trades.winrate * 100).toFixed(1) + '%';

                const healthDiv = document.getElementById('health-status');
                const health = data.health;
                const statusClass = `status-${health.status}`;
                healthDiv.innerHTML = `
                    <span class="${statusClass}">${health.status.toUpperCase()}</span>
                    <ul>
                        ${health.checks.map(c => `
                            <li>
                                <strong>${c.name}</strong>: ${c.status}
                                (${c.duration_ms.toFixed(0)}ms) - ${c.message}
                            </li>
                        `).join('')}
                    </ul>
                `;
            } catch (e) {
                console.error(e);
                alert('Failed to load stats');
            }
        }

        loadStats();
        setInterval(loadStats, 30000); // Обновлять каждые 30 секунд
    </script>
</body>
</html>
"""

with open("admin_templates/dashboard.html", "w", encoding="utf-8") as f:
    f.write(dashboard_html)


if __name__ == "__main__":
    import uvicorn
    admin = AdminPanel()
    print(f"Админ-панель запущена на http://localhost:8081")
    print(f"Токен: {ADMIN_TOKEN}")
    uvicorn.run(admin.app, host="0.0.0.0", port=8081)
