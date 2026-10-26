"""Аналитика в реальном времени: дашборд с графиками.

Предоставляет:
- Winrate и прибыльность
- Profit Factor и Max Drawdown
- Распределение сделок по дням недели/времени суток
- Графики баланса и equity
- Сравнение с прошлым периодом
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np

import config as cfg
import gallery
import logger_config

TRADES_PATH = os.path.join(os.path.dirname(__file__), "trades.jsonl")
USERS_PATH = os.path.join(os.path.dirname(__file__), "users.json")


@dataclass
class PerformanceMetrics:
    """Метрики производительности."""
    total_trades: int
    wins: int
    losses: int
    winrate: float
    total_r: float
    avg_r: float
    best_r: float
    worst_r: float
    profit_factor: float
    max_drawdown: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float


@dataclass
class TimeDistribution:
    """Распределение по времени."""
    by_day: dict[str, int]  # {"Mon": 10, "Tue": 15, ...}
    by_hour: dict[int, int]  # {0: 5, 1: 3, ...}
    by_tf: dict[str, int]  # {"15": 50, "60": 20, ...}


class RealTimeAnalytics:
    """Аналитика в реальном времени."""

    def __init__(self):
        self._trades: list[dict] = []
        self._users: dict = {}
        self._load_data()

    def _load_data(self):
        """Загрузить данные."""
        # Сделки
        try:
            with open(TRADES_PATH, encoding="utf-8") as f:
                for line in f:
                    try:
                        self._trades.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            pass

        # Пользователи
        try:
            with open(USERS_PATH, encoding="utf-8") as f:
                self._users = json.load(f)
        except FileNotFoundError:
            pass

    def calculate_metrics(
        self,
        symbol: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> PerformanceMetrics:
        """Рассчитать метрики производительности."""
        # Фильтруем сделки
        trades = self._trades
        if symbol:
            trades = [t for t in trades if t.get("symbol") == symbol]

        if start_date:
            trades = [t for t in trades if datetime.fromtimestamp(t["ts"]) >= start_date]

        if end_date:
            trades = [t for t in trades if datetime.fromtimestamp(t["ts"]) <= end_date]

        if not trades:
            return PerformanceMetrics(
                total_trades=0, wins=0, losses=0, winrate=0,
                total_r=0, avg_r=0, best_r=0, worst_r=0,
                profit_factor=0, max_drawdown=0, max_drawdown_pct=0,
                sharpe_ratio=0, sortino_ratio=0,
            )

        # Базовые метрики
        r_values = [t["r"] for t in trades]
        wins = [r for r in r_values if r > 0]
        losses = [r for r in r_values if r < 0]

        total_trades = len(trades)
        total_wins = len(wins)
        total_losses = len(losses)
        winrate = total_wins / total_trades if total_trades > 0 else 0

        total_r = sum(r_values)
        avg_r = total_r / total_trades if total_trades > 0 else 0
        best_r = max(r_values) if r_values else 0
        worst_r = min(r_values) if r_values else 0

        # Profit Factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0

        # Max Drawdown
        cumulative = np.cumsum(r_values)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = running_max - cumulative
        max_drawdown = np.max(drawdown) if len(drawdown) > 0 else 0
        max_drawdown_pct = (max_drawdown / np.max(running_max) * 100) if np.max(running_max) > 0 else 0

        # Sharpe Ratio (упрощённый, без risk-free rate)
        if len(r_values) > 1:
            sharpe_ratio = np.mean(r_values) / np.std(r_values) if np.std(r_values) > 0 else 0
        else:
            sharpe_ratio = 0

        # Sortino Ratio (только downside risk)
        downside = [r for r in r_values if r < 0]
        if len(downside) > 1:
            sortino_ratio = np.mean(r_values) / np.std(downside) if np.std(downside) > 0 else 0
        else:
            sortino_ratio = 0

        return PerformanceMetrics(
            total_trades=total_trades,
            wins=total_wins,
            losses=total_losses,
            winrate=winrate,
            total_r=total_r,
            avg_r=avg_r,
            best_r=best_r,
            worst_r=worst_r,
            profit_factor=profit_factor,
            max_drawdown=max_drawdown,
            max_drawdown_pct=max_drawdown_pct,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
        )

    def calculate_time_distribution(self) -> TimeDistribution:
        """Рассчитать распределение по времени."""
        by_day = defaultdict(int)
        by_hour = defaultdict(int)
        by_tf = defaultdict(int)

        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        for trade in self._trades:
            dt_obj = datetime.fromtimestamp(trade["ts"])

            by_day[day_names[dt_obj.weekday()]] += 1
            by_hour[dt_obj.hour] += 1
            by_tf[trade.get("tf", "15")] += 1

        return TimeDistribution(
            by_day=dict(by_day),
            by_hour=dict(by_hour),
            by_tf=dict(by_tf),
        )

    def compare_periods(self, days: int = 30) -> dict[str, Any]:
        """Сравнить текущий период с предыдущим."""
        now = datetime.utcnow()
        current_start = now - timedelta(days=days)
        previous_start = current_start - timedelta(days=days)

        current_metrics = self.calculate_metrics(start_date=current_start, end_date=now)
        previous_metrics = self.calculate_metrics(start_date=previous_start, end_date=current_start)

        return {
            "current": {
                "total_trades": current_metrics.total_trades,
                "winrate": current_metrics.winrate,
                "total_r": current_metrics.total_r,
                "profit_factor": current_metrics.profit_factor,
            },
            "previous": {
                "total_trades": previous_metrics.total_trades,
                "winrate": previous_metrics.winrate,
                "total_r": previous_metrics.total_r,
                "profit_factor": previous_metrics.profit_factor,
            },
            "change": {
                "total_trades": current_metrics.total_trades - previous_metrics.total_trades,
                "winrate": current_metrics.winrate - previous_metrics.winrate,
                "total_r": current_metrics.total_r - previous_metrics.total_r,
                "profit_factor": current_metrics.profit_factor - previous_metrics.profit_factor,
            },
        }

    def get_balance_curve(self, days: int = 30) -> list[dict]:
        """Получить кривую баланса."""
        end = datetime.utcnow()
        start = end - timedelta(days=days)

        trades = [
            t for t in self._trades
            if start <= datetime.fromtimestamp(t["ts"]) <= end
        ]
        trades.sort(key=lambda t: t["ts"])

        balance = 0
        curve = []
        for trade in trades:
            balance += trade["r"]
            curve.append({
                "ts": trade["ts"],
                "datetime": datetime.fromtimestamp(trade["ts"]).isoformat(),
                "balance": balance,
                "r": trade["r"],
            })

        return curve

    def get_user_stats(self) -> dict[str, Any]:
        """Получить статистику пользователей."""
        now = datetime.utcnow()

        total_users = len(self._users)
        active_users = sum(
            1 for u in self._users.values()
            if u.get("paid_until") and datetime.fromisoformat(u["paid_until"]) > now
        )
        trial_users = sum(1 for u in self._users.values() if u.get("is_trial", False))

        # Новые пользователи за последние 30 дней
        thirty_days_ago = now - timedelta(days=30)
        new_users = sum(
            1 for u in self._users.values()
            if u.get("created_at") and datetime.fromisoformat(u["created_at"]) >= thirty_days_ago
        )

        return {
            "total": total_users,
            "active": active_users,
            "trial": trial_users,
            "new_last_30d": new_users,
        }

    def get_dashboard_data(self, days: int = 30) -> dict[str, Any]:
        """Получить все данные для дашборда."""
        end = datetime.utcnow()
        start = end - timedelta(days=days)

        return {
            "period": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
            "metrics": self.calculate_metrics(start_date=start, end_date=end),
            "time_distribution": self.calculate_time_distribution(),
            "comparison": self.compare_periods(days=days),
            "balance_curve": self.get_balance_curve(days=days),
            "user_stats": self.get_user_stats(),
            "symbol_stats": {
                symbol: self.calculate_metrics(symbol=symbol, start_date=start, end_date=end)
                for symbol in set(t.get("symbol") for t in self._trades)
            },
        }


# Глобальный инстанс аналитики
analytics = RealTimeAnalytics()


def render_dashboard_html(days: int = 30) -> str:
    """Сгенерировать HTML дашборда."""
    data = analytics.get_dashboard_data(days=days)

    metrics = data["metrics"]
    comparison = data["comparison"]
    time_dist = data["time_distribution"]
    user_stats = data["user_stats"]

    # Формируем данные для графиков
    balance_points = data["balance_curve"]
    balance_json = json.dumps(balance_points)

    # Распределение по дням
    day_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    day_values = [time_dist.by_day.get(d, 0) for d in day_order]

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Аналитика - KidzordFX</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; }}
        .container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #2563eb, #7c3aed); color: white; padding: 30px; margin-bottom: 20px; border-radius: 12px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 20px; }}
        .card {{ background: white; padding: 20px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
        .card h3 {{ color: #1e40af; margin-bottom: 15px; font-size: 1.1em; }}
        .metric-value {{ font-size: 2em; font-weight: bold; color: #2563eb; }}
        .metric-label {{ color: #6b7280; font-size: 0.9em; margin-top: 5px; }}
        .metric-change {{ font-size: 0.9em; margin-top: 5px; }}
        .change-positive {{ color: #10b981; }}
        .change-negative {{ color: #ef4444; }}
        .chart-container {{ height: 300px; margin-top: 15px; }}
        .section {{ background: white; padding: 25px; border-radius: 12px; margin-bottom: 20px; }}
        .section h2 {{ color: #1e40af; margin-bottom: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 Аналитика в реальном времени</h1>
            <p>Период: {days} дней ({data['period']['start'][:10]} - {data['period']['end'][:10]})</p>
        </div>

        <div class="grid">
            <div class="card">
                <h3>Всего сделок</h3>
                <div class="metric-value">{metrics.total_trades}</div>
                <div class="metric-label">За период</div>
                <div class="metric-change {'change-positive' if comparison['change']['total_trades'] >= 0 else 'change-negative'}">
                    {comparison['change']['total_trades']:+d} vs прошлый период
                </div>
            </div>

            <div class="card">
                <h3>Winrate</h3>
                <div class="metric-value">{metrics.winrate:.1%}</div>
                <div class="metric-label">Прибыльных сделок</div>
                <div class="metric-change {'change-positive' if comparison['change']['winrate'] >= 0 else 'change-negative'}">
                    {comparison['change']['winrate']:+.1%} vs прошлый период
                </div>
            </div>

            <div class="card">
                <h3>Сумма R</h3>
                <div class="metric-value">{metrics.total_r:+.2f}</div>
                <div class="metric-label">Общий результат</div>
                <div class="metric-change {'change-positive' if comparison['change']['total_r'] >= 0 else 'change-negative'}">
                    {comparison['change']['total_r']:+.2f}R vs прошлый период
                </div>
            </div>

            <div class="card">
                <h3>Profit Factor</h3>
                <div class="metric-value">{metrics.profit_factor:.2f}</div>
                <div class="metric-label">Gross Profit / Gross Loss</div>
                <div class="metric-change {'change-positive' if comparison['change']['profit_factor'] >= 0 else 'change-negative'}">
                    {comparison['change']['profit_factor']:+.2f} vs прошлый период
                </div>
            </div>

            <div class="card">
                <h3>Max Drawdown</h3>
                <div class="metric-value">{metrics.max_drawdown:.2f}R</div>
                <div class="metric-label">{metrics.max_drawdown_pct:.1f}% от пика</div>
            </div>

            <div class="card">
                <h3>Sharpe Ratio</h3>
                <div class="metric-value">{metrics.sharpe_ratio:.2f}</div>
                <div class="metric-label">Risk-adjusted return</div>
            </div>
        </div>

        <div class="section">
            <h2>📈 Кривая баланса</h2>
            <div class="chart-container">
                <canvas id="balanceChart"></canvas>
            </div>
        </div>

        <div class="section">
            <h2>📅 Распределение по дням недели</h2>
            <div class="chart-container">
                <canvas id="dayChart"></canvas>
            </div>
        </div>

        <div class="section">
            <h2>👥 Статистика пользователей</h2>
            <div class="grid">
                <div class="card">
                    <h3>Всего пользователей</h3>
                    <div class="metric-value">{user_stats['total']}</div>
                </div>
                <div class="card">
                    <h3>Активные подписки</h3>
                    <div class="metric-value">{user_stats['active']}</div>
                </div>
                <div class="card">
                    <h3>Пробные</h3>
                    <div class="metric-value">{user_stats['trial']}</div>
                </div>
                <div class="card">
                    <h3>Новых (30д)</h3>
                    <div class="metric-value">{user_stats['new_last_30d']}</div>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Кривая баланса
        const balanceCtx = document.getElementById('balanceChart').getContext('2d');
        const balanceData = {balance_json};
        new Chart(balanceCtx, {{
            type: 'line',
            data: {{
                labels: balanceData.map(d => d.datetime.slice(5, 16)),
                datasets: [{{
                    label: 'Баланс (R)',
                    data: balanceData.map(d => d.balance),
                    borderColor: '#2563eb',
                    backgroundColor: 'rgba(37, 99, 235, 0.1)',
                    fill: true,
                    tension: 0.4,
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }}
                }}
            }}
        }});

        // Распределение по дням
        const dayCtx = document.getElementById('dayChart').getContext('2d');
        new Chart(dayCtx, {{
            type: 'bar',
            data: {{
                labels: {day_labels},
                datasets: [{{
                    label: 'Сделок',
                    data: {day_values},
                    backgroundColor: '#7c3aed',
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
            }}
        }});
    </script>
</body>
</html>
"""

    return html


if __name__ == "__main__":
    # Тест: сгенерировать HTML дашборда
    html = render_dashboard_html(days=30)
    output_path = os.path.join(os.path.dirname(__file__), "analytics.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Дашборд сохранён в {output_path}")
