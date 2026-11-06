#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Календарные эффекты на D1: turn-of-month, overnight drift, dual momentum.

Все три проверяются на фиксированных правилах без подбора параметров —
именно в этом их ценность по сравнению с очередной M5-вариацией.

  tom        buy US500 на закрытии последнего торгового дня месяца,
             выход на закрытии 3-го торгового дня следующего; фильтр close>SMA200.
             McConnell–Xu (FAJ 2008).
  overnight  buy на закрытии дня, продажа на открытии следующего;
             фильтр close>SMA200 и realized vol ниже 80-го перцентиля
             (прокси VIX, которого в MT5 нет). Lou–Polk–Skouras (JFE 2019).
  dual       месячный rank 12M-доходностей индексов; держим top-1 (или top-2),
             только если 12M > 0, иначе cash.

pre-FOMC не реализован: в MT5 нет календаря заседаний FOMC, а
`news_calendar.py` его не содержит. Carry отложен: нет 2Y yield differential,
свопы CFD не равны carry-фактору.

    python calendar_effects.py --effect tom --symbol US500 --json > out.json
    python calendar_effects.py --effect overnight --symbol US500
    python calendar_effects.py --effect dual --symbols US500,USTEC,DE40,JPN225
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ta_core as ta
from tsmom import load_d1, month_entries, stats, _mt5, _month


def spread_pct(mt5, symbol: str, price: float) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        return 0.0
    point = float(getattr(info, "point", 0.0) or 0.0)
    spread = float(getattr(info, "spread", 0.0) or 0.0) * point
    if spread <= 0:
        spread = max(0.0, float(info.ask) - float(info.bid))
    return spread / price if price > 0 else 0.0


def month_groups(frame) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for i, t in enumerate(frame.t):
        groups.setdefault(_month(t), []).append(i)
    return groups


def turn_of_month(frame, sma_len: int = 200, cost_pct: float = 0.0,
                  exit_day: int = 3) -> list[dict]:
    """Возврат в окне «последний день месяца → 3-й день следующего»."""
    sma = ta.sma(frame.c, sma_len)
    groups = month_groups(frame)
    keys = sorted(groups)
    rows: list[dict] = []
    for k in range(len(keys) - 1):
        cur, nxt = groups[keys[k]], groups[keys[k + 1]]
        entry_i = cur[-1]
        if np.isnan(sma[entry_i]) or frame.c[entry_i] <= sma[entry_i]:
            continue
        exit_i = nxt[min(exit_day, len(nxt)) - 1]
        gross = float(frame.c[exit_i] / frame.c[entry_i] - 1.0)
        cost = 2.0 * cost_pct
        rows.append({
            "month": keys[k + 1], "entry_ts": int(frame.t[entry_i]),
            "exit_ts": int(frame.t[exit_i]),
            "entry": float(frame.c[entry_i]), "exit": float(frame.c[exit_i]),
            "gross": round(gross, 6), "cost": round(cost, 6),
            "net": round(gross - cost, 6),
        })
    return rows


def overnight(frame, sma_len: int = 200, vol_len: int = 20,
              pct_len: int = 252, pct_max: float = 0.80,
              cost_pct: float = 0.0) -> list[dict]:
    """Закрытие → открытие следующего дня, фильтр тренда и низкой волатильности."""
    sma = ta.sma(frame.c, sma_len)
    ret1 = np.diff(np.log(np.maximum(frame.c, 1e-12)), prepend=np.log(frame.c[0]))
    vol = np.full(len(frame.c), np.nan)
    for i in range(vol_len, len(frame.c)):
        vol[i] = float(np.std(ret1[i - vol_len + 1:i + 1], ddof=1) * np.sqrt(252.0))
    rows: list[dict] = []
    for t in range(max(sma_len, vol_len, pct_len), len(frame.c) - 1):
        if np.isnan(sma[t]) or np.isnan(vol[t]) or frame.c[t] <= sma[t]:
            continue
        window = vol[t - pct_len:t]
        window = window[~np.isnan(window)]
        if len(window) < 50:
            continue
        if (window <= vol[t]).mean() > pct_max:
            continue
        gross = float(frame.o[t + 1] / frame.c[t] - 1.0)
        rows.append({
            "month": _month(frame.t[t + 1]), "entry_ts": int(frame.t[t]),
            "exit_ts": int(frame.t[t + 1]), "gross": round(gross, 6),
            "cost": round(cost_pct, 6), "net": round(gross - cost_pct, 6),
        })
    return rows


def _month_state(frame) -> dict[str, tuple[int, int, int]]:
    """month -> (sig_i, entry_i следующего месяца, exit_i через месяц)."""
    entries = month_entries(frame)
    out = {}
    for k in range(1, len(entries) - 1):
        sig = entries[k] - 1
        out[_month(frame.t[entries[k]])] = (
            sig, entries[k], entries[k + 1] if k + 1 < len(entries) else len(frame.c) - 1)
    return out


def dual_momentum(frames: dict[str, object], top_n: int = 1,
                  lookback: int = 252, cost_pct: dict[str, float] | None = None,
                  with_cash_filter: bool = True) -> list[dict]:
    """Rank 12M среди индексов; держим top_n, если доходность > 0."""
    cost_pct = cost_pct or {}
    states = {s: _month_state(f) for s, f in frames.items()}
    months = sorted(set.intersection(*[set(v) for v in states.values()]))
    rows: list[dict] = []
    prev: set[str] = set()
    for month in months:
        scored = []
        for sym, st_map in states.items():
            f = frames[sym]
            sig_i, entry_i, exit_i = st_map[month]
            if sig_i - lookback < 0:
                continue
            mom = float(f.c[sig_i] / f.c[sig_i - lookback] - 1.0)
            if with_cash_filter and mom <= 0:
                continue
            scored.append((mom, sym, entry_i, exit_i))
        scored.sort(reverse=True)
        chosen = scored[:top_n]
        picked = {x[1] for x in chosen}
        if not chosen:
            rows.append({"month": month, "net": 0.0, "n": 0, "chosen": []})
            prev = set()
            continue
        rets = []
        costs = 0.0
        for mom, sym, entry_i, exit_i in chosen:
            f = frames[sym]
            rets.append(float(f.o[exit_i] / f.o[entry_i] - 1.0))
            if sym not in prev:
                costs += cost_pct.get(sym, 0.0)
        net = float(np.mean(rets)) - costs / max(1, len(chosen))
        rows.append({"month": month, "net": round(net, 6), "n": len(chosen),
                     "chosen": [x[1] for x in chosen]})
        prev = picked
    return rows


def fomc_drift(frame, dates, vix: dict[str, float] | None = None,
               high_only: bool = False, mode: str = "day",
               cost_pct: float = 0.0) -> list[dict]:
    """Pre-FOMC drift на дневных барах.

    Вход — закрытие торгового дня перед заседанием, выход — открытие или
    закрытие дня заседания. Дневные бары не позволяют выйти «за 15 минут до
    14:00 ET», поэтому проверяются два приближения:
      overnight — закрытие(t−1) → открытие(t): только ночная часть;
      day       — закрытие(t−1) → закрытие(t): включает объявление.
    high_only — только заседания при VIX выше медианы последнего года.
    """
    idx = {dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).date(): i
           for i, ts in enumerate(frame.t)}
    vix_days = [dt.date.fromisoformat(k) for k in sorted(vix)] if vix else []
    vix_vals = [vix[k.isoformat()] for k in vix_days] if vix else []
    rows: list[dict] = []
    for d in dates:
        i = idx.get(d)
        if i is None or i < 1:
            continue
        if high_only:
            if not vix_vals:
                continue
            prior = [v for dd, v in zip(vix_days, vix_vals) if dd < d][-252:]
            if len(prior) < 60:
                continue
            cur = vix.get(d.isoformat())
            if cur is None:
                cur = prior[-1]
            if cur <= float(np.median(prior)):
                continue
        gross = (float(frame.o[i] / frame.c[i - 1] - 1.0) if mode == "overnight"
                 else float(frame.c[i] / frame.c[i - 1] - 1.0))
        rows.append({"month": d.strftime("%Y-%m"), "entry_ts": int(frame.t[i - 1]),
                     "exit_ts": int(frame.t[i]), "gross": round(gross, 6),
                     "cost": round(cost_pct, 6), "net": round(gross - cost_pct, 6)})
    return rows


def fomc_intraday(frame, dates, exit_hm: tuple[int, int] = (13, 0),
                  vix: dict[str, float] | None = None, high_only: bool = False,
                  cost_pct: float = 0.0) -> list[dict]:
    """Точное окно pre-FOMC на часовых барах.

    Вход — закрытие последнего бара перед днём заседания (16:00 ET),
    выход — цена на exit_hm по времени Нью-Йорка (открытие бара).
    Часовые бары дают границы 13:00 и 14:00 ET; правило плана (13:45)
    между ними, поэтому проверяются обе границы.
    """
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    dts = [dt.datetime.fromtimestamp(int(t), dt.timezone.utc).astimezone(et)
           for t in frame.t]
    vix_days = [dt.date.fromisoformat(k) for k in sorted(vix)] if vix else []
    vix_vals = [vix[k.isoformat()] for k in vix_days] if vix else []
    rows: list[dict] = []
    for d in dates:
        entry_i = None
        for i, ts in enumerate(dts):
            if ts.date() < d:
                entry_i = i
            else:
                break
        exit_i = None
        for i, ts in enumerate(dts):
            if ts.date() == d and (ts.hour, ts.minute) >= exit_hm:
                exit_i = i
                break
        if entry_i is None or exit_i is None or exit_i <= entry_i:
            continue
        if high_only:
            prior = [v for dd, v in zip(vix_days, vix_vals) if dd < d][-252:]
            if len(prior) < 60:
                continue
            cur = vix.get(d.isoformat(), prior[-1])
            if cur <= float(np.median(prior)):
                continue
        gross = float(frame.o[exit_i] / frame.c[entry_i] - 1.0)
        rows.append({"month": d.strftime("%Y-%m"), "entry_ts": int(frame.t[entry_i]),
                     "exit_ts": int(frame.t[exit_i]), "gross": round(gross, 6),
                     "cost": round(cost_pct, 6), "net": round(gross - cost_pct, 6)})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Календарные эффекты на D1")
    ap.add_argument("--effect", required=True,
                    choices=("tom", "overnight", "dual", "fomc", "fomc-intraday"))
    ap.add_argument("--source", choices=("mt5", "external"), default="mt5")
    ap.add_argument("--symbol", default="US500")
    ap.add_argument("--symbols", default="US500,USTEC,DE40,JPN225")
    ap.add_argument("--top", type=int, default=1)
    ap.add_argument("--bars", type=int, default=5000)
    ap.add_argument("--fomc-mode", choices=("overnight", "day"), default="day")
    ap.add_argument("--vix-mode", choices=("all", "high"), default="all")
    ap.add_argument("--exit-time", default="13:00", help="ЧЧ:ММ по Нью-Йорку")
    ap.add_argument("--json", action="store_true",
                    help="напечатать JSON в stdout (сохраните через > файл)")
    args = ap.parse_args()

    import external_data

    mt5 = None
    if args.source == "mt5":
        mt5 = _mt5()

    def _frame(sym: str):
        if args.source == "external":
            return external_data.yahoo_frame(sym)
        return load_d1(mt5, sym, args.bars)

    def _cost(sym: str, frame) -> float:
        if args.source == "external":
            return external_data.DEFAULT_SPREAD_PCT.get(sym.upper(), 0.0002)
        return spread_pct(mt5, sym, float(frame.c[-1]))

    try:
        if args.effect == "dual":
            syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
            frames, costs = {}, {}
            for sym in syms:
                try:
                    frames[sym] = _frame(sym)
                except Exception as e:
                    print(f"{sym}: {e}", file=sys.stderr)
                    continue
                costs[sym] = _cost(sym, frames[sym])
            rows = dual_momentum(frames, args.top, cost_pct=costs)
            label = f"dual top{args.top} ({', '.join(frames)})"
            meta = {"bars": {s: len(f.c) for s, f in frames.items()},
                    "start": "—", "end": "—"}
        elif args.effect == "fomc-intraday":
            frame = external_data.yahoo_hourly_frame("SPX")
            cost = external_data.DEFAULT_SPREAD_PCT["SPX"]
            hh, mm = (int(x) for x in args.exit_time.split(":"))
            vix_days, vix_vals = external_data.fred_series("VIXCLS")
            vix_map = {d.isoformat(): v for d, v in zip(vix_days, vix_vals)}
            rows = fomc_intraday(frame, external_data.fomc_dates(), (hh, mm),
                                 vix=vix_map, high_only=args.vix_mode == "high",
                                 cost_pct=cost)
            label = (f"fomc-intraday exit {args.exit_time} ET SPX "
                     f"(VIX {args.vix_mode})")
            meta = {"bars": len(frame.c), "start": _month(frame.t[0]),
                    "end": _month(frame.t[-1])}
        else:
            frame = _frame(args.symbol)
            cost = _cost(args.symbol, frame)
            if args.effect == "fomc":
                vix_days, vix_vals = external_data.fred_series("VIXCLS")
                vix_map = {d.isoformat(): v for d, v in zip(vix_days, vix_vals)}
                rows = fomc_drift(frame, external_data.fomc_dates(), vix=vix_map,
                                  high_only=args.vix_mode == "high",
                                  mode=args.fomc_mode, cost_pct=cost)
                label = (f"fomc {args.fomc_mode} {args.symbol} "
                         f"(VIX {args.vix_mode})")
            elif args.effect == "tom":
                rows = turn_of_month(frame, cost_pct=cost)
                label = f"tom {args.symbol}"
            else:
                rows = overnight(frame, cost_pct=cost)
                label = f"overnight {args.symbol}"
            meta = {"bars": len(frame.c), "start": _month(frame.t[0]),
                    "end": _month(frame.t[-1])}
    finally:
        if mt5 is not None:
            mt5.shutdown()

    st = stats(rows)
    print(f"{label}: событий {st['months']} · ann {st['ann_ret']:+.2%} · "
          f"vol {st['ann_vol']:.1%} · Sharpe {st['sharpe']:+.2f} · "
          f"maxDD {st['max_dd']:.1%} · плюсовых {st['win_share']:.0%} · "
          f"t {st['t_stat']:+.2f} · годы+ {st['years_positive']}", file=sys.stderr)
    if rows:
        years: dict[str, float] = {}
        for x in rows:
            years[x["month"][:4]] = years.get(x["month"][:4], 0.0) + x["net"]
        print("  по годам: " + "  ".join(f"{y}:{v:+.1%}" for y, v in sorted(years.items())),
              file=sys.stderr)
    if args.json:
        # без файлового вывода: путь задаёт вызывающая оболочка, инструмент
        # пишет только в stdout
        print(json.dumps({"effect": args.effect, "meta": meta, "stats": st,
                          "rows": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
