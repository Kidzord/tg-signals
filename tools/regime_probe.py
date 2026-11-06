#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Этап 4: межрыночные фильтры на реальных сделках range.

Проверяются два фильтра из плана, для которых есть данные в MT5:

  DXY  — запрет long AUDUSD, если DXY выше SMA200 и 20-дневная доходность
         DXY положительна (Lustig–Roussanov–Verdelhan). DXY есть только на
         Exness (`DXYm`), поэтому снимок D1 собирается отдельным процессом.
  OIL  — USDCAD short (= CAD long) только если 60-дневный импульс WTI
         положителен и corr60(USDCAD, WTI) < −0.2 (Chen–Rogoff–Rossi).

VIX-режим и золото–реальные ставки не проверяются: VIX и real yields в MT5
нет. Это зафиксировано в отчёте этапа 4, а не подменяется прокси без
предупреждения.

Инструмент не пишет файлов: снимки печатаются в stdout, отчёт читается из
stdin. Один MT5-терминал на процесс, поэтому сборка идёт тремя шагами:

    python tools/regime_probe.py --collect-native > renders/stage4/regime_native.json
    MT5_TERMINAL_PATH="C:/MT5-Exness/terminal64.exe" \
      python tools/regime_probe.py --collect-dxy > renders/stage4/dxy.json
    python -c "import json;n=json.load(open('renders/stage4/regime_native.json'));\
d=json.load(open('renders/stage4/dxy.json'));print(json.dumps({'native':n,'dxy':d}))" \
      | python tools/regime_probe.py --report
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot
import costs
import range_v2
import strategy as st
from tsmom import load_d1

LIVE = {
    "AUDUSD": dict(er_max=0.10, tp_r=3.0, trend="with", spread=0.00004),
    "USDCAD": dict(er_max=0.20, tp_r=3.0, trend="off", spread=0.00005),
}


def _date(ts: float) -> str:
    return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).strftime("%Y-%m-%d")


def collect_native(bars: int = 30000) -> dict:
    data = bot.connect()
    if data is None:
        raise SystemExit("нет MT5")
    out = {"trades": {}, "daily": {}}
    try:
        for sym, cfg in LIVE.items():
            chart, htf = bot.load_frames(data, sym, bot.TF, bars)
            H = htf[bot.RANGE_ZONE_TF]
            zones = range_v2.zones_for_chart(chart, H, touches=2,
                                             flips_keep=bot.RANGE_FLIP_KEEP)
            trades = range_v2.backtest(
                chart, zones, H, stop_atr=1.0, tp_r=cfg["tp_r"],
                max_r=cfg["tp_r"] + 2.0, session="", smt=None, smt_look=0,
                max_bars=bot.PARAMS.max_bars, er_max=cfg["er_max"],
                side_allow="both", trend_filter=cfg["trend"],
                max_stops=bot.RANGE_MAX_STOPS, spread=cfg["spread"],
                max_spread_r=bot.SPREAD_MAX_R)
            out["trades"][sym] = [{
                "side": t["side"], "entry_ts": int(chart.t[t["entry_i"]]),
                "exit_ts": int(chart.t[t["exit_i"]]), "r": float(t["r"]),
                "risk": float(t.get("risk") or 0.0),
            } for t in trades]
            print(f"{sym}: сделок {len(trades)}", file=sys.stderr)
        # classic XAUUSD — для фильтра «золото–реальные ставки»
        chart, htf = bot.load_frames(data, "XAUUSD", bot.TF, bars)
        res = st.evaluate(chart, htf, bot.PARAMS)
        if res.get("ok"):
            spread = 0.21
            xau = []
            for t in bot.simulate(res, chart, bot.PARAMS):
                risk = float(t.get("risk") or 0.0)
                xau.append({
                    "side": "LONG" if t["dir"] > 0 else "SHORT",
                    "entry_ts": int(chart.t[t["i"]]),
                    "exit_ts": int(chart.t[t["ei"]]),
                    "r": float(t["r"]) - costs.cost_r(spread, risk),
                    "risk": risk,
                })
            out["trades"]["XAUUSD"] = xau
            print(f"XAUUSD classic: сделок {len(xau)}", file=sys.stderr)
        for sym in ("WTI", "USDCAD", "EURUSD"):
            try:
                f = load_d1(data.mt5, sym, 5000)
            except RuntimeError as e:
                print(f"{sym} D1: {e}", file=sys.stderr)
                continue
            out["daily"][sym] = {"t": [int(x) for x in f.t],
                                 "c": [float(x) for x in f.c]}
            print(f"{sym} D1: {len(f.c)} баров", file=sys.stderr)
    finally:
        data.close()
    return out


def collect_dxy() -> dict:
    from tsmom import _mt5

    mt5 = _mt5()
    try:
        f = load_d1(mt5, "DXYm", 5000)
    finally:
        mt5.shutdown()
    print(f"DXYm D1: {len(f.c)} баров", file=sys.stderr)
    return {"t": [int(x) for x in f.t], "c": [float(x) for x in f.c]}


def _index_by_date(t: list[int]) -> dict[str, int]:
    return {_date(ts): i for i, ts in enumerate(t)}


def _regime_dxy(dxy: dict, date: str) -> bool | None:
    """True — доллар сильный (DXY>SMA200 и 20d return>0)."""
    idx = _index_by_date(dxy["t"])
    key = _prev_day(date)
    while key and key not in idx:
        key = _prev_day(key)
    if not key:
        return None
    i = idx[key]
    c = np.asarray(dxy["c"], dtype=float)
    if i < 220:
        return None
    sma = float(np.mean(c[i - 199:i + 1]))
    mom20 = float(c[i] / c[i - 20] - 1.0)
    return bool(c[i] > sma and mom20 > 0)


def _oil_flags(daily: dict, date: str) -> tuple[bool, float] | None:
    if "WTI" not in daily or "USDCAD" not in daily:
        return None
    wti = np.asarray(daily["WTI"]["c"], dtype=float)
    cad = np.asarray(daily["USDCAD"]["c"], dtype=float)
    idx = _index_by_date(daily["WTI"]["t"])
    key = _prev_day(date)
    while key and key not in idx:
        key = _prev_day(key)
    if not key:
        return None
    i = idx[key]
    j = _index_by_date(daily["USDCAD"]["t"]).get(key)
    if j is None or i < 65 or j < 65:
        return None
    mom60 = float(wti[i] / wti[i - 60] - 1.0)
    rw = np.diff(np.log(wti[i - 60:i + 1]))
    rc = np.diff(np.log(cad[j - 60:j + 1]))
    n = min(len(rw), len(rc))
    corr = float(np.corrcoef(rw[-n:], rc[-n:])[0, 1]) if n > 5 else 0.0
    return bool(mom60 > 0 and corr < -0.2), round(corr, 3)


def _series_index(series: dict[str, float]):
    keys = sorted(series)
    pos = {k: i for i, k in enumerate(keys)}
    return keys, pos


def _prev_day(date: str) -> str:
    return (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()


def _value_idx(keys, pos, date: str) -> int | None:
    for _ in range(4000):
        if date in pos:
            return pos[date]
        d = dt.date.fromisoformat(date)
        if d.year <= 1900:
            return None
        date = (d - dt.timedelta(days=1)).isoformat()
    return None


def _vix_high(vix: dict[str, float], date: str) -> bool | None:
    """VIX выше 80-го перцентиля года или спайк >20% за 5 дней."""
    if not vix:
        return None
    keys, pos = _series_index(vix)
    i = _value_idx(keys, pos, _prev_day(date))
    if i is None or i < 60:
        return None   # только закрытие предыдущего дня
    vals = [vix[k] for k in keys[max(0, i - 250):i + 1]]
    cur = vix[keys[i]]
    spike = i >= 5 and cur / vix[keys[i - 5]] - 1.0 > 0.20
    return bool(cur >= float(np.percentile(vals, 80)) or spike)


def _real_rising(real10: dict[str, float], date: str) -> bool | None:
    """Real yield вырос за последние 20 наблюдений (в п.п.)."""
    if not real10:
        return None
    keys, pos = _series_index(real10)
    i = _value_idx(keys, pos, _prev_day(date))
    if i is None or i < 20:
        return None
    return bool(real10[keys[i]] - real10[keys[i - 20]] > 0)


def _broad_dollar_strong(broad: dict[str, float], date: str) -> bool | None:
    if not broad:
        return None
    keys, pos = _series_index(broad)
    i = _value_idx(keys, pos, _prev_day(date))
    if i is None or i < 220:
        return None
    c = np.asarray([broad[k] for k in keys], dtype=float)
    sma = float(np.mean(c[i - 199:i + 1]))
    mom20 = float(c[i] / c[i - 20] - 1.0)
    return bool(c[i] > sma and mom20 > 0)


def report(payload: dict) -> dict:
    native, dxy = payload["native"], payload["dxy"]
    vix = payload.get("vix") or {}
    real10 = payload.get("real10") or {}
    broad = payload.get("broad") or {}
    res = {}
    for sym, trades in native["trades"].items():
        base_net = sum(x["r"] for x in trades)
        checks = {}
        if sym == "AUDUSD":
            checks["dxy_strong_ban_long"] = (
                lambda t, d: t["side"] == "LONG" and _regime_dxy(dxy, d) is True)
            checks["broad_strong_ban_long"] = (
                lambda t, d: t["side"] == "LONG" and _broad_dollar_strong(broad, d) is True)
        if sym == "USDCAD":
            checks["oil_cad"] = (
                lambda t, d: t["side"] == "SHORT"
                and (_oil_flags(native["daily"], d) or (True, 0))[0] is False)
        if sym in ("AUDUSD", "USDCAD", "EURUSD"):
            checks["vix_high_ban"] = lambda t, d: _vix_high(vix, d) is True
        if sym == "XAUUSD":
            checks["real_yield_rising_ban_long"] = (
                lambda t, d: t["side"] == "LONG" and _real_rising(real10, d) is True)
        variants = {}
        for name, blocks in checks.items():
            kept, blocked = [], []
            for t in trades:
                d = _date(t["entry_ts"])
                if blocks(t, d):
                    blocked.append(t)
                else:
                    kept.append(t)
            variants[name] = {
                "kept": len(kept), "kept_net": round(sum(x["r"] for x in kept), 2),
                "blocked": len(blocked),
                "blocked_net": round(sum(x["r"] for x in blocked), 2),
            }
        res[sym] = {"trades": len(trades), "base_net": round(base_net, 2),
                    "variants": variants}
        print(f"{sym}: база {len(trades)} ({base_net:+.2f}R)")
        for name, v in variants.items():
            print(f"  {name:<28} остаётся {v['kept']:>4} ({v['kept_net']:+.2f}R) · "
                  f"блок {v['blocked']:>3} ({v['blocked_net']:+.2f}R)")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect-native", action="store_true")
    ap.add_argument("--collect-dxy", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--bars", type=int, default=30000)
    args = ap.parse_args()
    if args.collect_native:
        print(json.dumps(collect_native(args.bars), ensure_ascii=False))
    elif args.collect_dxy:
        print(json.dumps(collect_dxy(), ensure_ascii=False))
    elif args.report:
        print(json.dumps(report(json.load(sys.stdin)), ensure_ascii=False, indent=2))
    else:
        ap.error("нужен один из режимов: --collect-native / --collect-dxy / --report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
