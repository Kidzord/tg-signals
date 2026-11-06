"""Внешние дневные ряды для research: Yahoo, FRED, календарь FOMC.

Файлы лежат в `data/external/` и скачиваются отдельно (curl), поэтому модуль
только читает фиксированные пути и ничего не пишет. Это важно: путь из CLI
в research-инструментах не должен управлять файловым вводом-выводом.

Источники:
  * Yahoo Finance chart API — daily OHLC индексов и FX с 2000 года;
  * FRED CSV — VIX (VIXCLS), 10Y TIPS (DFII10), 2Y (DGS2),
    broad dollar (DTWEXBGS);
  * federalreserve.gov — даты заседаний FOMC 2010–2026.

Точки выравнивания: VIX и доходности публикуются в тот же день, но
используются на следующем баре (сдвиг на 1 день в вызывающем коде), чтобы
не заглядывать вперёд.
"""
from __future__ import annotations

import datetime as dt
import json
import os

import numpy as np

import strategy as st

ROOT = os.path.dirname(os.path.abspath(__file__))
EXT = os.path.join(ROOT, "data", "external")

YAHOO_SYMBOLS = ("SPX", "NDX", "DAX", "N225",
                 "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
                 "USDCHF", "NZDUSD")
# месячным стратегиям спред не критичен: 2 bp FX и 20 bp индексы
DEFAULT_SPREAD_PCT = {
    "SPX": 0.0002, "NDX": 0.0002, "DAX": 0.0002, "N225": 0.0002,
    "EURUSD": 0.00002, "GBPUSD": 0.00003, "USDJPY": 0.00003,
    "AUDUSD": 0.00004, "USDCAD": 0.00005, "USDCHF": 0.00004,
    "NZDUSD": 0.00006,
}


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def yahoo_frame(symbol: str) -> st.Frame:
    """Дневной OHLC из сохранённого JSON Yahoo (валидные бары)."""
    sym = symbol.upper()
    if sym not in YAHOO_SYMBOLS:
        raise KeyError(f"нет внешнего ряда {sym}; есть {', '.join(YAHOO_SYMBOLS)}")
    data = json.loads(_read_text(os.path.join(EXT, "yahoo", f"{sym}.json")))
    res = data["chart"]["result"][0]
    ts = np.asarray(res["timestamp"], dtype="int64")
    q = res["indicators"]["quote"][0]
    o = np.asarray(q["open"], dtype=float)
    h = np.asarray(q["high"], dtype=float)
    l = np.asarray(q["low"], dtype=float)
    c = np.asarray(q["close"], dtype=float)
    v = np.asarray(q.get("volume") or np.zeros(len(c)), dtype=float)
    ok = (~np.isnan(o)) & (~np.isnan(h)) & (~np.isnan(l)) & (~np.isnan(c))
    return st.Frame(t=ts[ok], o=o[ok], h=h[ok], l=l[ok], c=c[ok], v=v[ok])


def yahoo_hourly_frame(symbol: str) -> st.Frame:
    """Часовые бары Yahoo (730 дней) из data/external/yahoo_hourly."""
    sym = symbol.upper()
    data = json.loads(_read_text(os.path.join(EXT, "yahoo_hourly", f"{sym}_60m.json")))
    res = data["chart"]["result"][0]
    ts = np.asarray(res["timestamp"], dtype="int64")
    q = res["indicators"]["quote"][0]
    o = np.asarray(q["open"], dtype=float)
    h = np.asarray(q["high"], dtype=float)
    l = np.asarray(q["low"], dtype=float)
    c = np.asarray(q["close"], dtype=float)
    v = np.asarray(q.get("volume") or np.zeros(len(c)), dtype=float)
    ok = (~np.isnan(o)) & (~np.isnan(h)) & (~np.isnan(l)) & (~np.isnan(c))
    return st.Frame(t=ts[ok], o=o[ok], h=h[ok], l=l[ok], c=c[ok], v=v[ok])


def fred_series(series_id: str) -> tuple[list[dt.date], list[float]]:
    """Дневной ряд FRED: даты и значения, без пропусков."""
    raw = _read_text(os.path.join(EXT, "fred", f"{series_id}.csv"))
    days: list[dt.date] = []
    vals: list[float] = []
    for line in raw.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 2 or not parts[0]:
            continue
        try:
            d = dt.date.fromisoformat(parts[0])
            v = float(parts[1])
        except ValueError:
            continue
        days.append(d)
        vals.append(v)
    return days, vals


def fred_by_date(series_id: str) -> dict[str, float]:
    days, vals = fred_series(series_id)
    return {d.isoformat(): v for d, v in zip(days, vals)}


def fomc_dates() -> list[dt.date]:
    raw = _read_text(os.path.join(EXT, "fomc_dates.csv"))
    out = []
    for line in raw.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(dt.date.fromisoformat(line))
        except ValueError:
            continue
    return out


def value_on_or_before(data: dict[str, float], day: dt.date) -> float | None:
    """Значение ряда на дату или последнюю перед ней (без будущего)."""
    for _ in range(10):
        v = data.get(day.isoformat())
        if v is not None:
            return v
        day -= dt.timedelta(days=1)
    return None
