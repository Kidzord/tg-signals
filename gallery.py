"""Галерея сделок: история с фильтрами и карточками.

Позволяет просматривать историю сигналов и сделок с фильтрами по:
- Символу
- Периоду времени
- Стороне (LONG/SHORT)
- Результату (TP/SL/Time)
- Таймфрейму
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import config as cfg
import logger_config

TRADES_PATH = os.path.join(os.path.dirname(__file__), "trades.jsonl")
SIGNALS_PATH = os.path.join(os.path.dirname(__file__), "signals_history.jsonl")


@dataclass
class Trade:
    """Сделка из журнала."""
    ts: float
    symbol: str
    side: str
    kind: str  # tp1, tp2, tp3, sl, be, time
    r: float

    @property
    def when(self) -> datetime:
        return datetime.fromtimestamp(self.ts)

    @property
    def is_profit(self) -> bool:
        return self.r > 0

    @property
    def is_loss(self) -> bool:
        return self.r < 0


@dataclass
class Signal:
    """Сигнал с карточкой."""
    ts: float
    symbol: str
    side: str
    entry: float
    sl: float
    tp1: float
    tp2: float | None
    tp3: float | None
    tf: str
    image_path: str
    caption: str

    @property
    def when(self) -> datetime:
        return datetime.fromtimestamp(self.ts)


class TradeGallery:
    """Галерея сделок."""

    def __init__(self):
        self._trades: list[Trade] = []
        self._signals: list[Signal] = []
        self._load_data()

    def _load_data(self):
        """Загрузить данные из файлов."""
        # Загружаем сделки
        try:
            with open(TRADES_PATH, encoding="utf-8") as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        self._trades.append(Trade(
                            ts=data["ts"],
                            symbol=data["symbol"],
                            side=data["side"],
                            kind=data["kind"],
                            r=data["r"],
                        ))
                    except (json.JSONDecodeError, KeyError):
                        continue
        except FileNotFoundError:
            pass

        # Загружаем сигналы
        try:
            with open(SIGNALS_PATH, encoding="utf-8") as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        self._signals.append(Signal(
                            ts=data["ts"],
                            symbol=data["symbol"],
                            side=data["side"],
                            entry=data["entry"],
                            sl=data["sl"],
                            tp1=data["tp1"],
                            tp2=data.get("tp2"),
                            tp3=data.get("tp3"),
                            tf=data.get("tf", "15"),
                            image_path=data.get("image_path", ""),
                            caption=data.get("caption", ""),
                        ))
                    except (json.JSONDecodeError, KeyError):
                        continue
        except FileNotFoundError:
            pass

        # Сортируем по времени (новые сверху)
        self._trades.sort(key=lambda t: t.ts, reverse=True)
        self._signals.sort(key=lambda s: s.ts, reverse=True)

    def get_trades(
        self,
        symbol: str | None = None,
        side: str | None = None,
        kind: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Trade]:
        """Получить сделки с фильтрами."""
        filtered = self._trades

        if symbol:
            filtered = [t for t in filtered if t.symbol.upper() == symbol.upper()]

        if side:
            filtered = [t for t in filtered if t.side.upper() == side.upper()]

        if kind:
            filtered = [t for t in filtered if t.kind == kind]

        if start_date:
            filtered = [t for t in filtered if t.when >= start_date]

        if end_date:
            filtered = [t for t in filtered if t.when <= end_date]

        total = len(filtered)
        filtered = filtered[offset:offset + limit]

        return filtered, total

    def get_signals(
        self,
        symbol: str | None = None,
        side: str | None = None,
        tf: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Signal]:
        """Получить сигналы с фильтрами."""
        filtered = self._signals

        if symbol:
            filtered = [s for s in filtered if s.symbol.upper() == symbol.upper()]

        if side:
            filtered = [s for s in filtered if s.side.upper() == side.upper()]

        if tf:
            filtered = [s for s in filtered if s.tf == tf]

        if start_date:
            filtered = [s for s in filtered if s.when >= start_date]

        if end_date:
            filtered = [s for s in filtered if s.when <= end_date]

        total = len(filtered)
        filtered = filtered[offset:offset + limit]

        return filtered, total

    def get_stats(
        self,
        symbol: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:
        """Получить статистику по сделкам."""
        trades, _ = self.get_trades(symbol=symbol, start_date=start_date, end_date=end_date, limit=10000)

        if not trades:
            return {
                "total": 0,
                "wins": 0,
                "losses": 0,
                "winrate": 0,
                "total_r": 0,
                "avg_r": 0,
                "best_r": 0,
                "worst_r": 0,
            }

        wins = [t for t in trades if t.is_profit]
        losses = [t for t in trades if t.is_loss]
        r_values = [t.r for t in trades]

        return {
            "total": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "winrate": len(wins) / len(trades) if trades else 0,
            "total_r": sum(r_values),
            "avg_r": sum(r_values) / len(r_values) if r_values else 0,
            "best_r": max(r_values) if r_values else 0,
            "worst_r": min(r_values) if r_values else 0,
        }

    def get_symbol_stats(self) -> dict[str, dict]:
        """Получить статистику по каждому символу."""
        symbols = set(t.symbol for t in self._trades)
        return {
            symbol: self.get_stats(symbol=symbol)
            for symbol in symbols
        }

    def get_daily_stats(self, days: int = 30) -> list[dict]:
        """Получить ежедневную статистику."""
        end = datetime.utcnow()
        start = end - timedelta(days=days)

        daily = []
        for i in range(days):
            day_start = start + timedelta(days=i)
            day_end = day_start + timedelta(days=1)

            stats = self.get_stats(start_date=day_start, end_date=day_end)
            daily.append({
                "date": day_start.strftime("%Y-%m-%d"),
                **stats,
            })

        return daily

    def add_signal(self, signal: Signal):
        """Добавить новый сигнал в историю."""
        self._signals.append(signal)
        self._signals.sort(key=lambda s: s.ts, reverse=True)

        # Сохраняем в файл
        with open(SIGNALS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": signal.ts,
                "symbol": signal.symbol,
                "side": signal.side,
                "entry": signal.entry,
                "sl": signal.sl,
                "tp1": signal.tp1,
                "tp2": signal.tp2,
                "tp3": signal.tp3,
                "tf": signal.tf,
                "image_path": signal.image_path,
                "caption": signal.caption,
            }, ensure_ascii=False) + "\n")

        logger_config.metrics.increment("signal_added_to_gallery")


# Глобальный инстанс галереи
gallery = TradeGallery()


def render_gallery_html(
    symbol: str | None = None,
    side: str | None = None,
    days: int = 30,
) -> str:
    """Сгенерировать HTML для галереи."""
    end = datetime.utcnow()
    start = end - timedelta(days=days)

    trades, total_trades = gallery.get_trades(
        symbol=symbol,
        side=side,
        start_date=start,
        end_date=end,
        limit=100,
    )

    signals, total_signals = gallery.get_signals(
        symbol=symbol,
        side=side,
        start_date=start,
        end_date=end,
        limit=100,
    )

    stats = gallery.get_stats(symbol=symbol, start_date=start, end_date=end)

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Галерея сделок - KidzordFX</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
        .header {{ background: #2563eb; color: white; padding: 20px; margin-bottom: 20px; border-radius: 8px; }}
        .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }}
        .stat-card {{ background: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .stat-value {{ font-size: 1.5em; font-weight: bold; color: #2563eb; }}
        .stat-label {{ color: #6b7280; font-size: 0.9em; }}
        .section {{ background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
        .trade {{ border-bottom: 1px solid #e5e7eb; padding: 15px 0; }}
        .trade:last-child {{ border-bottom: none; }}
        .trade-profit {{ color: #10b981; }}
        .trade-loss {{ color: #ef4444; }}
        .signal-card {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 15px; margin-bottom: 15px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 Галерея сделок</h1>
            <p>Период: {days} дней | Символ: {symbol or 'Все'} | Сторона: {side or 'Все'}</p>
        </div>

        <div class="stats">
            <div class="stat-card">
                <div class="stat-value">{stats['total']}</div>
                <div class="stat-label">Всего сделок</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats['wins']}</div>
                <div class="stat-label">Прибыльных</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats['winrate']:.1%}</div>
                <div class="stat-label">Winrate</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats['total_r']:+.2f}R</div>
                <div class="stat-label">Сумма R</div>
            </div>
        </div>

        <div class="section">
            <h2>📈 Сигналы ({total_signals})</h2>
            {"".join(f"""
            <div class="signal-card">
                <strong>{s.side} {s.symbol}</strong> • {s.when:%d.%m %H:%M}
                <br>Вход: {s.entry} | SL: {s.sl} | TP1: {s.tp1}
            </div>
            """ for s in signals)}
        </div>

        <div class="section">
            <h2>💰 Сделки ({total_trades})</h2>
            {"".join(f"""
            <div class="trade">
                <span class="{'trade-profit' if t.is_profit else 'trade-loss'}">
                    {t.kind.upper()} {t.symbol} • {t.r:+.2f}R
                </span>
                <small>{t.when:%d.%m %H:%M}</small>
            </div>
            """ for t in trades)}
        </div>
    </div>
</body>
</html>
"""

    return html


if __name__ == "__main__":
    # Тест: сгенерировать HTML галереи
    html = render_gallery_html(days=7)
    output_path = os.path.join(os.path.dirname(__file__), "gallery.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Галерея сохранена в {output_path}")
