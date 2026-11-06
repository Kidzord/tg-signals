#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Механическая версия «свип уровня + возврат + частичный фикс» с их скринов.

Правила:
  * уровень — PDH/PDL прошлого дня или Donchian N;
  * short: бар прокалывает сопротивление и закрывается ниже; long — симметрично;
  * вход на открытии следующего бара;
  * стоп за экстремум свипа + stop_buf * ATR;
  * 50% фиксируется на partial_r R, стоп переводится в БУ;
  * остаток ведётся трейлом trail_atr * ATR или закрывается по tp_r;
  * издержки max(0.05, spread/risk); без усреднения.

Режимы: sweep (сетка) и wfa (60д IS -> 15д OOS). JSON stdout, таблица stderr.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.grid_probe import Bar, load_m5  # noqa: E402
from tools.mr_probe import atr_series  # noqa: E402


def _stats(trades: list[dict]) -> dict:
    rs = [float(t["r"]) for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gw, gl = sum(wins), -sum(losses)
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    mean = sum(rs) / len(rs) if rs else 0.0
    var = sum((r - mean) ** 2 for r in rs) / (len(rs) - 1) if len(rs) > 1 else 0.0
    tstat = mean / math.sqrt(var / len(rs)) if var > 0 and rs else 0.0
    by_month: dict[str, float] = {}
    for t in trades:
        key = dt.datetime.fromtimestamp(t["exit_ts"], dt.UTC).strftime("%Y-%m")
        by_month[key] = round(by_month.get(key, 0.0) + t["r"], 2)
    return {"trades": len(rs), "net_r": round(sum(rs), 2),
            "avg_r": round(mean, 4), "t_stat": round(tstat, 2),
            "win_rate": round(len(wins) / len(rs), 3) if rs else 0.0,
            "profit_factor": round(gw / gl, 2) if gl > 0 else None,
            "max_dd_r": round(dd, 2), "monthly_r": by_month}


def _levels(bars: list[Bar], kind: str):
    n = len(bars)
    if kind in ("pdh", "pdl"):
        dh: dict[dt.date, float] = {}
        dl: dict[dt.date, float] = {}
        days: list[dt.date] = []
        for b in bars:
            d = dt.datetime.fromtimestamp(b.t, dt.UTC).date()
            if d not in dh:
                dh[d], dl[d] = b.h, b.l
                days.append(d)
            else:
                dh[d] = max(dh[d], b.h)
                dl[d] = min(dl[d], b.l)
        prev_h: dict[dt.date, float] = {}
        prev_l: dict[dt.date, float] = {}
        for a, b in zip(days, days[1:]):
            prev_h[b], prev_l[b] = dh[a], dl[a]
        res = [prev_h.get(dt.datetime.fromtimestamp(b.t, dt.UTC).date()) for b in bars]
        sup = [prev_l.get(dt.datetime.fromtimestamp(b.t, dt.UTC).date()) for b in bars]
        return res, sup
    width = 20 if kind == "don20" else 50
    res = [None] * n
    sup = [None] * n
    for i in range(width, n):
        res[i] = max(b.h for b in bars[i - width:i])
        sup[i] = min(b.l for b in bars[i - width:i])
    return res, sup


def backtest(bars: list[Bar], *, level: str, tp_r: float, trail_atr: float,
             spread: float, stop_buf: float = 0.3, partial_r: float = 2.0,
             partial_frac: float = 0.5, max_bars: int = 96) -> list[dict]:
    atr = atr_series(bars, 14)
    res, sup = _levels(bars, level)
    trades: list[dict] = []
    pos = None
    pending = None

    for i, b in enumerate(bars):
        if pending is not None:
            side, stop, risk0 = pending
            pending = None
            entry = b.o
            if (side > 0 and entry > stop) or (side < 0 and entry < stop):
                risk = abs(entry - stop)
                if risk > 0:
                    pos = {"side": side, "entry": entry, "stop": stop,
                           "risk": risk, "i": i, "ts": b.t,
                           "partial": False, "r": 0.0, "done": 0.0,
                           "target": entry + side * tp_r * risk}

        if pos is not None:
            side = pos["side"]
            px = None
            why = ""
            if side > 0:
                if b.o <= pos["stop"]:
                    px, why = b.o, "stop_gap"
                elif b.l <= pos["stop"]:
                    px, why = pos["stop"], "stop"
                elif not pos["partial"] and b.h >= pos["entry"] + partial_r * pos["risk"]:
                    pos["partial"] = True
                    pos["stop"] = pos["entry"]
                    pos["r"] += partial_frac * partial_r
                    pos["done"] += partial_frac
                if px is None and b.h >= pos["target"]:
                    px, why = pos["target"], "target"
            else:
                if b.o >= pos["stop"]:
                    px, why = b.o, "stop_gap"
                elif b.h >= pos["stop"]:
                    px, why = pos["stop"], "stop"
                elif not pos["partial"] and b.l <= pos["entry"] - partial_r * pos["risk"]:
                    pos["partial"] = True
                    pos["stop"] = pos["entry"]
                    pos["r"] += partial_frac * partial_r
                    pos["done"] += partial_frac
                if px is None and b.l <= pos["target"]:
                    px, why = pos["target"], "target"

            if px is None and trail_atr > 0 and pos["partial"]:
                if side > 0:
                    pos["stop"] = max(pos["stop"], b.c - trail_atr * (atr[i] or 0))
                else:
                    pos["stop"] = min(pos["stop"], b.c + trail_atr * (atr[i] or 0))
            if px is None and i - pos["i"] >= max_bars:
                px, why = b.c, "time"
            if px is not None:
                rem = 1.0 - pos["done"]
                exit_r = ((px - pos["entry"]) if side > 0 else
                          (pos["entry"] - px)) / pos["risk"]
                gross = pos["r"] + rem * exit_r
                cost = max(0.05, spread / pos["risk"])
                trades.append({"side": "LONG" if side > 0 else "SHORT",
                               "entry": pos["entry"], "exit": px, "risk": pos["risk"],
                               "r": gross - cost, "reason": why,
                               "entry_ts": pos["ts"], "exit_ts": b.t})
                pos = None

        if pos is None and i >= 55:
            r, s = res[i], sup[i]
            if r is None or s is None or not atr[i]:
                continue
            short = b.h > r and b.c < r
            long = b.l < s and b.c > s
            if short and long:
                continue  # оба уровня в одном баре — неоднозначно
            if short:
                pending = (-1, b.h + stop_buf * atr[i], atr[i])
            elif long:
                pending = (1, b.l - stop_buf * atr[i], atr[i])
    return trades


def _grid() -> list[dict]:
    return [dict(level=lv, tp_r=tp, trail_atr=tr)
            for lv in ("pdh", "don20", "don50")
            for tp in (2.0, 3.0)
            for tr in (0.0, 1.0)]


def _fold_ranges(bars: list[Bar], is_days: int = 60, oos_days: int = 15):
    start, end = float(bars[0].t), float(bars[-1].t)
    span, step = is_days * 86400, oos_days * 86400
    t = start + span
    while t + step <= end:
        a = [b for b in bars if t - span <= b.t < t]
        c = [b for b in bars if t <= b.t < t + step]
        if a and c:
            yield a, c
        t += step


def wfa(bars: list[Bar], spread: float) -> dict:
    oos: list[dict] = []
    folds = []
    for k, (is_bars, oos_bars) in enumerate(_fold_ranges(bars), 1):
        best = None
        for cfg in _grid():
            tr = backtest(is_bars, spread=spread, **cfg)
            if len(tr) < 3:
                continue
            net = sum(t["r"] for t in tr)
            if best is None or net > best[0]:
                best = (net, cfg)
        if best is None:
            continue
        cfg = best[1]
        tr = backtest(oos_bars, spread=spread, **cfg)
        oos.extend(tr)
        folds.append({"fold": k, "is_net": round(best[0], 2),
                      "oos_net": round(sum(t["r"] for t in tr), 2),
                      "trades": len(tr), "params": cfg})
    return {"wfa": _stats(oos), "folds": folds}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--terminal", default="C:/Program Files/MetaTrader 5/terminal64.exe")
    ap.add_argument("--start", default="2025-11-01")
    ap.add_argument("--end", default="2026-11-06")
    ap.add_argument("--spread", type=float, default=None)
    ap.add_argument("--stress", action="store_true")
    ap.add_argument("--mode", choices=("sweep", "wfa", "both"), default="both")
    args = ap.parse_args()

    start = dt.datetime.strptime(args.start, "%Y-%m-%d")
    end = dt.datetime.strptime(args.end, "%Y-%m-%d")
    bars, term_spread = load_m5(args.terminal, args.symbol, start, end)
    if len(bars) < 1000:
        raise SystemExit(f"мало баров: {len(bars)}")
    spread = term_spread if args.spread is None else max(0.0, args.spread)
    spread *= 2.0 if args.stress else 1.0

    out: dict = {"config": {"symbol": args.symbol, "bars": len(bars),
                            "first": dt.datetime.fromtimestamp(bars[0].t, dt.UTC).isoformat(),
                            "last": dt.datetime.fromtimestamp(bars[-1].t, dt.UTC).isoformat(),
                            "spread": round(spread, 4), "stress": args.stress}}
    if args.mode in ("sweep", "both"):
        rows = [{**cfg, **_stats(backtest(bars, spread=spread, **cfg))}
                for cfg in _grid()]
        out["sweep"] = sorted(rows, key=lambda r: -r["net_r"])
    if args.mode in ("wfa", "both"):
        out["wfa"] = wfa(bars, spread)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n{args.symbol}: {len(bars)} баров M5, спред {spread:.3f}, stress={args.stress}",
          file=sys.stderr)
    if "sweep" in out:
        print(f"{'уровень':<8}{'tp':>5}{'trail':>6}{'сделки':>7}{'net R':>8}"
              f"{'avg R':>8}{'t':>6}{'win%':>7}{'PF':>6}{'DD':>7}", file=sys.stderr)
        for r in out["sweep"][:8]:
            pf = "—" if r["profit_factor"] is None else f"{r['profit_factor']:.2f}"
            print(f"{r['level']:<8}{r['tp_r']:>5.1f}{r['trail_atr']:>6.1f}"
                  f"{r['trades']:>7}{r['net_r']:>8.1f}{r['avg_r']:>8.3f}"
                  f"{r['t_stat']:>6.2f}{r['win_rate'] * 100:>7.1f}{pf:>6}"
                  f"{r['max_dd_r']:>7.1f}", file=sys.stderr)
    if "wfa" in out:
        w = out["wfa"]["wfa"]; pf = "—" if w["profit_factor"] is None else f"{w['profit_factor']:.2f}"
        print(f"WFA OOS: сделки {w['trades']} · net {w['net_r']:+.2f}R · avg "
              f"{w['avg_r']:+.3f}R · t {w['t_stat']:.2f} · PF {pf} · DD {w['max_dd_r']:.1f}R",
              file=sys.stderr)
        print("месяцы:", w["monthly_r"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
