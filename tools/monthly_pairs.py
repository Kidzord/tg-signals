#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Месячная net-R разбивка по парам на live-параметрах.

classic XAUUSD + range EURUSD/USDCAD/AUDUSD. Окно — последние N баров M15
(по умолчанию 18000, как в walk_forward на 2026-11-05). Издержки вычитаются
как в research: max(0.05, spread/risk).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot
import costs
import range_v2
import strategy as st

LIVE_RANGE = {
    "EURUSD": dict(er_max=0.0, trend="with", spread=0.00004),
    "USDCAD": dict(er_max=0.20, trend="off", spread=0.00005),
    "AUDUSD": dict(er_max=0.10, trend="with", spread=0.00004),
}


def _month(ts: float) -> str:
    return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).strftime("%Y-%m")


def _monthly(trades: list[dict], chart, idx_key: str, spread: float) -> dict:
    out: dict[str, float] = {}
    for t in trades:
        i = int(t.get(idx_key, 0))
        if i < 0 or i >= len(chart.t):
            continue
        risk = float(t.get("risk") or 0.0)
        net = float(t["r"]) - costs.cost_r(spread, risk)
        key = _month(chart.t[i])
        out[key] = out.get(key, 0.0) + net
    return {k: round(v, 2) for k, v in sorted(out.items())}


def classic(data, bars: int) -> dict:
    chart, htf = bot.load_frames(data, "XAUUSD", bot.TF, bars)
    res = st.evaluate(chart, htf, bot.PARAMS)
    if not res.get("ok"):
        return {}
    return _monthly(bot.simulate(res, chart, bot.PARAMS), chart, "i", 0.13)


def range_pair(data, sym: str, bars: int, cfg: dict) -> dict:
    chart, htf = bot.load_frames(data, sym, bot.TF, bars)
    H = htf[bot.RANGE_ZONE_TF]
    zones = range_v2.zones_for_chart(chart, H, touches=2,
                                     flips_keep=bot.RANGE_FLIP_KEEP)
    trades = range_v2.backtest(
        chart, zones, H, stop_atr=1.0, tp_r=3.0, max_r=5.0, session="",
        smt=None, smt_look=0, max_bars=bot.PARAMS.max_bars,
        er_max=cfg["er_max"], side_allow="both", trend_filter=cfg["trend"],
        max_stops=bot.RANGE_MAX_STOPS, spread=cfg["spread"],
        max_spread_r=bot.SPREAD_MAX_R)
    return _monthly(trades, chart, "entry_i", cfg["spread"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=18000)
    args = ap.parse_args()
    data = bot.connect()
    if data is None:
        raise SystemExit("нет MT5")
    try:
        series = {"XAUUSD classic": classic(data, args.bars)}
        for sym, cfg in LIVE_RANGE.items():
            series[f"{sym} range"] = range_pair(data, sym, args.bars, cfg)
    finally:
        data.close()

    all_months = sorted({m for s in series.values() for m in s})
    out = {}
    for name, months in series.items():
        vals = [months.get(m, 0.0) for m in all_months]
        active = [v for v in vals if v != 0.0]
        out[name] = {
            "months": months,
            "positive_months": sum(1 for v in vals if v > 0),
            "negative_months": sum(1 for v in vals if v < 0),
            "flat_months": sum(1 for v in vals if v == 0.0),
            "active_months": len(active),
            "net_r": round(sum(vals), 2),
            "best_month": round(max(vals), 2) if vals else 0.0,
            "worst_month": round(min(vals), 2) if vals else 0.0,
        }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nмесяцы: {all_months[0]}..{all_months[-1]} ({len(all_months)})", file=sys.stderr)
    print(f"{'пара':<16}{'плюс':>6}{'минус':>7}{'0/нет':>7}{'всего':>7}"
          f"{'net R':>9}{'лучший':>9}{'худший':>9}", file=sys.stderr)
    for name, r in out.items():
        print(f"{name:<16}{r['positive_months']:>6}{r['negative_months']:>7}"
              f"{r['flat_months']:>7}{r['active_months']:>7}{r['net_r']:>+9.2f}"
              f"{r['best_month']:>+9.2f}{r['worst_month']:>+9.2f}", file=sys.stderr)
    print("\nпо месяцам (net R):", file=sys.stderr)
    print(f"{'месяц':<9}" + "".join(f"{n.split()[0]:>10}" for n in series), file=sys.stderr)
    for m in all_months:
        print(f"{m:<9}" + "".join(f"{series[n].get(m, 0.0):>+10.2f}" for n in series),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
