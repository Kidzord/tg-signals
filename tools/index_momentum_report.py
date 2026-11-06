#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Индексная стратегия: месячный dual momentum / TSMOM.

Universe: SPX, NDX, DAX, N225 (Yahoo, длинная история).
Правила:
  * на закрытии месяца считаем 12M-доходность каждого индекса;
  * dual momentum: держим top-1, если его 12M > 0, иначе cash;
  * TSMOM long-only: держим каждый индекс с 12M > 0, равными долями.
Ребаланс — по открытию следующего месяца, без заглядывания вперёд.
Издержки — спред/финансирование через `external_data.DEFAULT_SPREAD_PCT`.

Запуск: python tools/index_momentum_report.py
JSON в stdout, таблица в stderr.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calendar_effects as ce  # noqa: E402
import external_data as ed  # noqa: E402
import tsmom  # noqa: E402


def stats(rows: list[dict], name: str, *, risk_free: float = 0.0) -> dict:
    vals = [float(r["net"]) for r in rows]
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for v in vals:
        equity *= 1.0 + v
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
    n = len(vals)
    mean = sum(vals) / n if n else 0.0
    var = sum((v - mean) ** 2 for v in vals) / (n - 1) if n > 1 else 0.0
    sharpe = ((mean - risk_free / 12.0) / math.sqrt(var) * math.sqrt(12)
              if var > 0 else 0.0)
    tstat = mean / math.sqrt(var / n) if var > 0 and n else 0.0
    years = n / 12.0
    cagr = equity ** (1.0 / years) - 1.0 if years > 0 and equity > 0 else 0.0
    return {
        "name": name,
        "months": n,
        "first": rows[0]["month"] if rows else None,
        "last": rows[-1]["month"] if rows else None,
        "total_return_pct": round((equity - 1.0) * 100.0, 1),
        "cagr_pct": round(cagr * 100.0, 1),
        "sharpe": round(sharpe, 2),
        "t_stat": round(tstat, 2),
        "max_dd_pct": round(max_dd * 100.0, 1),
        "positive_months": sum(1 for v in vals if v > 0),
        "negative_months": sum(1 for v in vals if v < 0),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="SPX,NDX,DAX,N225")
    ap.add_argument("--top-n", type=int, default=1)
    args = ap.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    frames = {s: ed.yahoo_frame(s) for s in symbols}
    spreads = {s: ed.DEFAULT_SPREAD_PCT.get(s, 0.0) for s in symbols}

    dual = ce.dual_momentum(frames, args.top_n, cost_pct=spreads)
    series = {s: tsmom.monthly_series(f, long_only=True, spread_pct=spreads[s])
              for s, f in frames.items()}
    ts = tsmom.portfolio(series)

    out = {
        "dual_momentum": stats(dual, f"dual_momentum top-{args.top_n}"),
        "tsmom_long": stats(ts, "tsmom_long"),
        "tsmom_by_symbol": {s: stats(series[s], f"tsmom_{s}") for s in symbols},
        "last_signal": dual[-1] if dual else None,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nИндексы: {', '.join(symbols)} · месячный ребаланс · издержки учтены",
          file=sys.stderr)
    print(f"{'стратегия':<22}{'мес':>5}{'CAGR':>8}{'Sharpe':>8}{'t':>6}"
          f"{'maxDD':>8}{'плюс/мин':>10}", file=sys.stderr)
    rows = [out["dual_momentum"], out["tsmom_long"]]
    rows += list(out["tsmom_by_symbol"].values())
    for r in rows:
        print(f"{r['name']:<22}{r['months']:>5}{r['cagr_pct']:>7.1f}%"
              f"{r['sharpe']:>8.2f}{r['t_stat']:>6.2f}{r['max_dd_pct']:>7.1f}%"
              f"{r['positive_months']:>5}/{r['negative_months']:<4}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
