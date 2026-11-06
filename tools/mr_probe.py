#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mean reversion на золоте как отдельное направление (не сетка).

Правила заранее зафиксированы, без подгонки под ролик:

  * на закрытии M15 считаем z = (close - SMA(n)) / std(n);
  * long при z <= -k, short при z >= +k, вход на открытии следующего бара;
  * цель — SMA на момент сигнала, стоп — stop_atr * ATR(14), максимум max_bars;
  * одна позиция, без усреднения и пирамидинга;
  * издержки: cost_r = max(0.05, (spread + slippage) / risk), как в costs.py.

Стоп проверяется раньше цели (консервативно). Режимы:
  sweep — сетка параметров на всём периоде (in-sample, только для ориентира);
  wfa   — walk-forward: 3 месяца IS, следующий месяц OOS; параметры выбираются
          на IS и переносятся на OOS без переобучения.

Инструмент ничего не пишет на диск: JSON в stdout, таблица в stderr.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from dataclasses import dataclass, field

TF_MINUTES = 15
CONTRACT = 100.0


@dataclass
class Bar:
    t: int
    o: float
    h: float
    l: float
    c: float


@dataclass
class MRTrade:
    side: int
    entry: float
    exit: float
    stop: float
    target: float
    ts_entry: int
    ts_exit: int
    risk: float
    gross_r: float
    cost_r: float
    net_r: float
    reason: str


@dataclass
class Stats:
    trades: list[MRTrade] = field(default_factory=list)

    def summary(self) -> dict:
        rs = [t.net_r for t in self.trades]
        gross = sum(t.gross_r for t in self.trades)
        cost = sum(t.cost_r for t in self.trades)
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        gross_win = sum(wins)
        gross_loss = -sum(losses)
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in rs:
            equity += r
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        mean = sum(rs) / len(rs) if rs else 0.0
        var = sum((r - mean) ** 2 for r in rs) / (len(rs) - 1) if len(rs) > 1 else 0.0
        tstat = mean / math.sqrt(var / len(rs)) if var > 0 and rs else 0.0
        return {
            "trades": len(rs),
            "net_r": round(sum(rs), 3),
            "gross_r": round(gross, 3),
            "cost_r": round(cost, 3),
            "avg_r": round(mean, 4),
            "t_stat": round(tstat, 2),
            "win_rate": round(len(wins) / len(rs), 4) if rs else 0.0,
            "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
            "max_dd_r": round(max_dd, 2),
            "monthly_r": monthly_r(self.trades),
        }


def monthly_r(trades: list[MRTrade]) -> dict:
    out: dict[str, float] = {}
    for t in trades:
        key = dt.datetime.fromtimestamp(t.ts_exit, dt.UTC).strftime("%Y-%m")
        out[key] = round(out.get(key, 0.0) + t.net_r, 2)
    return out


def rolling_mean_std(vals: list[float], n: int) -> tuple[list[float | None], list[float | None]]:
    mean: list[float | None] = [None] * len(vals)
    std: list[float | None] = [None] * len(vals)
    s = 0.0
    s2 = 0.0
    for i, v in enumerate(vals):
        s += v
        s2 += v * v
        if i >= n:
            old = vals[i - n]
            s -= old
            s2 -= old * old
        if i >= n - 1:
            m = s / n
            var = max(0.0, s2 / n - m * m)
            mean[i] = m
            std[i] = math.sqrt(var)
    return mean, std


def er_series(closes: list[float], n: int = 20) -> list[float | None]:
    """Efficiency ratio: |close_i - close_{i-n}| / сумма |шагов| за n баров."""
    out: list[float | None] = [None] * len(closes)
    for i in range(n, len(closes)):
        change = abs(closes[i] - closes[i - n])
        vol = sum(abs(closes[k] - closes[k - 1]) for k in range(i - n + 1, i + 1))
        out[i] = change / vol if vol > 0 else 0.0
    return out


def atr_series(bars: list[Bar], n: int = 14) -> list[float | None]:
    trs: list[float] = []
    prev = None
    for b in bars:
        if prev is None:
            trs.append(b.h - b.l)
        else:
            trs.append(max(b.h - b.l, abs(b.h - prev), abs(b.l - prev)))
        prev = b.c
    out: list[float | None] = [None] * len(bars)
    s = 0.0
    for i, tr in enumerate(trs):
        s += tr
        if i >= n:
            s -= trs[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def backtest(bars: list[Bar], n: int, k: float, stop_atr: float, max_bars: int,
             spread_eff: float, er_max: float | None = None) -> Stats:
    closes = [b.c for b in bars]
    sma, std = rolling_mean_std(closes, n)
    atr = atr_series(bars, 14)
    er = er_series(closes, 20) if er_max is not None else None
    trades: list[MRTrade] = []
    pos = None
    pending = None

    for i, b in enumerate(bars):
        if pending is not None:
            side, target, atr_sig = pending
            pending = None
            entry = b.o
            risk = stop_atr * atr_sig
            if risk > 0:
                stop = entry - risk if side > 0 else entry + risk
                valid = (side > 0 and target > entry) or (side < 0 and target < entry)
                if valid:
                    pos = {"side": side, "entry": entry, "stop": stop,
                           "target": target, "risk": risk, "i": i,
                           "ts": b.t}

        if pos is not None:
            px = None
            reason = ""
            if pos["side"] > 0:
                # открытие за целью — первое событие бара, раньше любого стопа
                if b.o >= pos["target"]:
                    px, reason = b.o, "target_gap"
                elif b.o <= pos["stop"]:
                    px, reason = b.o, "stop_gap"
                elif b.l <= pos["stop"]:
                    px, reason = pos["stop"], "stop"
                elif b.h >= pos["target"]:
                    px, reason = pos["target"], "target"
            else:
                if b.o <= pos["target"]:
                    px, reason = b.o, "target_gap"
                elif b.o >= pos["stop"]:
                    px, reason = b.o, "stop_gap"
                elif b.h >= pos["stop"]:
                    px, reason = pos["stop"], "stop"
                elif b.l <= pos["target"]:
                    px, reason = pos["target"], "target"
            if px is None and i - pos["i"] >= max_bars:
                px, reason = b.c, "time"
            if px is not None:
                if pos["side"] > 0:
                    gross = (px - pos["entry"]) / pos["risk"]
                else:
                    gross = (pos["entry"] - px) / pos["risk"]
                cost = max(0.05, spread_eff / pos["risk"])
                trades.append(MRTrade(pos["side"], pos["entry"], px, pos["stop"],
                                      pos["target"], pos["ts"], b.t, pos["risk"],
                                      gross, cost, gross - cost, reason))
                pos = None

        if pos is None and i >= max(n, 14):
            regime_ok = er_max is None or (er is not None and er[i] is not None
                                           and er[i] <= er_max)
            if regime_ok and std[i] and std[i] > 0 and atr[i] and atr[i] > 0:
                z = (b.c - sma[i]) / std[i]
                if z <= -k:
                    pending = (1, sma[i], atr[i])
                elif z >= k:
                    pending = (-1, sma[i], atr[i])

    return Stats(trades)


# ------------------------------------------------------------------ данные

def load_m15(terminal: str, symbol: str, start: dt.datetime,
             end: dt.datetime) -> tuple[list[Bar], float]:
    import MetaTrader5 as mt5

    if not mt5.initialize(path=terminal):
        raise RuntimeError(f"MT5 не инициализировался: {mt5.last_error()}")
    try:
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"символ {symbol} не выбран")
        info = mt5.symbol_info(symbol)
        point = float(getattr(info, "point", 0.01) or 0.01)
        spread = float(getattr(info, "spread", 0) or 0) * point
        rows: dict[int, Bar] = {}
        cur = start
        while cur < end:
            nxt = min(end, cur + dt.timedelta(days=45))
            rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, cur, nxt)
            for r in rates if rates is not None else []:
                rows[int(r["time"])] = Bar(int(r["time"]), float(r["open"]),
                                           float(r["high"]), float(r["low"]),
                                           float(r["close"]))
            cur = nxt
        return [rows[t] for t in sorted(rows)], spread
    finally:
        mt5.shutdown()


def _month_slices(bars: list[Bar]) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    start = 0
    cur = dt.datetime.fromtimestamp(bars[0].t, dt.UTC).strftime("%Y-%m")
    for i, b in enumerate(bars):
        key = dt.datetime.fromtimestamp(b.t, dt.UTC).strftime("%Y-%m")
        if key != cur:
            out.append((cur, start, i))
            start = i
            cur = key
    out.append((cur, start, len(bars)))
    return out


def sweep(bars: list[Bar], grid: list[tuple[int, float, float, float | None]],
          spread_eff: float, max_bars: int) -> list[dict]:
    rows = []
    for n, k, stop_atr, er_max in grid:
        s = backtest(bars, n, k, stop_atr, max_bars, spread_eff, er_max).summary()
        rows.append({"n": n, "k": k, "stop_atr": stop_atr, "er_max": er_max,
                     "net_r": s["net_r"], "trades": s["trades"],
                     "avg_r": s["avg_r"], "t_stat": s["t_stat"],
                     "win_rate": s["win_rate"], "pf": s["profit_factor"],
                     "max_dd_r": s["max_dd_r"], "monthly_r": s["monthly_r"]})
    return rows


def wfa(bars: list[Bar], grid: list[tuple[int, float, float, float | None]],
        spread_eff: float, max_bars: int, is_months: int, min_trades: int) -> dict:
    slices = _month_slices(bars)
    all_trades: list[MRTrade] = []
    folds = []
    for idx in range(is_months, len(slices)):
        key, o_start, o_end = slices[idx]
        i_start = slices[idx - is_months][1]
        is_bars = bars[i_start:o_start]
        oos_bars = bars[o_start:o_end]
        best = None
        for n, k, stop_atr, er_max in grid:
            s = backtest(is_bars, n, k, stop_atr, max_bars, spread_eff, er_max)
            if len(s.trades) < min_trades:
                continue
            r = sum(t.net_r for t in s.trades)
            if best is None or r > best[0]:
                best = (r, n, k, stop_atr, er_max)
        if best is None:
            folds.append({"month": key, "skipped": True})
            continue
        _, n, k, stop_atr, er_max = best
        oos = backtest(oos_bars, n, k, stop_atr, max_bars, spread_eff, er_max)
        all_trades.extend(oos.trades)
        folds.append({
            "month": key, "n": n, "k": k, "stop_atr": stop_atr,
            "er_max": er_max,
            "is_r": round(best[0], 2),
            "oos_r": round(sum(t.net_r for t in oos.trades), 2),
            "oos_trades": len(oos.trades),
        })
    return {"wfa": Stats(all_trades).summary(), "folds": folds}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--terminal", default="C:/Program Files/MetaTrader 5/terminal64.exe")
    ap.add_argument("--start", default="2025-11-01")
    ap.add_argument("--end", default="2026-11-05")
    ap.add_argument("--spread", type=float, default=None)
    ap.add_argument("--slippage", type=float, default=0.06)
    ap.add_argument("--stress", action="store_true")
    ap.add_argument("--mode", choices=("sweep", "wfa", "both"), default="both")
    ap.add_argument("--n-list", default="20,50,100")
    ap.add_argument("--k-list", default="1.5,2.0,2.5")
    ap.add_argument("--stop-list", default="1.0,2.0")
    ap.add_argument("--er-list", default="off,0.3,0.4",
                    help="порог ER (off = без фильтра)")
    ap.add_argument("--max-bars", type=int, default=96)
    ap.add_argument("--is-months", type=int, default=3)
    ap.add_argument("--min-trades", type=int, default=10)
    args = ap.parse_args()

    start = dt.datetime.strptime(args.start, "%Y-%m-%d")
    end = dt.datetime.strptime(args.end, "%Y-%m-%d")
    bars, term_spread = load_m15(args.terminal, args.symbol, start, end)
    if len(bars) < 500:
        raise SystemExit(f"мало баров: {len(bars)}")
    spread = term_spread if args.spread is None else max(0.0, args.spread)
    spread_eff = (spread + args.slippage) * (2.0 if args.stress else 1.0)

    def _er(v: str) -> float | None:
        v = v.strip().lower()
        return None if v in ("off", "none", "") else float(v)

    grid = [(int(n), float(k), float(s), _er(e))
            for n in args.n_list.split(",") for k in args.k_list.split(",")
            for s in args.stop_list.split(",") for e in args.er_list.split(",")]
    out: dict = {
        "config": {"symbol": args.symbol, "bars": len(bars),
                   "first": dt.datetime.fromtimestamp(bars[0].t, dt.UTC).isoformat(),
                   "last": dt.datetime.fromtimestamp(bars[-1].t, dt.UTC).isoformat(),
                   "spread_terminal": round(term_spread, 4),
                   "spread_used": round(spread_eff, 4),
                   "slippage": args.slippage, "stress": args.stress,
                   "max_bars": args.max_bars, "grid": len(grid)},
    }
    if args.mode in ("sweep", "both"):
        rows = sweep(bars, grid, spread_eff, args.max_bars)
        out["sweep"] = sorted(rows, key=lambda r: -r["net_r"])
    if args.mode in ("wfa", "both"):
        out["wfa"] = wfa(bars, grid, spread_eff, args.max_bars,
                         args.is_months, args.min_trades)

    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n{args.symbol}: {len(bars)} баров M15, спред {spread_eff:.3f} "
          f"(+слиппедж, stress={args.stress})", file=sys.stderr)
    if "sweep" in out:
        print(f"\n{'n':>4}{'k':>6}{'stop':>6}{'ER':>6}{'сделки':>8}{'net R':>8}"
              f"{'avg R':>8}{'t':>6}{'win%':>7}{'PF':>6}{'DD R':>7}", file=sys.stderr)
        for r in out["sweep"][:8]:
            pf = "—" if r["pf"] is None else f"{r['pf']:.2f}"
            er = "off" if r["er_max"] is None else f"{r['er_max']:.1f}"
            print(f"{r['n']:>4}{r['k']:>6.1f}{r['stop_atr']:>6.1f}{er:>6}{r['trades']:>8}"
                  f"{r['net_r']:>8.1f}{r['avg_r']:>8.3f}{r['t_stat']:>6.2f}"
                  f"{r['win_rate'] * 100:>7.1f}{pf:>6}{r['max_dd_r']:>7.1f}",
                  file=sys.stderr)
    if "wfa" in out:
        w = out["wfa"]["wfa"]
        pf = "—" if w["profit_factor"] is None else f"{w['profit_factor']:.2f}"
        print(f"\nWFA OOS: сделки {w['trades']}  net {w['net_r']:+.1f}R  "
              f"avg {w['avg_r']:+.3f}R  t {w['t_stat']:.2f}  PF {pf}  "
              f"DD {w['max_dd_r']:.1f}R", file=sys.stderr)
        print(f"{'месяц':<9}{'n':>4}{'k':>6}{'stop':>6}{'ER':>6}{'IS R':>8}"
              f"{'OOS R':>8}{'сделки':>8}", file=sys.stderr)
        for f in out["wfa"]["folds"]:
            if f.get("skipped"):
                print(f"{f['month']:<9}нет данных на IS", file=sys.stderr)
                continue
            er = "off" if f["er_max"] is None else f"{f['er_max']:.1f}"
            print(f"{f['month']:<9}{f['n']:>4}{f['k']:>6.1f}{f['stop_atr']:>6.1f}{er:>6}"
                  f"{f['is_r']:>8.1f}{f['oos_r']:>8.1f}{f['oos_trades']:>8}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
