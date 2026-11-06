#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TSMOM 12M: time-series momentum на дневных барах, месячный ребаланс.

Правило (Moskowitz–Ooi–Pedersen, JFE 2012), без оптимизации:
  * в последний торговый день месяца считаем 12-месячную доходность
    close / close[-252] - 1;
  * знак позиции — её знак; размер ∝ target_vol / realized_vol(60 дней),
    потолок 2×;
  * вход по открытию первого торгового дня следующего месяца, держим месяц;
  * издержки — спред/цена × размер на каждый ребаланс.

Lookback фиксирован 12M и не перебирается: короткие lookback после 2009 не
работают, а перебор здесь — это уже data mining.

    python tsmom.py --symbols EURUSD,GBPUSD,US500 --json > out.json
    MT5_TERMINAL_PATH="C:/MT5-Exness/terminal64.exe" \
        python tsmom.py --symbols EURUSDm,GBPUSDm,US500m

Метрики: доходность, волатильность, Sharpe, maxDD, доля плюсовых месяцев,
t-статистика, разбивка по годам. Для месячных стратегий это честнее, чем
R-вердикт внутридневных WFA.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg
import strategy as st

DEFAULT_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
                   "USDCHF", "NZDUSD", "US500", "USTEC", "DE40", "JPN225")
LOOKBACK = 252
VOL_LEN = 60
TARGET_VOL = 0.10
MAX_LEV = 2.0


def _mt5():
    import MetaTrader5 as mt5

    if not cfg.mt5_init(timeout=30000):
        raise RuntimeError(f"MT5: {mt5.last_error()}")
    return mt5


def load_d1(mt5, symbol: str, want: int = 5000) -> st.Frame:
    """D1 с повторами: у части символов первый большой запрос отдаёт 0 баров."""
    mt5.symbol_select(symbol, True)
    rates = None
    for count in (want, 2000, 1000, 500):
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, count)
        if rates is not None and len(rates) >= 50:
            break
    if rates is None or len(rates) < 50:
        raise RuntimeError(f"{symbol}: нет D1 ({0 if rates is None else len(rates)} баров)")
    rates = rates[:-1]                # незакрытый день
    return st.Frame(
        t=rates["time"].astype("int64"),
        o=rates["open"].astype(float), h=rates["high"].astype(float),
        l=rates["low"].astype(float), c=rates["close"].astype(float),
        v=rates["tick_volume"].astype(float),
    )


def _month(ts: int) -> str:
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m")


def month_entries(frame: st.Frame) -> list[int]:
    """Индексы первых баров каждого месяца (по ним входим/считаем доходность)."""
    keys = [_month(t) for t in frame.t]
    out = [0]
    for i in range(1, len(keys)):
        if keys[i] != keys[i - 1]:
            out.append(i)
    return out


def monthly_series(frame: st.Frame, lookback: int = LOOKBACK,
                   vol_len: int = VOL_LEN, target_vol: float = TARGET_VOL,
                   max_lev: float = MAX_LEV, spread_pct: float = 0.0,
                   long_only: bool = False) -> list[dict]:
    """Месячные записи: знак, размер, gross/net доходность.

    Решение принимается на закрытии последнего бара месяца и исполняется по
    открытию первого бара следующего месяца — без заглядывания вперёд.
    """
    entries = month_entries(frame)
    c = frame.c
    ret1 = np.diff(np.log(np.maximum(c, 1e-12)), prepend=np.log(c[0]))
    rows: list[dict] = []
    for k in range(1, len(entries) - 1):
        sig_i = entries[k] - 1          # последний бар месяца k
        nxt_i = entries[k + 1]          # первый бар месяца k+1 (вход)
        ex_i = entries[k + 2] if k + 2 < len(entries) else len(c) - 1
        if sig_i - lookback < 0 or sig_i - vol_len < 0:
            continue
        mom = float(c[sig_i] / c[sig_i - lookback] - 1.0)
        if not np.isfinite(mom):
            continue
        vol = float(np.std(ret1[sig_i - vol_len + 1:sig_i + 1], ddof=1) * np.sqrt(252.0))
        lev = 0.0 if vol <= 1e-9 else min(max_lev, target_vol / vol)
        sign = 1.0 if mom > 0 else (-1.0 if mom < 0 and not long_only else 0.0)
        entry_px = float(frame.o[nxt_i])
        exit_px = float(frame.o[ex_i])
        gross = (exit_px / entry_px - 1.0) * sign * lev
        cost = abs(spread_pct * lev)
        rows.append({
            "month": _month(frame.t[ex_i]),
            "entry_ts": int(frame.t[nxt_i]),
            "exit_ts": int(frame.t[ex_i]),
            "entry": entry_px, "exit": exit_px,
            "mom_12m": round(mom, 6), "vol": round(vol, 6),
            "sign": int(sign), "lev": round(lev, 4),
            "gross": round(gross, 6), "cost": round(cost, 6),
            "net": round(gross - cost, 6),
        })
    return rows


def stats(rows: list[dict], bank: float = 1.0) -> dict:
    """Метрики месячного ряда (net)."""
    if not rows:
        return {"months": 0, "ann_ret": 0.0, "ann_vol": 0.0, "sharpe": 0.0,
                "max_dd": 0.0, "win_share": 0.0, "t_stat": 0.0,
                "years_positive": "0/0", "final": bank}
    r = np.array([x["net"] for x in rows], dtype=float)
    eq = bank * np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / np.maximum(peak, 1e-12)
    ann_ret = float(np.mean(r) * 12.0)
    ann_vol = float(np.std(r, ddof=1) * np.sqrt(12.0)) if len(r) > 1 else 0.0
    sharpe = ann_ret / ann_vol if ann_vol > 1e-12 else 0.0
    t_stat = float(np.mean(r) / (np.std(r, ddof=1) / np.sqrt(len(r)))) \
        if len(r) > 2 and np.std(r, ddof=1) > 1e-12 else 0.0
    years: dict[str, list[float]] = {}
    for x in rows:
        years.setdefault(x["month"][:4], []).append(x["net"])
    pos_years = sum(1 for v in years.values() if sum(v) > 0)
    return {
        "months": len(r),
        "ann_ret": round(ann_ret, 4),
        "ann_vol": round(ann_vol, 4),
        "sharpe": round(sharpe, 3),
        "max_dd": round(float(np.max(dd)), 4),
        "win_share": round(float(np.mean(r > 0)), 3),
        "t_stat": round(t_stat, 2),
        "years_positive": f"{pos_years}/{len(years)}",
        "final": round(float(eq[-1]), 4),
    }


def portfolio(series: dict[str, list[dict]]) -> list[dict]:
    """Равновесный портфель по месяцам: среднее net по доступным символам."""
    by_month: dict[str, list[float]] = {}
    for rows in series.values():
        for x in rows:
            by_month.setdefault(x["month"], []).append(x["net"])
    out = []
    for month in sorted(by_month):
        vals = by_month[month]
        out.append({"month": month, "net": float(np.mean(vals)), "n": len(vals)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="TSMOM 12M на D1")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--lookback", type=int, default=LOOKBACK)
    ap.add_argument("--vol-len", type=int, default=VOL_LEN)
    ap.add_argument("--target-vol", type=float, default=TARGET_VOL)
    ap.add_argument("--bars", type=int, default=5000)
    ap.add_argument("--source", choices=("mt5", "external"), default="mt5",
                    help="external — длинная история Yahoo из data/external")
    ap.add_argument("--json", action="store_true",
                    help="напечатать JSON в stdout (сохраните через > файл)")
    ap.add_argument("--years", default="", help="фильтр месяцев: 2018:2026")
    ap.add_argument("--long-only", action="store_true", help="только long при momentum>0")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    series: dict[str, list[dict]] = {}
    metas: dict[str, dict] = {}

    def _process(sym: str, frame, spread_pct: float) -> None:
        rows = monthly_series(frame, args.lookback, args.vol_len,
                              args.target_vol, MAX_LEV, spread_pct,
                              long_only=args.long_only)
        if args.years:
            lo, hi = args.years.split(":")
            rows = [x for x in rows if lo <= x["month"][:4] <= hi]
        series[sym] = rows
        metas[sym] = {"bars": len(frame.c), "start": _month(frame.t[0]),
                      "end": _month(frame.t[-1]), "spread_pct": round(spread_pct, 8),
                      **stats(rows)}
        print(f"{sym:<8} {len(rows):>4} мес  ann {metas[sym]['ann_ret']:+.2%}  "
              f"Sharpe {metas[sym]['sharpe']:+.2f}  maxDD {metas[sym]['max_dd']:.1%}  "
              f"мес+ {metas[sym]['win_share']:.0%}  годы+ {metas[sym]['years_positive']}",
              file=sys.stderr)

    if args.source == "external":
        import external_data

        for sym in symbols:
            try:
                frame = external_data.yahoo_frame(sym)
            except Exception as e:
                print(f"{sym}: {e}", file=sys.stderr)
                continue
            _process(sym, frame, external_data.DEFAULT_SPREAD_PCT.get(sym.upper(), 0.0002))
    else:
        mt5 = _mt5()
        try:
            for sym in symbols:
                try:
                    frame = load_d1(mt5, sym, args.bars)
                except RuntimeError as e:
                    print(f"{sym}: {e}", file=sys.stderr)
                    continue
                info = mt5.symbol_info(sym)
                point = float(getattr(info, "point", 0.0) or 0.0)
                spread = float(getattr(info, "spread", 0.0) or 0.0) * point
                if spread <= 0:
                    spread = max(0.0, float(info.ask) - float(info.bid)) if info else 0.0
                price = float(frame.c[-1])
                _process(sym, frame, spread / price if price > 0 else 0.0)
        finally:
            mt5.shutdown()

    port_rows = portfolio(series)
    port = stats(port_rows)
    print(f"\nПОРТФЕЛЬ ({len(series)} симв.): ann {port['ann_ret']:+.2%} · "
          f"vol {port['ann_vol']:.1%} · Sharpe {port['sharpe']:+.2f} · "
          f"maxDD {port['max_dd']:.1%} · мес+ {port['win_share']:.0%} · "
          f"t {port['t_stat']:+.2f} · годы+ {port['years_positive']}",
          file=sys.stderr)

    if args.json:
        out = {"params": {"lookback": args.lookback, "vol_len": args.vol_len,
                          "target_vol": args.target_vol, "max_lev": MAX_LEV,
                          "long_only": args.long_only},
               "symbols": metas, "portfolio": port,
               "series": series, "portfolio_series": port_rows}
        # файловый вывод не делаем: путь из CLI — это путь вне контроля
        # инструмента. Машинный результат — в stdout, сохранение — оболочкой.
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
