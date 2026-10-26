"""Прогон сделок под правила проп-фирмы.

    python prop.py [стратегия]

Считает: хватает ли лимитов (день/всего), когда наступает нарушение,
и какой риск на сделку безопасен. Стартовый баланс 5000$.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot
import range_v2 as r2
import ta_core as ta

START = float(os.getenv("PROP_START", "5000"))
DAILY_LIMIT = float(os.getenv("PROP_DAILY", "250"))
TOTAL_LIMIT = float(os.getenv("PROP_TOTAL", "400"))
STOP_PTS = float(os.getenv("PROP_STOP_PTS", "10"))


def get_trades() -> tuple[list[dict], np.ndarray]:
    data = bot.MT5Data()
    try:
        chart, htf = bot.load_frames(data, bot.SYMBOL, bot.TF, int(os.getenv("BOT_BARS", "20000")))
    finally:
        data.close()
    zones = r2.zones_for_chart(chart, htf["240"], touches=2, flips_keep=60)
    trades = r2.backtest(chart, zones, htf["240"], stop_pts=STOP_PTS)
    er = ta.efficiency_ratio(chart.c)
    er_max = float(os.getenv("RANGE_ER_MAX", "0.15"))
    trades = [t for t in trades if not np.isnan(er[t["entry_i"]]) and er[t["entry_i"]] <= er_max]
    return trades, chart


def simulate(trades: list[dict], chart, risk_pct: float) -> dict:
    risk_usd = START * risk_pct / 100.0
    equity = START
    peak = START
    max_dd = 0.0
    day = None
    day_pnl = 0.0
    worst_day = 0.0
    daily_breach = total_breach = None
    streak = worst_streak = 0

    for t in trades:
        when = dt.datetime.fromtimestamp(float(chart.t[t["exit_i"]]), dt.timezone.utc)
        d = when.date()
        if d != day:
            worst_day = min(worst_day, day_pnl) if day is not None else worst_day
            day, day_pnl = d, 0.0

        pnl = t["r"] * risk_usd
        equity += pnl
        day_pnl += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

        if pnl < 0:
            streak += 1
            worst_streak = max(worst_streak, streak)
        else:
            streak = 0

        if daily_breach is None and day_pnl <= -DAILY_LIMIT:
            daily_breach = (d, day_pnl)
        if total_breach is None and equity <= START - TOTAL_LIMIT:
            total_breach = (d, equity)

    worst_day = min(worst_day, day_pnl)
    return {
        "risk_pct": risk_pct,
        "final": equity,
        "max_dd": max_dd,
        "worst_day": worst_day,
        "worst_streak": worst_streak,
        "daily_breach": daily_breach,
        "total_breach": total_breach,
    }


def main() -> None:
    trades, chart = get_trades()
    rs = [t["r"] for t in trades]
    wins = sum(1 for r in rs if r > 0)
    span = (chart.t[-1] - chart.t[0]) / 86400.0
    print(f"сделок {len(trades)} · винрейт {wins / len(rs) * 100:.0f}% · "
          f"сумма {sum(rs):+.0f}R · период {span:.0f} дн.")
    print(f"правила: старт {START:.0f}$ · день −{DAILY_LIMIT:.0f}$ · всего −{TOTAL_LIMIT:.0f}$\n")

    print(f"{'риск':>6} {'$на сделку':>11} {'финиш':>8} {'макс.DD':>8} {'худший день':>12} "
          f"{'серия':>6} {'итог':>10}")
    for risk in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0):
        r = simulate(trades, chart, risk)
        if r["total_breach"] or r["daily_breach"]:
            when = r["total_breach"][0] if r["total_breach"] else r["daily_breach"][0]
            verdict = f"слив {when:%d.%m}"
        else:
            verdict = "выжил"
        print(f"{risk:>5.2f}% {START * risk / 100:>10.0f}$ {r['final']:>8.0f} "
              f"{r['max_dd']:>8.0f} {r['worst_day']:>12.0f} {r['worst_streak']:>6} {verdict:>10}")

    print("\nдетали для риска 1%:")
    r = simulate(trades, chart, 1.0)
    print(f"  финиш {r['final']:.0f}$ · просадка {r['max_dd']:.0f}$ · худший день {r['worst_day']:.0f}$")
    print(f"  серия убытков подряд: {r['worst_streak']}")
    if r["daily_breach"]:
        d, p = r["daily_breach"]
        print(f"  дневной лимит пробит {d:%d.%m}: {p:.0f}$")
    if r["total_breach"]:
        d, e = r["total_breach"]
        print(f"  общий лимит пробит {d:%d.%m}: баланс {e:.0f}$")


if __name__ == "__main__":
    main()
