#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Почему EURUSD range расходится на native и Exness: влияние H4.

Range-стратегия строит зоны и тренд-фильтр по H4. У native сервер UTC+2,
у Exness UTC+0, поэтому брокерские H4 имеют разные границы. Проверяем
варианты с пересборкой H4 из M15.

    python tools/range_feed_diag.py
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402
import costs  # noqa: E402
import range_v2  # noqa: E402
from tools.classic_feed_diag import aggregate, load, shift  # noqa: E402

EXNESS = r"C:\MT5-Exness\terminal64.exe"
NATIVE = r"C:\Program Files\MetaTrader 5\terminal64.exe"
BARS = 30000


def run(chart, H, *, tp: float, er: float, trend: str, lo: float, hi: float,
        days: int | None = None, spread: float = 0.0) -> dict:
    zones = range_v2.zones_for_chart(chart, H, touches=2,
                                     flips_keep=bot.RANGE_FLIP_KEEP)
    trades = range_v2.backtest(
        chart, zones, H, stop_atr=1.0, tp_r=tp, max_r=tp + 2.0, session="",
        smt=None, smt_look=0, max_bars=bot.PARAMS.max_bars, er_max=er,
        side_allow="both", trend_filter=trend, max_stops=bot.RANGE_MAX_STOPS,
    )
    end = hi
    cut = lo if days is None else max(lo, hi - days * 86400)
    sel = [t for t in trades if cut <= chart.t[t["entry_i"]] < end]
    return {"n": len(sel), "r": sum(costs.net_rs(sel, spread)),
            "exits": Counter(t["exit"] for t in trades)}


def main() -> int:
    print("загрузка native EURUSD...")
    n_chart = load("EURUSD", "15", BARS + 5000, NATIVE)
    n_h4 = load("EURUSD", "240", 8000, NATIVE)
    print("загрузка Exness EURUSDm...")
    e_chart = load("EURUSDm", "15", BARS, EXNESS)
    e_h4 = load("EURUSDm", "240", 8000, EXNESS)

    e_chart2 = shift(e_chart, 2)
    e_h4_2 = shift(e_h4, 2)
    n_rebuild = aggregate(n_chart, 14400)
    e_rebuild = aggregate(e_chart2, 14400)

    lo = max(float(n_chart.t[0]), float(e_chart2.t[0]))
    hi = min(float(n_chart.t[-1]), float(e_chart2.t[-1]))
    n_spread = costs.symbol_spread("EURUSD")
    e_spread = costs.symbol_spread("EURUSDm")
    variants = [
        ("N", n_chart, n_h4, n_spread),
        ("N/rebuild", n_chart, n_rebuild, n_spread),
        ("E", e_chart, e_h4, e_spread),
        ("E+2h", e_chart2, e_h4_2, e_spread),
        ("E+2h/rebuild", e_chart2, e_rebuild, e_spread),
        ("N/Erebuild", n_chart, e_rebuild, n_spread),
    ]
    configs = [
        ("er0/tp3/off", dict(tp=3.0, er=0.0, trend="off")),
        ("er0/tp3/with", dict(tp=3.0, er=0.0, trend="with")),
        ("er10/tp3/with", dict(tp=3.0, er=0.10, trend="with")),
    ]
    for label, cfg in configs:
        print(f"\n=== {label} ===")
        print(f"{'вариант':<16} {'сделок':>7} {'net R':>9} {'послед.60д':>12}")
        for name, chart, H, spread in variants:
            r = run(chart, H, lo=lo, hi=hi, spread=spread, **cfg)
            r60 = run(chart, H, lo=lo, hi=hi, days=60, spread=spread, **cfg)
            print(f"{name:<16} {r['n']:>7} {r['r']:>+9.2f} {r60['r']:>+12.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
