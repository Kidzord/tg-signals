"""Расширенная аналитика: heatmap, correlation matrix, sentiment analysis.

Предоставляет:
- Heatmap символов (какие инструменты активны)
- Correlation matrix (корреляция между инструментами)
- Sentiment analysis (настроение рынка)
- Advanced metrics (Expectancy, SQN, Calmar Ratio)
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np

TRADES_PATH = os.path.join(os.path.dirname(__file__), "trades.jsonl")


@dataclass
class AdvancedMetrics:
    """Продвинутые метрики."""
    expectancy: float  # математическое ожидание (средний R на сделку)
    sqn: float  # System Quality Number
    calmar_ratio: float  # max drawdown / годовая прибыль
    kelly_criterion: float  # оптимальный размер позиции
    sortino_ratio: float  # Downside risk adjusted return
    ulcer_index: float  # измерение просадок


@dataclass
class SymbolHeatmap:
    """Данные для heatmap символов."""
    symbol: str
    total_trades: int
    winrate: float
    total_r: float
    avg_r: float
    best_r: float
    worst_r: float
    recent_activity: float  # активность за последние 7 дней


class AdvancedAnalytics:
    """Расширенная аналитика."""

    def __init__(self):
        self._trades: list[dict] = []
        self._load_trades()

    def _load_trades(self):
        """Загрузить сделки."""
        try:
            with open(TRADES_PATH, encoding="utf-8") as f:
                for line in f:
                    try:
                        self._trades.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            pass

    def calculate_advanced_metrics(
        self,
        symbol: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> AdvancedMetrics:
        """Рассчитать продвинутые метрики."""
        trades = self._filter_trades(symbol, start_date, end_date)

        if not trades:
            return AdvancedMetrics(
                expectancy=0.0, sqn=0.0, calmar_ratio=0.0,
                kelly_criterion=0.0, sortino_ratio=0.0, ulcer_index=0.0
            )

        r_values = [t["r"] for t in trades]
        n = len(r_values)

        # Expectancy (математическое ожидание)
        expectancy = np.mean(r_values)

        # SQN (System Quality Number)
        if np.std(r_values) > 0:
            sqn = (np.sqrt(n) * expectancy) / np.std(r_values)
        else:
            sqn = 0.0

        # Calmar Ratio (max drawdown / годовая прибыль)
        cumulative = np.cumsum(r_values)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = running_max - cumulative
        max_dd = np.max(drawdown) if len(drawdown) > 0 else 0.0

        # Годовая прибыль (приблизительно, assuming 365 дней)
        total_r = cumulative[-1] if len(cumulative) > 0 else 0.0
        annual_return = total_r * (365 / (end_date - start_date).days if start_date and end_date else 1)
        calmar_ratio = annual_return / max_dd if max_dd > 0 else 0.0

        # Kelly Criterion (оптимальный размер позиции)
        wins = [r for r in r_values if r > 0]
        losses = [abs(r) for r in r_values if r < 0]

        if wins and losses:
            avg_win = np.mean(wins)
            avg_loss = np.mean(losses)
            win_rate = len(wins) / n
            kelly = win_rate - ((1 - win_rate) / (avg_win / avg_loss))
            kelly = max(0, kelly)  # не может быть отрицательным
        else:
            kelly = 0.0

        # Sortino Ratio (только downside risk)
        downside = [r for r in r_values if r < 0]
        if len(downside) > 1:
            sortino = np.mean(r_values) / np.std(downside) if np.std(downside) > 0 else 0.0
        else:
            sortino = 0.0

        # Ulcer Index (измерение глубины и длительности просадок)
        if len(cumulative) > 0:
            peaks = np.maximum.accumulate(cumulative)
            dd = (peaks - cumulative) / peaks * 100
            ulcer_index = np.sqrt(np.mean(dd ** 2)) if len(dd) > 0 else 0.0
        else:
            ulcer_index = 0.0

        return AdvancedMetrics(
            expectancy=expectancy,
            sqn=sqn,
            calmar_ratio=calmar_ratio,
            kelly_criterion=kelly,
            sortino_ratio=sortino,
            ulcer_index=ulcer_index,
        )

    def get_symbol_heatmap(self, days: int = 30) -> list[SymbolHeatmap]:
        """Получить данные для heatmap символов."""
        end = datetime.utcnow()
        start = end - timedelta(days=days)

        symbols = set(t.get("symbol", "") for t in self._trades)
        heatmaps = []

        for symbol in symbols:
            trades = self._filter_trades(symbol, start, end)

            if not trades:
                continue

            r_values = [t["r"] for t in trades]
            wins = [r for r in r_values if r > 0]

            # Активность за последние 7 дней
            recent_start = end - timedelta(days=7)
            recent_trades = [t for t in trades if datetime.fromtimestamp(t["ts"]) >= recent_start]
            recent_activity = len(recent_trades) / 7.0  # среднее сделок в день

            heatmaps.append(SymbolHeatmap(
                symbol=symbol,
                total_trades=len(trades),
                winrate=len(wins) / len(trades) if trades else 0.0,
                total_r=sum(r_values),
                avg_r=np.mean(r_values) if r_values else 0.0,
                best_r=max(r_values) if r_values else 0.0,
                worst_r=min(r_values) if r_values else 0.0,
                recent_activity=recent_activity,
            ))

        return heatmaps

    def calculate_correlation_matrix(self, symbols: list[str], days: int = 30) -> dict:
        """Рассчитать корреляционную матрицу между символами."""
        # Для реальной корреляции нужны цены, не только результаты сделок
        # Здесь упрощённая версия на основе R-значений сделок

        end = datetime.utcnow()
        start = end - timedelta(days=days)

        symbol_data = {}
        for symbol in symbols:
            trades = self._filter_trades(symbol, start, end)
            if trades:
                symbol_data[symbol] = [t["r"] for t in trades]

        if len(symbol_data) < 2:
            return {}

        # Выравниваем длины (минимальная длина)
        min_len = min(len(v) for v in symbol_data.values())
        if min_len < 10:
            return {}

        matrix = {}
        for s1 in symbols:
            if s1 not in symbol_data:
                continue
            for s2 in symbols:
                if s2 not in symbol_data or s1 == s2:
                    continue

                data1 = symbol_data[s1][:min_len]
                data2 = symbol_data[s2][:min_len]

                if np.std(data1) > 0 and np.std(data2) > 0:
                    corr = np.corrcoef(data1, data2)[0, 1]
                    matrix[f"{s1}_{s2}"] = corr

        return matrix

    def get_sentiment_overview(self, days: int = 7) -> dict:
        """Обзор настроения рынка."""
        end = datetime.utcnow()
        start = end - timedelta(days=days)

        trades = self._filter_trades(None, start, end)

        if not trades:
            return {
                "total_trades": 0,
                "bullish_sentiment": 0.5,
                "avg_r": 0.0,
                "most_profitable": None,
                "most_active": None,
            }

        r_values = [t["r"] for t in trades]
        longs = [t for t in trades if t.get("side") == "LONG"]
        shorts = [t for t in trades if t.get("side") == "SHORT"]

        # Настроение (соотношение лонгов/шортов)
        total = len(longs) + len(shorts)
        bullish_sentiment = len(longs) / total if total > 0 else 0.5

        # Самый прибыльный символ
        symbol_profits = defaultdict(list)
        for t in trades:
            symbol_profits[t.get("symbol", "")].append(t["r"])

        symbol_totals = {s: sum(v) for s, v in symbol_profits.items()}
        most_profitable = max(symbol_totals, key=symbol_totals.get) if symbol_totals else None

        # Самый активный символ
        symbol_counts = {s: len(v) for s, v in symbol_profits.items()}
        most_active = max(symbol_counts, key=symbol_counts.get) if symbol_counts else None

        return {
            "total_trades": len(trades),
            "bullish_sentiment": bullish_sentiment,
            "avg_r": np.mean(r_values) if r_values else 0.0,
            "most_profitable": most_profitable,
            "most_active": most_active,
            "symbol_profits": symbol_totals,
        }

    def _filter_trades(
        self,
        symbol: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict]:
        """Отфильтровать сделки."""
        trades = self._trades

        if symbol:
            trades = [t for t in trades if t.get("symbol") == symbol]

        if start_date:
            trades = [t for t in trades if datetime.fromtimestamp(t["ts"]) >= start_date]

        if end_date:
            trades = [t for t in trades if datetime.fromtimestamp(t["ts"]) <= end_date]

        return trades

    def get_hourly_distribution(self, days: int = 30) -> dict[int, int]:
        """Распределение сделок по часам суток."""
        end = datetime.utcnow()
        start = end - timedelta(days=days)

        trades = self._filter_trades(None, start, end)
        hourly = defaultdict(int)

        for t in trades:
            hour = datetime.fromtimestamp(t["ts"]).hour
            hourly[hour] += 1

        return dict(hourly)

    def get_day_of_week_distribution(self, days: int = 30) -> dict[str, int]:
        """Распределение сделок по дням недели."""
        end = datetime.utcnow()
        start = end - timedelta(days=days)

        trades = self._filter_trades(None, start, end)
        daily = defaultdict(int)
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        for t in trades:
            day = day_names[datetime.fromtimestamp(t["ts"]).weekday()]
            daily[day] += 1

        return dict(daily)


# Глобальный инстанс
advanced_analytics = AdvancedAnalytics()
