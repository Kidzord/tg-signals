"""AI функции: анализ настроения рынка, smart alerts.

Анализирует:
- Настроение рынка через технические индикаторы
- Аномальную активность (volatility spikes)
- Smart alerts на основе паттернов
- Предсказание волатильности
"""
from __future__ import annotations

import json
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np

AI_ALERTS_PATH = os.path.join(os.path.dirname(__file__), "ai_alerts.json")


@dataclass
class MarketSentiment:
    """Настроение рынка."""
    timestamp: float
    symbol: str
    trend: str  # "bullish", "bearish", "neutral"
    strength: float  # 0-1, насколько сильный тренд
    volatility: str  # "low", "normal", "high", "extreme"
    momentum: float  # индикатор импульса
    volume_anomaly: bool  # аномальный объём?


@dataclass
class SmartAlert:
    """Smart alert - уведомление о важном событии."""
    id: str
    timestamp: float
    symbol: str
    alert_type: str  # "volatility_spike", "trend_reversal", "breakout", "volume_surge"
    severity: str  # "low", "medium", "high", "critical"
    message: str
    data: dict[str, Any]


class MarketAnalyzer:
    """Анализатор рынка с AI функциями."""

    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self._history: dict[str, deque] = {}
        self._alerts: list[SmartAlert] = []
        self._load_alerts()

    def _load_alerts(self):
        """Загрузить историю алертов."""
        try:
            with open(AI_ALERTS_PATH, encoding="utf-8") as f:
                data = json.load(f)
                self._alerts = [SmartAlert(**a) for a in data]
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save_alerts(self):
        """Сохранить алерты."""
        # Храним только последние 1000
        self._alerts = self._alerts[-1000:]
        data = [asdict(a) for a in self._alerts]
        with open(AI_ALERTS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add_price_data(self, symbol: str, ohlc: dict):
        """Добавить OHLC данные для анализа."""
        if symbol not in self._history:
            self._history[symbol] = deque(maxlen=self.window_size)

        self._history[symbol].append({
            "timestamp": ohlc["time"],
            "open": ohlc["open"],
            "high": ohlc["high"],
            "low": ohlc["low"],
            "close": ohlc["close"],
            "volume": ohlc.get("volume", 0),
        })

    def analyze_sentiment(self, symbol: str) -> MarketSentiment | None:
        """Анализировать настроение рынка."""
        if symbol not in self._history or len(self._history[symbol]) < 20:
            return None

        data = list(self._history[symbol])
        closes = np.array([d["close"] for d in data])
        volumes = np.array([d["volume"] for d in data])

        # EMA для тренда
        ema_short = self._ema(closes, 9)
        ema_long = self._ema(closes, 21)

        # Определяем тренд
        if ema_short[-1] > ema_long[-1]:
            trend = "bullish"
        elif ema_short[-1] < ema_long[-1]:
            trend = "bearish"
        else:
            trend = "neutral"

        # Сила тренда (разница между EMA)
        strength = abs(ema_short[-1] - ema_long[-1]) / closes[-1]
        strength = min(strength * 100, 1.0)  # нормализация 0-1

        # Волатильность (ATR)
        atr = self._atr(data)
        avg_price = np.mean(closes)
        volatility_pct = (atr / avg_price) * 100

        if volatility_pct < 0.2:
            volatility = "low"
        elif volatility_pct < 0.5:
            volatility = "normal"
        elif volatility_pct < 1.0:
            volatility = "high"
        else:
            volatility = "extreme"

        # Импульс (RSI упрощённый)
        momentum = self._momentum(closes)

        # Аномалия объёма
        vol_mean = np.mean(volumes)
        vol_std = np.std(volumes)
        volume_anomaly = volumes[-1] > vol_mean + 2 * vol_std

        return MarketSentiment(
            timestamp=datetime.utcnow().timestamp(),
            symbol=symbol,
            trend=trend,
            strength=strength,
            volatility=volatility,
            volatility_pct=volatility_pct,
            momentum=momentum,
            volume_anomaly=volume_anomaly,
        )

    def _ema(self, data: np.ndarray, period: int) -> np.ndarray:
        """Экспоненциальная скользящая средняя."""
        alpha = 2 / (period + 1)
        ema = np.zeros_like(data)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = alpha * data[i] + (1 - alpha) * ema[i-1]
        return ema

    def _atr(self, data: list, period: int = 14) -> float:
        """Average True Range."""
        trs = []
        for i in range(1, len(data)):
            high = data[i]["high"]
            low = data[i]["low"]
            prev_close = data[i-1]["close"]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
        if not trs:
            return 0.0
        return np.mean(trs[-period:])

    def _momentum(self, closes: np.ndarray, period: int = 14) -> float:
        """Импульс цены."""
        if len(closes) < period:
            return 0.0
        return (closes[-1] - closes[-period]) / closes[-period]

    def detect_smart_alerts(self, symbol: str) -> list[SmartAlert]:
        """Обнаружить smart alerts."""
        alerts = []
        sentiment = self.analyze_sentiment(symbol)

        if not sentiment:
            return alerts

        # 1. Волатильность spike
        if sentiment.volatility == "extreme":
            alert = SmartAlert(
                id=f"vol_spike_{symbol}_{int(time.time())}",
                timestamp=datetime.utcnow().timestamp(),
                symbol=symbol,
                alert_type="volatility_spike",
                severity="high",
                message=f"⚠️ Экстремальная волатильность на {symbol}: {sentiment.volatility_pct:.2f}%",
                data={"volatility_pct": sentiment.volatility_pct}
            )
            alerts.append(alert)

        # 2. Объём аномалия
        if sentiment.volume_anomaly:
            alert = SmartAlert(
                id=f"vol_surge_{symbol}_{int(time.time())}",
                timestamp=datetime.utcnow().timestamp(),
                symbol=symbol,
                alert_type="volume_surge",
                severity="medium",
                message=f"📊 Аномальный объём на {symbol}",
                data={"volume_ratio": 1.0}
            )
            alerts.append(alert)

        # 3. Смена тренда
        data = list(self._history[symbol])
        if len(data) >= 20:
            ema_short = self._ema(np.array([d["close"] for d in data]), 9)
            ema_long = self._ema(np.array([d["close"] for d in data]), 21)

            # Проверяем пересечение EMA
            if ema_short[-2] < ema_long[-2] and ema_short[-1] > ema_long[-1]:
                alert = SmartAlert(
                    id=f"trend_bull_{symbol}_{int(time.time())}",
                    timestamp=datetime.utcnow().timestamp(),
                    symbol=symbol,
                    alert_type="trend_reversal",
                    severity="high",
                    message=f"🚀 Смена тренда на бычий на {symbol}",
                    data={"new_trend": "bullish"}
                )
                alerts.append(alert)
            elif ema_short[-2] > ema_long[-2] and ema_short[-1] < ema_long[-1]:
                alert = SmartAlert(
                    id=f"trend_bear_{symbol}_{int(time.time())}",
                    timestamp=datetime.utcnow().timestamp(),
                    symbol=symbol,
                    alert_type="trend_reversal",
                    severity="high",
                    message=f"📉 Смена тренда на медвежий на {symbol}",
                    data={"new_trend": "bearish"}
                )
                alerts.append(alert)

        # Сохраняем алерты
        for alert in alerts:
            self._alerts.append(alert)

        if alerts:
            self._save_alerts()

        return alerts

    def get_recent_alerts(self, hours: int = 24) -> list[SmartAlert]:
        """Получить алерты за последние N часов."""
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        return [a for a in self._alerts if datetime.fromtimestamp(a.timestamp) >= cutoff]

    def predict_volatility(self, symbol: str, periods: int = 5) -> list[float]:
        """Предсказать волатильность на следующие периоды (простая модель)."""
        if symbol not in self._history or len(self._history[symbol]) < 20:
            return []

        data = list(self._history[symbol])
        atrs = []

        for i in range(20, len(data)):
            atr = self._atr(data[i-20:i])
            atrs.append(atr)

        if not atrs:
            return []

        # Простое предсказание: последнее значение с небольшим шумом
        last_atr = atrs[-1]
        predictions = [last_atr * (1 + np.random.normal(0, 0.1)) for _ in range(periods)]
        return predictions


# Глобальный инстанс
market_analyzer = MarketAnalyzer()
