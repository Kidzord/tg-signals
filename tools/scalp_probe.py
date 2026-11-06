#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Скальпинг на M5: три архетипа с ATR-стопом/тейком и издержками.

Проверяем не «сетку каждую свечу», а обычные скальпинг-входы:

  donchian  — пробой максимума/минимума предыдущих n баров;
  pullback  — откат к EMA(fast) в направлении EMA(fast) > EMA(slow);
  fade      — фейд импульсной свечи range >= k * ATR(14) (закрытие у края).

Вход на открытии следующего бара, TP/SL в ATR, максимум max_bars, одна
позиция без усреднения. Стоп проверяется раньше тейка. Издержки:
cost = max(0.05, (spread + slippage) / risk).

Режимы: sweep (сетка in-sample) и wfa (3 месяца IS -> месяц OOS).
Инструмент ничего не пишет на диск: JSON в stdout, таблицы в stderr.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.grid_probe import Bar, load_m5  # noqa: E402


@dataclass
class Trade:
    kind: str
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
    trades: list[Trade] = field(default_factory=list)

    def summary(self) -> dict:
        rs = [t.net_r for t in self.trades]
        gross = sum(t.gross_r for t in self.trades)
        cost = sum(t.cost_r for t in self.trades)
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        gw = sum(wins)
        gl = -sum(losses)
        equity = peak = dd = 0.0
        for r in rs:
            equity += r
            peak = max(peak, equity)
            dd = max(dd, peak - equity)
        mean = sum(rs) / len(rs) if rs else 0.0
        var = sum((r - mean) ** 2 for r in rs) / (len(rs) - 1) if len(rs) > 1 else 0.0
        tstat = mean / math.sqrt(var / len(rs)) if var > 0 and rs else 0.0
        return {
            "trades": len(rs), "net_r": round(sum(rs), 2),
            "gross_r": round(gross, 2), "cost_r": round(cost, 2),
            "avg_r": round(mean, 4), "t_stat": round(tstat, 2),
            "win_rate": round(len(wins) / len(rs), 4) if rs else 0.0,
            "profit_factor": round(gw / gl, 3) if gl > 0 else None,
            "max_dd_r": round(dd, 2),
            "monthly_r": _monthly(self.trades),
        }


def _monthly(trades: list[Trade]) -> dict:
    out: dict[str, float] = {}
    for t in trades:
        key = dt.datetime.fromtimestamp(t.ts_exit, dt.UTC).strftime("%Y-%m")
        out[key] = round(out.get(key, 0.0) + t.net_r, 2)
    return out


def atr_series(bars: list[Bar], n: int = 14) -> list[float | None]:
    trs: list[float] = []
    prev = None
    for b in bars:
        trs.append(b.h - b.l if prev is None
                   else max(b.h - b.l, abs(b.h - prev), abs(b.l - prev)))
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


def ema(vals: list[float], n: int) -> list[float]:
    alpha = 2.0 / (n + 1.0)
    out = [0.0] * len(vals)
    if not vals:
        return out
    e = vals[0]
    for i, v in enumerate(vals):
        e = v * alpha + e * (1.0 - alpha)
        out[i] = e
    return out


def _donchian(bars: list[Bar], cfg: dict, atr: list[float | None]):
    highs = [b.h for b in bars]
    lows = [b.l for b in bars]
    closes = [b.c for b in bars]
    n = int(cfg["n"])

    def sig(i: int) -> int:
        if i < n:
            return 0
        hh = max(highs[i - n:i])
        ll = min(lows[i - n:i])
        if closes[i] > hh:
            return 1
        if closes[i] < ll:
            return -1
        return 0

    return sig, n


def _pullback(bars: list[Bar], cfg: dict, atr: list[float | None]):
    closes = [b.c for b in bars]
    highs = [b.h for b in bars]
    lows = [b.l for b in bars]
    fast = ema(closes, int(cfg["fast"]))
    slow = ema(closes, int(cfg["slow"]))
    warm = int(cfg["slow"])

    def sig(i: int) -> int:
        if i < warm:
            return 0
        if fast[i] > slow[i] and lows[i] <= fast[i] and closes[i] > fast[i]:
            return 1
        if fast[i] < slow[i] and highs[i] >= fast[i] and closes[i] < fast[i]:
            return -1
        return 0

    return sig, warm


def _fade(bars: list[Bar], cfg: dict, atr: list[float | None]):
    k = float(cfg["k"])
    warm = 20

    def sig(i: int) -> int:
        if i < warm or not atr[i] or atr[i] <= 0:
            return 0
        rng = bars[i].h - bars[i].l
        if rng < k * atr[i]:
            return 0
        pos = (bars[i].c - bars[i].l) / rng if rng > 0 else 0.5
        if pos >= 0.75:
            return -1
        if pos <= 0.25:
            return 1
        return 0

    return sig, warm


SIGNALS = {"donchian": _donchian, "pullback": _pullback, "fade": _fade}


def backtest(bars: list[Bar], kind: str, cfg: dict, spread_eff: float) -> Stats:
    atr = atr_series(bars, 14)
    sig, warm = SIGNALS[kind](bars, cfg, atr)
    tp_mult = float(cfg["tp_atr"])
    sl_mult = float(cfg["sl_atr"])
    max_bars = int(cfg["max_bars"])
    trades: list[Trade] = []
    pos = None
    pending = None

    for i, b in enumerate(bars):
        if pending is not None:
            side, atr_sig = pending
            pending = None
            risk = sl_mult * atr_sig
            if risk > 0:
                entry = b.o
                stop = entry - risk if side > 0 else entry + risk
                target = entry + tp_mult * atr_sig if side > 0 else entry - tp_mult * atr_sig
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
                gross = ((px - pos["entry"]) if pos["side"] > 0
                         else (pos["entry"] - px)) / pos["risk"]
                cost = max(0.05, spread_eff / pos["risk"])
                trades.append(Trade(kind, pos["side"], pos["entry"], px,
                                    pos["stop"], pos["target"], pos["ts"], b.t,
                                    pos["risk"], gross, cost, gross - cost, reason))
                pos = None

        if pos is None and i >= warm:
            side = sig(i)
            if side and atr[i] and atr[i] > 0:
                pending = (side, atr[i])

    return Stats(trades)


def grid_for(kind: str) -> list[dict]:
    if kind == "donchian":
        return [{"n": n, "tp_atr": tp, "sl_atr": 1.0, "max_bars": 24}
                for n in (12, 24, 48) for tp in (1.0, 1.5)]
    if kind == "pullback":
        return [{"fast": f, "slow": s, "tp_atr": tp, "sl_atr": 1.0, "max_bars": 24}
                for f, s in ((9, 21), (21, 50)) for tp in (1.0, 1.5)]
    if kind == "fade":
        return [{"k": k, "tp_atr": tp, "sl_atr": 1.0, "max_bars": 12}
                for k in (1.5, 2.0) for tp in (1.0, 1.5)]
    raise ValueError(kind)


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


def sweep(bars: list[Bar], kind: str, spread_eff: float) -> list[dict]:
    rows = []
    for cfg in grid_for(kind):
        s = backtest(bars, kind, cfg, spread_eff).summary()
        rows.append({**cfg, **{k: s[k] for k in
                               ("trades", "net_r", "avg_r", "t_stat",
                                "win_rate", "profit_factor", "max_dd_r")}})
    return sorted(rows, key=lambda r: -r["net_r"])


def wfa(bars: list[Bar], kind: str, spread_eff: float, is_months: int,
        min_trades: int) -> dict:
    slices = _month_slices(bars)
    all_trades: list[Trade] = []
    folds = []
    for idx in range(is_months, len(slices)):
        key, o_start, o_end = slices[idx]
        is_bars = bars[slices[idx - is_months][1]:o_start]
        oos_bars = bars[o_start:o_end]
        best = None
        for cfg in grid_for(kind):
            s = backtest(is_bars, kind, cfg, spread_eff)
            if len(s.trades) < min_trades:
                continue
            r = sum(t.net_r for t in s.trades)
            if best is None or r > best[0]:
                best = (r, cfg)
        if best is None:
            folds.append({"month": key, "skipped": True})
            continue
        oos = backtest(oos_bars, kind, best[1], spread_eff)
        all_trades.extend(oos.trades)
        folds.append({"month": key, **best[1], "is_r": round(best[0], 2),
                      "oos_r": round(sum(t.net_r for t in oos.trades), 2),
                      "oos_trades": len(oos.trades)})
    return {"wfa": Stats(all_trades).summary(), "folds": folds}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--terminal", default="C:/Program Files/MetaTrader 5/terminal64.exe")
    ap.add_argument("--start", default="2025-11-01")
    ap.add_argument("--end", default="2026-11-05")
    ap.add_argument("--kinds", default="donchian,pullback,fade")
    ap.add_argument("--spread", type=float, default=None)
    ap.add_argument("--slippage", type=float, default=0.06)
    ap.add_argument("--stress", action="store_true")
    ap.add_argument("--mode", choices=("sweep", "wfa", "both"), default="both")
    ap.add_argument("--is-months", type=int, default=3)
    ap.add_argument("--min-trades", type=int, default=20)
    args = ap.parse_args()

    start = dt.datetime.strptime(args.start, "%Y-%m-%d")
    end = dt.datetime.strptime(args.end, "%Y-%m-%d")
    bars, term_spread = load_m5(args.terminal, args.symbol, start, end)
    if len(bars) < 500:
        raise SystemExit(f"мало баров: {len(bars)}")
    spread = term_spread if args.spread is None else max(0.0, args.spread)
    spread_eff = (spread + args.slippage) * (2.0 if args.stress else 1.0)
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]

    out: dict = {"config": {"symbol": args.symbol, "bars": len(bars),
                            "first": dt.datetime.fromtimestamp(bars[0].t, dt.UTC).isoformat(),
                            "last": dt.datetime.fromtimestamp(bars[-1].t, dt.UTC).isoformat(),
                            "spread_used": round(spread_eff, 4),
                            "slippage": args.slippage, "stress": args.stress},
                 "kinds": {}}
    for kind in kinds:
        block: dict = {}
        if args.mode in ("sweep", "both"):
            block["sweep"] = sweep(bars, kind, spread_eff)
        if args.mode in ("wfa", "both"):
            block["wfa"] = wfa(bars, kind, spread_eff, args.is_months, args.min_trades)
        out["kinds"][kind] = block

    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n{args.symbol}: {len(bars)} баров M5, спред {spread_eff:.3f} "
          f"(+слиппедж, stress={args.stress})", file=sys.stderr)
    for kind, block in out["kinds"].items():
        print(f"\n[{kind}]", file=sys.stderr)
        if "sweep" in block:
            print(f"  {'cfg':<28}{'сделки':>7}{'net R':>8}{'avg R':>8}"
                  f"{'t':>6}{'win%':>7}{'PF':>6}{'DD R':>7}", file=sys.stderr)
            for r in block["sweep"][:5]:
                cfg = " ".join(f"{k}={v}" for k, v in r.items()
                               if k in ("n", "fast", "slow", "k", "tp_atr",
                                        "sl_atr", "max_bars"))
                pf = "—" if r["profit_factor"] is None else f"{r['profit_factor']:.2f}"
                print(f"  {cfg:<28}{r['trades']:>7}{r['net_r']:>8.1f}"
                      f"{r['avg_r']:>8.3f}{r['t_stat']:>6.2f}"
                      f"{r['win_rate'] * 100:>7.1f}{pf:>6}{r['max_dd_r']:>7.1f}",
                      file=sys.stderr)
        if "wfa" in block:
            w = block["wfa"]["wfa"]
            pf = "—" if w["profit_factor"] is None else f"{w['profit_factor']:.2f}"
            print(f"  WFA OOS: сделки {w['trades']}  net {w['net_r']:+.1f}R  "
                  f"avg {w['avg_r']:+.3f}R  t {w['t_stat']:.2f}  PF {pf}  "
                  f"DD {w['max_dd_r']:.1f}R", file=sys.stderr)
            for f in block["wfa"]["folds"]:
                if f.get("skipped"):
                    print(f"    {f['month']}  нет данных", file=sys.stderr)
                    continue
                print(f"    {f['month']}  IS {f['is_r']:+.1f}R  OOS {f['oos_r']:+.1f}R  "
                      f"сделок {f['oos_trades']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
