#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""In-sample разбор: какие признаки отличают сетапы автора от лишних.

    python tools/pmb_fit.py --ref pmb_trades.json
    python tools/pmb_fit.py --ref pmb_trades.json --window 10

Это НЕ валидация. Мы подгоняем фильтры под размеченную выборку автора
(48 H4-сетапов) и вытаскиваем правила; проверять их надо на независимом
окне, иначе это просто описание выборки.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pmb  # noqa: E402

PAIRS = ("EURUSD", "GBPUSD", "JPN225", "AUDUSD")
NUMERIC = ("sweep_atr", "rb_wick", "rb_atr", "bars_sweep", "bars_exp",
           "exp_atr", "fvg_atr", "retest_atr", "risk_atr", "dr_pos",
           "level_dist_atr", "mb_body_atr", "ta_dist_atr", "entry_level_atr")


def date_of(ts: int) -> dt.date:
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).date()


def load_pair(symbol: str):
    import MetaTrader5 as mt5

    mt5.symbol_select(symbol, True)
    r4 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H4, 0, 5000)
    r1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 5000)
    if r4 is None or r1 is None:
        return None
    def fr(r):
        return pmb.Frame(t=r["time"].astype("int64"), o=r["open"].astype(float),
                         h=r["high"].astype(float), l=r["low"].astype(float),
                         c=r["close"].astype(float))
    return fr(r4), fr(r1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--window", type=int, default=10)
    args = ap.parse_args()
    ref = json.load(open(args.ref, encoding="utf-8"))

    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit("MT5 не отвечает")

    rows = []
    for sym in PAIRS:
        frames = load_pair(sym)
        if frames is None:
            print(f"{sym}: нет данных"); continue
        eng = pmb.Engine(frames[0], frames[1], pmb.Params(min_bars_exp=0, min_exp_atr=0.0, min_rb_wick=0.0, rb_wick=0.0, exp_atr=0.5))
        trades = eng.run()
        theirs = [r for r in ref if (r.get("pair") == sym or (sym == "JPN225" and r.get("pair") == "JP225")) and r.get("tf") == "4h" and r.get("date")]
        pairs = []
        for ai, r in enumerate(theirs):
            d0 = dt.date.fromisoformat(r["date"])
            for ci, t in enumerate(trades):
                dates = [date_of(t.ts_setup), date_of(t.ts_trigger)]
                if t.ts_entry:
                    dates.append(date_of(t.ts_entry))
                gap = min(abs((d - d0).days) for d in dates)
                if gap <= args.window:
                    pairs.append((gap, ai, ci))
        pairs.sort()
        used_a, used_c = set(), set()
        for gap, ai, ci in pairs:
            if ai in used_a or ci in used_c:
                continue
            used_a.add(ai); used_c.add(ci)
        for ci, t in enumerate(trades):
            d = dict(t.feat)
            d.update(pair=sym, matched=ci in used_c, result=t.result,
                     date=date_of(t.ts_setup).isoformat(), n=ci)
            rows.append(d)
        print(f"{sym}: кандидатов {len(trades)}, сетапов автора {len(theirs)}, "
              f"совпало {len(used_a)}, лишних {len(trades) - len(used_c)}")
    mt5.shutdown()

    matched = [r for r in rows if r["matched"]]
    extras = [r for r in rows if not r["matched"]]
    print(f"\nвсего: кандидатов {len(rows)}, совпало {len(matched)}, лишних {len(extras)}")
    print("потолок recall при текущем детекторе: "
          f"{100*len(matched)/max(1, len(matched)+ (48-len(matched))):.0f}%"
          " (считается от 48 H4-сетапов; часть могла не попасть в кандидаты)")

    print("\nпризнаки (медиана [q25..q75]) — совпавшие vs лишние:")
    for k in NUMERIC:
        def q(xs):
            if not xs:
                return "—"
            xs = sorted(xs)
            pick = lambda p: xs[min(len(xs) - 1, max(0, int(p * (len(xs) - 1))))]
            return f"{pick(.5):.2f} [{pick(.25):.2f}..{pick(.75):.2f}]"
        print(f"  {k:16} {q([r[k] for r in matched]):>22}   {q([r[k] for r in extras]):>22}")
    from collections import Counter
    print("\nтип ликвидности:")
    print("  matched:", dict(Counter(r["liq"] for r in matched)))
    print("  extras :", dict(Counter(r["liq"] for r in extras)))
    print("стороны:")
    print("  matched:", dict(Counter(r["side"] for r in matched)))
    print("  extras :", dict(Counter(r["side"] for r in extras)))

    print("\nжадный поиск условий (in-sample):")
    keep = rows
    rules = []
    for step in range(3):
        best = best_rule(keep, [r for r in keep if r["matched"]], len(keep))
        if not best:
            break
        rules.append(best)
        keep = [r for r in keep if rule_ok(r, best)]
        tp = sum(1 for r in keep if r["matched"])
        fp = len(keep) - tp
        prec = tp / max(1, tp + fp)
        rec = tp / max(1, len(matched))
        f1 = 2 * prec * rec / max(1e-9, prec + rec)
        print(f"  шаг {step + 1}: {best} -> keep={len(keep)} tp={tp} fp={fp} "
              f"precision={prec:.2f} recall={rec:.2f} F1={f1:.2f}")
    print("\nитоговое правило:", rules)


def rule_ok(row: dict, rule) -> bool:
    feat, op, thr = rule[0], rule[1], rule[2]
    v = row.get(feat)
    if isinstance(v, str) or v is None:
        return v == thr
    return (v >= thr) if op == ">=" else (v <= thr)


def best_rule(rows, matched, total_candidates):
    """Грубый поиск одного порога по F1 на размеченной выборке."""
    best = None
    for k in NUMERIC:
        vals = sorted({round(r[k], 3) for r in rows})
        for op in (">=", "<="):
            for thr in vals:
                keep = [r for r in rows if rule_ok(r, (k, op, thr))]
                tp = sum(1 for r in keep if r["matched"])
                fp = sum(1 for r in keep if not r["matched"])
                fn = len(matched) - tp
                if tp == 0:
                    continue
                prec = tp / (tp + fp)
                rec = tp / max(1, len(matched))
                f1 = 2 * prec * rec / (prec + rec)
                if best is None or f1 > best[0]:
                    best = (f1, (k, op, thr), prec, rec, tp, fp, fn)
    if not best:
        return None
    f1, rule, prec, rec, tp, fp, fn = best
    print(f"  F1={f1:.2f} precision={prec:.2f} recall={rec:.2f} "
          f"(tp={tp} fp={fp} fn={fn})")
    return rule


if __name__ == "__main__":
    main()
