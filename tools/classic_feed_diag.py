#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Почему classic XAUUSD расходится на native и Exness.

Фиксируем live-параметры и меняем по одному фактору:
  N            native как есть
  E            Exness как есть
  E+2h         Exness со сдвигом времени сервера (+2 часа)
  E+2h/rebuild Exness +2h, H1/H4/D1/W1 пересобраны из M15
  E+2h/nogap   то же, но убраны ночные бары 00:00–01:59 (у native их нет)
  E+2h/Nhtf    Exness M15 + старшие ТФ native
  N/Ehtf       native M15 + старшие ТФ Exness (rebuild)

    python tools/classic_feed_diag.py
"""
from __future__ import annotations

import os
import sys
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402
import costs  # noqa: E402
import strategy as st  # noqa: E402

NATIVE = r"C:\Program Files\MetaTrader 5\terminal64.exe"
EXNESS = r"C:\MT5-Exness\terminal64.exe"
M15_BARS = 30000


def load(symbol: str, tf: str, bars: int, path: str) -> st.Frame:
    import MetaTrader5 as mt5

    if not mt5.initialize(path=path, timeout=30000):
        raise SystemExit(f"MT5 {path}: {mt5.last_error()}")
    rates = mt5.copy_rates_from_pos(symbol, _tf(tf), 0, bars)
    mt5.shutdown()
    if rates is None or len(rates) < 10:
        raise SystemExit(f"{symbol} {tf}: нет баров")
    rates = rates[:-1]                      # незакрытый бар
    return st.Frame(
        t=rates["time"].astype("int64"),
        o=rates["open"].astype(float), h=rates["high"].astype(float),
        l=rates["low"].astype(float), c=rates["close"].astype(float),
        v=rates["tick_volume"].astype(float),
    )


def _tf(tf: str):
    import MetaTrader5 as mt5

    return {"60": mt5.TIMEFRAME_H1, "240": mt5.TIMEFRAME_H4,
            "1D": mt5.TIMEFRAME_D1, "1W": mt5.TIMEFRAME_W1,
            "15": mt5.TIMEFRAME_M15}[tf]


def shift(frame: st.Frame, hours: float) -> st.Frame:
    return st.Frame(frame.t + int(hours * 3600), frame.o, frame.h,
                    frame.l, frame.c, frame.v)


def aggregate(frame: st.Frame, sec: int, week: bool = False) -> st.Frame:
    if week:                                # неделя с понедельника 00:00
        day = frame.t // 86400
        keys = (day - ((day - 4) % 7)) * 86400
    else:
        keys = frame.t // sec * sec
    order = np.argsort(keys, kind="stable")
    keys, o, h, l, c, v = (keys[order], frame.o[order], frame.h[order],
                           frame.l[order], frame.c[order], frame.v[order])
    out = []
    start = 0
    for i in range(1, len(keys) + 1):
        if i == len(keys) or keys[i] != keys[start]:
            out.append((keys[start], o[start], h[start:i].max(),
                        l[start:i].min(), c[i - 1], v[start:i].sum()))
            start = i
    arr = np.array(out, dtype=float)
    return st.Frame(t=arr[:, 0].astype("int64"), o=arr[:, 1], h=arr[:, 2],
                    l=arr[:, 3], c=arr[:, 4], v=arr[:, 5])


def rebuild(m15: st.Frame) -> dict[str, st.Frame]:
    return {"240": aggregate(m15, 14400), "60": aggregate(m15, 3600),
            "1D": aggregate(m15, 86400), "1W": aggregate(m15, 7 * 86400, week=True)}


def run(chart: st.Frame, htf: dict[str, st.Frame], lo: float, hi: float,
        spread: float = 0.0) -> dict:
    res = st.evaluate(chart, htf, bot.PARAMS)
    if not res.get("ok"):
        return {"n": 0, "r": 0.0, "exits": {}}
    trades = [t for t in bot.simulate(res, chart, bot.PARAMS)
              if lo <= chart.t[t["i"]] < hi]
    return {"n": len(trades),
            "r": sum(costs.net_rs(trades, spread)),
            "exits": Counter(t["exit"] for t in trades)}


def main() -> int:
    print("загрузка native...")
    n_chart = load("XAUUSD", "15", M15_BARS + 5000, NATIVE)
    n_htf = {
        "240": load("XAUUSD", "240", 8000, NATIVE),
        "60": load("XAUUSD", "60", 30000, NATIVE),
        "1D": load("XAUUSD", "1D", 1500, NATIVE),
        "1W": load("XAUUSD", "1W", 400, NATIVE),
    }
    print("загрузка Exness...")
    e_chart = load("XAUUSDm", "15", M15_BARS, EXNESS)
    e_htf = {
        "240": load("XAUUSDm", "240", 8000, EXNESS),
        "60": load("XAUUSDm", "60", 30000, EXNESS),
        "1D": load("XAUUSDm", "1D", 1500, EXNESS),
        "1W": load("XAUUSDm", "1W", 400, EXNESS),
    }

    e_chart2 = shift(e_chart, 2)
    e_htf2 = {k: shift(v, 2) for k, v in e_htf.items()}
    e_rebuild = rebuild(e_chart2)
    hour = (e_chart2.t % 86400) // 3600
    keep = (hour < 0) | (hour >= 2)         # у native нет 00:00–01:59
    e_nogap = st.Frame(e_chart2.t[keep], e_chart2.o[keep], e_chart2.h[keep],
                       e_chart2.l[keep], e_chart2.c[keep], e_chart2.v[keep])
    e_nogap_htf = rebuild(e_nogap)

    lo = max(float(n_chart.t[0]), float(e_chart2.t[0]))
    hi = min(float(n_chart.t[-1]), float(e_chart2.t[-1]))
    print(f"общее окно: {np.datetime64(int(lo), 's')} – {np.datetime64(int(hi), 's')}")

    n_spread = costs.symbol_spread("XAUUSD")
    e_spread = costs.symbol_spread("XAUUSDm")
    variants = [
        ("N", n_chart, n_htf, n_spread),
        ("N/rebuild", n_chart, rebuild(n_chart), n_spread),
        ("E", e_chart, e_htf, e_spread),
        ("E+2h", e_chart2, e_htf2, e_spread),
        ("E+2h/rebuild", e_chart2, e_rebuild, e_spread),
        ("E+2h/nogap", e_nogap, e_nogap_htf, e_spread),
        ("E+2h/Nhtf", e_chart2, n_htf, e_spread),
        ("N/Ehtf", n_chart, e_rebuild, n_spread),
    ]
    print(f"{'вариант':<16} {'сделок':>7} {'net R':>9}  выходы")
    for name, chart, htf, spread in variants:
        r = run(chart, htf, lo, hi, spread)
        print(f"{name:<16} {r['n']:>7} {r['r']:>+9.2f}  {dict(r['exits'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
