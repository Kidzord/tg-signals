"""Источники OHLC для карточки: MetaTrader 5 → CSV → демо-данные.

Карточка рисуется по данным брокера, а не по чужому скриншоту: вебхук
не умеет передавать изображение, а рендер своих свечей заодно снимает
вопрос с правами на чужие графики.
"""
from __future__ import annotations

import csv
import datetime as dt
import math
import random
from dataclasses import dataclass

import config as cfg


@dataclass
class Bar:
    t: dt.datetime
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


TF_MINUTES = {
    "1": 1, "3": 3, "5": 5, "15": 15, "30": 30, "45": 45,
    "60": 60, "120": 120, "180": 180, "240": 240,
    "1D": 1440, "1W": 10080,
}


def tf_minutes(tf: str) -> int:
    return TF_MINUTES.get(tf.upper(), 15)


def tf_label(tf: str) -> str:
    tf = tf.upper()
    if tf in ("1D", "1W"):
        return tf
    m = tf_minutes(tf)
    if m % 60 == 0:
        return f"H{m // 60}"
    return f"M{m}"


# ---------------------------------------------------------------- MT5

def _mt5_timeframe(tf: str):
    import MetaTrader5 as mt5

    m = tf_minutes(tf)
    return {
        1: mt5.TIMEFRAME_M1, 5: mt5.TIMEFRAME_M5, 15: mt5.TIMEFRAME_M15,
        30: mt5.TIMEFRAME_M30, 60: mt5.TIMEFRAME_H1, 240: mt5.TIMEFRAME_H4,
        1440: mt5.TIMEFRAME_D1, 10080: mt5.TIMEFRAME_W1,
    }.get(m, mt5.TIMEFRAME_M15)


def _mt5_resolve(symbol: str) -> str | None:
    """Тикер в алерте может не совпадать с тикером брокера (суффиксы .r, .pro и т.п.)."""
    import MetaTrader5 as mt5

    candidates = [symbol, symbol + cfg.MT5_SUFFIX]
    base = symbol.upper()
    for c in candidates:
        if c and mt5.symbol_info(c) is not None:
            return c
    # последняя попытка: ищем тикер, который начинается так же
    for s in (mt5.symbols_get() or []):
        name = s.name.upper()
        if name.startswith(base) or (base.startswith("XAU") and name.startswith("XAU")):
            return s.name
    return None


def from_mt5(symbol: str, tf: str, count: int) -> list[Bar] | None:
    try:
        import MetaTrader5 as mt5
    except Exception:
        return None

    try:
        if not mt5.initialize():
            return None
        real = _mt5_resolve(symbol)
        if not real:
            return None
        mt5.symbol_select(real, True)
        rates = mt5.copy_rates_from_pos(real, _mt5_timeframe(tf), 0, count)
        if rates is None or len(rates) < 10:
            return None
        return [
            Bar(
                t=dt.datetime.fromtimestamp(int(r["time"]), tz=dt.timezone.utc),
                o=float(r["open"]), h=float(r["high"]),
                l=float(r["low"]), c=float(r["close"]),
                v=float(r["tick_volume"]),
            )
            for r in rates
        ]
    except Exception:
        return None
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


# ---------------------------------------------------------------- CSV

def from_csv(count: int) -> list[Bar] | None:
    """Резервный источник: CSV с колонками time,open,high,low,close[,volume]."""
    if not cfg.DATA_CSV:
        return None
    try:
        rows: list[Bar] = []
        with open(cfg.DATA_CSV, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                t = None
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                    try:
                        t = dt.datetime.strptime(row["time"], fmt)
                        break
                    except (ValueError, KeyError):
                        continue
                if t is None:
                    continue
                rows.append(Bar(t, float(row["open"]), float(row["high"]),
                                float(row["low"]), float(row["close"]),
                                float(row.get("volume") or 0)))
        return rows[-count:] if len(rows) >= 10 else None
    except Exception:
        return None


# ---------------------------------------------------------------- DEMO

def demo_bars(count: int = 120, tf: str = "15") -> list[Bar]:
    """Детерминированная серия для проверки вёрстки без брокера."""
    rng = random.Random(20261025)
    step = dt.timedelta(minutes=tf_minutes(tf))
    t0 = dt.datetime(2026, 10, 23, 8, 0)
    price = 3978.0
    out: list[Bar] = []
    for i in range(count):
        # тренд вверх, к концу — откат и отскок
        if i < count * 0.65:
            drift = 0.55
        elif i < count * 0.85:
            drift = -0.95
        else:
            drift = 0.75
        o = price
        move = drift + rng.gauss(0, 2.1)
        c = o + move
        h = max(o, c) + abs(rng.gauss(0, 1.1))
        l = min(o, c) - abs(rng.gauss(0, 1.1))
        out.append(Bar(t0 + step * i, round(o, 2), round(h, 2), round(l, 2), round(c, 2), rng.randint(300, 1800)))
        price = c
    return out


# ---------------------------------------------------------------- API

def load_bars(symbol: str, tf: str, count: int | None = None) -> tuple[list[Bar], str, bool]:
    """Возвращает (бары, подпись источника, признак демо)."""
    count = count or cfg.BARS_COUNT
    bars = from_mt5(symbol, tf, count)
    if bars:
        return bars, "MT5", False
    bars = from_csv(count)
    if bars:
        return bars, "CSV", False
    return demo_bars(count, tf), "демо-данные", True
