#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Бэктест механической интерпретации PRO Mitigation Block (pmb.py).

    python tools/pmb_backtest.py --symbol EURUSD
    python tools/pmb_backtest.py --symbol EURUSD --ref pmb_trades.json
    python tools/pmb_backtest.py --symbol EURUSD --min-risk-atr 0.35

H4 — рабочий ТФ, D1 — старший. Сделки пишутся в renders/ (gitignored).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pmb  # noqa: E402

RENDERS = os.path.join(ROOT, "renders")


def frame(rates) -> pmb.Frame:
    return pmb.Frame(
        t=rates["time"].astype("int64"),
        o=rates["open"].astype(float),
        h=rates["high"].astype(float),
        l=rates["low"].astype(float),
        c=rates["close"].astype(float),
    )


def date_of(ts: int) -> dt.date:
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).date()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--bars", type=int, default=5000)
    ap.add_argument("--ref", default="", help="JSON с сделками автора для сверки")
    ap.add_argument("--csv", default="")
    ap.add_argument("--min-risk-atr", type=float, default=0.0)
    args = ap.parse_args()

    p = pmb.Params(min_risk_atr=args.min_risk_atr)
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise SystemExit(f"MT5 не отвечает: {mt5.last_error()}")
    mt5.symbol_select(args.symbol, True)
    r4 = mt5.copy_rates_from_pos(args.symbol, mt5.TIMEFRAME_H4, 0, args.bars)
    r1 = mt5.copy_rates_from_pos(args.symbol, mt5.TIMEFRAME_D1, 0, args.bars)
    mt5.shutdown()
    if r4 is None or r1 is None or len(r4) < 300 or len(r1) < 60:
        raise SystemExit("мало истории MT5")
    h4, d1 = frame(r4), frame(r1)

    eng = pmb.Engine(h4, d1, p)
    trades = eng.run()
    stats = pmb.result(trades, eng.cancelled)
    print(f"{args.symbol} H4: {date_of(h4.t[0])} .. {date_of(h4.t[-1])}")
    print("параметры:", p)
    print("итог:", stats)

    rows = []
    for t in trades:
        rows.append({
            "setup": date_of(t.ts_setup).isoformat(),
            "trigger": date_of(t.ts_trigger).isoformat(),
            "entry": date_of(t.ts_entry).isoformat() if t.ts_entry else "",
            "exit": date_of(t.ts_exit).isoformat() if t.ts_exit else "",
            "side": "LONG" if t.side == 1 else "SHORT",
            "entry_px": round(t.entry, 5),
            "sl": round(t.sl, 5),
            "tp": round(t.tp, 5),
            "result": t.result,
            "level": round(t.level, 5),
            "ta": round(t.ta, 5),
        })
    out = args.csv or os.path.join(RENDERS, f"pmb_{args.symbol}_H4.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["setup"])
        w.writeheader()
        w.writerows(rows)
    print("сделки:", out)

    by_year: dict[str, list] = {}
    for t in trades:
        if t.result in ("win", "loss"):
            by_year.setdefault(date_of(t.ts_entry or t.ts_trigger).isoformat()[:4], []).append(t)
    for y in sorted(by_year):
        ts = by_year[y]
        w_ = sum(1 for x in ts if x.result == "win")
        print(f"  {y}: {len(ts)} входов, WR {100*w_/len(ts):.0f}%, netR {w_*p.tp_r-(len(ts)-w_):+.0f}R")

    if args.ref and os.path.exists(args.ref):
        audit_reference(args.ref, args.symbol, trades, p)


def audit_reference(path: str, symbol: str, trades: list, p: pmb.Params) -> None:
    ref = json.load(open(path, encoding="utf-8"))
    mine = [t for t in trades if t.result in ("win", "loss")]
    theirs = [r for r in ref if r.get("pair") == symbol and r.get("tf") == "4h"
              and r.get("s1") in ("Win", "Stop") and r.get("date")]
    used = set()
    matched = 0
    for r in theirs:
        d0 = dt.date.fromisoformat(r["date"])
        best = None
        for k, t in enumerate(mine):
            if k in used:
                continue
            dates = [date_of(t.ts_setup), date_of(t.ts_trigger)]
            if t.ts_entry:
                dates.append(date_of(t.ts_entry))
            gap = min(abs((d - d0).days) for d in dates)
            if gap <= 10 and (best is None or gap < best[0]):
                best = (gap, k)
        if best:
            used.add(best[1])
            matched += 1
            print(f"  совпал #{r['n']:>2} {r['date']} ~ {date_of(mine[best[1]].ts_setup)}")
        else:
            print(f"  НЕ найден #{r['n']:>2} {r['date']} S1={r['s1']}")
    recall = matched / len(theirs) * 100 if theirs else 0
    extras = len(mine) - matched
    print(f"сверка: совпало {matched}/{len(theirs)} ({recall:.0f}%), лишних {extras}")
    ok = recall >= 70 and extras <= 2 * len(theirs)
    print("критерий v0:", "ПРОЙДЕН" if ok else "НЕ ПРОЙДЕН")


if __name__ == "__main__":
    main()
