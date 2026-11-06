#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Индексная momentum-стратегия: Donchian-пробой + EMA-фильтр на M15.

Правила:
  * long, если close пробивает максимум предыдущих n баров и (опционально)
    close > EMA200; short — симметрично;
  * вход на открытии следующего бара;
  * стоп stop_atr * ATR(14), тейк tp_atr * ATR(14), максимум max_bars;
  * одна позиция, без усреднения; side = both/long;
  * издержки: max(0.05, spread/risk), спред можно удвоить --stress.

Режимы: sweep (сетка на всём периоде) и wfa (60 дней IS -> 15 дней OOS,
параметры выбираются на IS). JSON в stdout, таблицы в stderr.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.grid_probe import Bar  # noqa: E402
from tools.mr_probe import atr_series, load_m15  # noqa: E402
from tools.scalp_probe import ema  # noqa: E402


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def backtest(bars: list[Bar], n: int, stop_atr: float, tp_atr: float,
             max_bars: int, trend: bool, side_mode: str,
             spread: float) -> list[dict]:
    if len(bars) < max(n, 200 if trend else 21) + 2:
        return []
    closes = [b.c for b in bars]
    highs = [b.h for b in bars]
    lows = [b.l for b in bars]
    atr = atr_series(bars, 14)
    ema200 = ema(closes, 200) if trend else None
    trades: list[dict] = []
    pos = None
    pending = None
    for i, b in enumerate(bars):
        if pending is not None:
            side, atr_sig = pending
            pending = None
            risk = stop_atr * atr_sig
            if risk > 0:
                entry = b.o
                stop = entry - risk if side > 0 else entry + risk
                target = entry + tp_atr * atr_sig if side > 0 else entry - tp_atr * atr_sig
                if (side > 0 and target > entry) or (side < 0 and target < entry):
                    pos = {"side": side, "entry": entry, "stop": stop,
                           "target": target, "risk": risk, "i": i, "ts": b.t}

        if pos is not None:
            px = None
            reason = ""
            if pos["side"] > 0:
                if b.o >= pos["target"]:
                    px, reason = b.o, "target_gap"
                elif b.o <= pos["stop"]:
                    px, reason = b.o, "stop_gap"
                elif b.h >= pos["target"]:
                    px, reason = pos["target"], "target"
                elif b.l <= pos["stop"]:
                    px, reason = pos["stop"], "stop"
            else:
                if b.o <= pos["target"]:
                    px, reason = b.o, "target_gap"
                elif b.o >= pos["stop"]:
                    px, reason = b.o, "stop_gap"
                elif b.l <= pos["target"]:
                    px, reason = pos["target"], "target"
                elif b.h >= pos["stop"]:
                    px, reason = pos["stop"], "stop"
            if px is None and i - pos["i"] >= max_bars:
                px, reason = b.c, "time"
            if px is not None:
                gross = ((px - pos["entry"]) if pos["side"] > 0
                         else (pos["entry"] - px)) / pos["risk"]
                cost = max(0.05, spread / pos["risk"])
                trades.append({
                    "side": "LONG" if pos["side"] > 0 else "SHORT",
                    "entry": pos["entry"], "exit": px, "risk": pos["risk"],
                    "r": gross - cost, "exit_reason": reason,
                    "entry_ts": pos["ts"], "exit_ts": b.t,
                })
                pos = None

        if pos is None and i >= n:
            hh = max(highs[i - n:i])
            ll = min(lows[i - n:i])
            allow_long = side_mode != "short"
            allow_short = side_mode != "long"
            up = closes[i] > hh
            dn = closes[i] < ll
            if trend and ema200 is not None:
                up = up and closes[i] > ema200[i]
                dn = dn and closes[i] < ema200[i]
            if up and allow_long:
                pending = (1, atr[i])
            elif dn and allow_short:
                pending = (-1, atr[i])
    return trades


def _stats(trades: list[dict]) -> dict:
    rs = [_num(t["r"]) for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    equity = peak = dd = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    mean = sum(rs) / len(rs) if rs else 0.0
    var = sum((r - mean) ** 2 for r in rs) / (len(rs) - 1) if len(rs) > 1 else 0.0
    tstat = mean / math.sqrt(var / len(rs)) if var > 0 and rs else 0.0
    by_month: dict[str, float] = {}
    for t in trades:
        key = dt.datetime.fromtimestamp(float(t["exit_ts"]), dt.UTC).strftime("%Y-%m")
        by_month[key] = round(by_month.get(key, 0.0) + _num(t["r"]), 2)
    return {
        "trades": len(rs), "net_r": round(sum(rs), 2),
        "avg_r": round(mean, 4), "t_stat": round(tstat, 2),
        "win_rate": round(len(wins) / len(rs), 3) if rs else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "max_dd_r": round(dd, 2), "monthly_r": by_month,
    }


def _grid() -> list[dict]:
    return [dict(n=n, stop_atr=s, tp_atr=tp, max_bars=mb,
                 trend=tr, side=side)
            for n in (20, 50)
            for s in (1.0, 1.5)
            for tp in (2.0, 3.0)
            for mb in (96,)
            for tr in (False, True)
            for side in ("both", "long")]


def _fold_ranges(bars: list[Bar], is_days: int = 60, oos_days: int = 15):
    start, end = float(bars[0].t), float(bars[-1].t)
    step, span = oos_days * 86400, is_days * 86400
    t = start + span
    while t + step <= end:
        is_bars = [b for b in bars if t - span <= b.t < t]
        oos_bars = [b for b in bars if t <= b.t < t + step]
        if is_bars and oos_bars:
            yield is_bars, oos_bars
        t += step


def wfa(bars: list[Bar], spread: float) -> dict:
    all_oos: list[dict] = []
    folds = []
    for k, (is_bars, oos_bars) in enumerate(_fold_ranges(bars), 1):
        best = None
        for cfg in _grid():
            tr = backtest(is_bars, cfg["n"], cfg["stop_atr"], cfg["tp_atr"],
                          cfg["max_bars"], cfg["trend"], cfg["side"], spread)
            if len(tr) < 3:
                continue
            net = sum(t["r"] for t in tr)
            if best is None or net > best[0]:
                best = (net, cfg)
        if best is None:
            continue
        cfg = best[1]
        tr = backtest(oos_bars, cfg["n"], cfg["stop_atr"], cfg["tp_atr"],
                      cfg["max_bars"], cfg["trend"], cfg["side"], spread)
        all_oos.extend(tr)
        folds.append({"fold": k, "is_net": round(best[0], 2),
                      "oos_net": round(sum(t["r"] for t in tr), 2),
                      "oos_trades": len(tr), "params": cfg})
    return {"wfa": _stats(all_oos), "folds": folds}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="US500")
    ap.add_argument("--terminal", default="C:/Program Files/MetaTrader 5/terminal64.exe")
    ap.add_argument("--start", default="2025-11-01")
    ap.add_argument("--end", default="2026-11-06")
    ap.add_argument("--spread", type=float, default=None)
    ap.add_argument("--stress", action="store_true")
    ap.add_argument("--mode", choices=("sweep", "wfa", "both"), default="both")
    args = ap.parse_args()

    start = dt.datetime.strptime(args.start, "%Y-%m-%d")
    end = dt.datetime.strptime(args.end, "%Y-%m-%d")
    bars, term_spread = load_m15(args.terminal, args.symbol, start, end)
    if len(bars) < 1000:
        raise SystemExit(f"мало баров: {len(bars)}")
    spread = term_spread if args.spread is None else max(0.0, args.spread)
    spread = spread * (2.0 if args.stress else 1.0)

    out: dict = {"config": {"symbol": args.symbol, "bars": len(bars),
                            "first": dt.datetime.fromtimestamp(bars[0].t, dt.UTC).isoformat(),
                            "last": dt.datetime.fromtimestamp(bars[-1].t, dt.UTC).isoformat(),
                            "spread": round(spread, 4), "stress": args.stress}}
    if args.mode in ("sweep", "both"):
        rows = []
        for cfg in _grid():
            tr = backtest(bars, cfg["n"], cfg["stop_atr"], cfg["tp_atr"],
                          cfg["max_bars"], cfg["trend"], cfg["side"], spread)
            rows.append({**cfg, **_stats(tr)})
        out["sweep"] = sorted(rows, key=lambda r: -r["net_r"])
    if args.mode in ("wfa", "both"):
        out["wfa"] = wfa(bars, spread)

    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n{args.symbol}: {len(bars)} баров M15, спред {spread:.2f}, stress={args.stress}",
          file=sys.stderr)
    if "sweep" in out:
        print(f"{'n':>4}{'stop':>6}{'tp':>5}{'trend':>7}{'side':>6}{'сделки':>7}"
              f"{'net R':>8}{'t':>6}{'PF':>6}{'DD':>7}", file=sys.stderr)
        for r in out["sweep"][:8]:
            pf = "—" if r["profit_factor"] is None else f"{r['profit_factor']:.2f}"
            print(f"{r['n']:>4}{r['stop_atr']:>6.1f}{r['tp_atr']:>5.1f}"
                  f"{'EMA' if r['trend'] else 'off':>7}{r['side']:>6}"
                  f"{r['trades']:>7}{r['net_r']:>8.1f}{r['t_stat']:>6.2f}"
                  f"{pf:>6}{r['max_dd_r']:>7.1f}", file=sys.stderr)
    if "wfa" in out:
        w = out["wfa"]["wfa"]
        pf = "—" if w["profit_factor"] is None else f"{w['profit_factor']:.2f}"
        print(f"WFA OOS: сделки {w['trades']} · net {w['net_r']:+.2f}R · "
              f"avg {w['avg_r']:+.3f}R · t {w['t_stat']:.2f} · PF {pf} · "
              f"DD {w['max_dd_r']:.1f}R", file=sys.stderr)
        print("месяцы:", w["monthly_r"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
