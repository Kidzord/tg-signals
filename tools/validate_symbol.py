#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Пакетный WFA по одному символу: набор стратегий за один прогон.

    MT5_TERMINAL_PATH="C:/MT5-Exness/terminal64.exe" \
      python tools/validate_symbol.py --symbol XAUUSDm --feed exness --bars 30000

Результат — JSON в renders/validation/ и компактная таблица в консоли.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402
import costs  # noqa: E402
from tools import walk_forward as wf  # noqa: E402

BY_SYMBOL = {
    "XAUUSD": ["classic", "classic_nopart", "ict_gold3"],
    "AUDUSD": ["range_live", "range_er10_tp3", "range_trend_aud"],
    "USDCAD": ["range_live", "range_er20_tp4", "range_cad_trail"],
    "EURUSD": ["range_er0_tp3", "range_trend_eur"],
    "GBPJPY": ["range", "range_er10_tp4"],
    "GBPCAD": ["range", "range_er20_tp4"],
}


def strategies_for(symbol: str) -> list[str]:
    base = symbol.upper().rstrip("M")
    if base in BY_SYMBOL:
        return BY_SYMBOL[base]
    return ["classic", "range"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--feed", default="native")
    ap.add_argument("--bars", type=int, default=30000)
    ap.add_argument("--strategies", help="через запятую; по умолчанию набор под символ")
    ap.add_argument("--exclude", default=os.getenv("WF_EXCLUDE", "").strip() or None)
    args = ap.parse_args()

    names = ([s.strip() for s in args.strategies.split(",") if s.strip()]
             if args.strategies else strategies_for(args.symbol))
    data = bot.connect()
    if data is None:
        print("нет MT5")
        return 3
    chart, htf = bot.load_frames(data, args.symbol, bot.TF, args.bars)
    chart, htf = wf.exclude_window(chart, htf, args.exclude)
    spread = costs.symbol_spread(args.symbol)
    days = (chart.t[-1] - chart.t[0]) / 86400
    wf.BARS_PER_DAY = max(1, int(round(len(chart.c) / days)))
    print(f"{args.feed}/{args.symbol}: {len(chart.c)} баров, {days:.0f} дн., "
          f"~{wf.BARS_PER_DAY}/день, спред {spread:.5f}, стратегии: {', '.join(names)}")

    results = []
    for name in names:
        if name not in wf.STRATEGIES:
            print("нет стратегии", name)
            continue
        res = wf.wfa(name, wf.STRATEGIES[name], chart, htf, spread)
        res["symbol"] = args.symbol
        res["feed"] = args.feed
        results.append(res)
    data.close()

    out_dir = Path("renders/validation")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.feed}_{args.symbol}_{args.bars}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'стратегия':<18} {'вердикт':<12} {'сделок':>7} {'net R':>9} "
          f"{'актив.фолды+':>12}")
    for r in results:
        print(f"{r['name']:<18} {r['verdict']:<12} {r['trades']:>7} "
              f"{r['net_r']:>+9.2f} "
              f"{r['folds_active_positive']:>5}/{r['folds_active']:<5}")
    print(f"JSON: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
