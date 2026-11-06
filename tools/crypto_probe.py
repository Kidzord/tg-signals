"""Проверка live-стратегий на крипте по публичным данным Binance.

Ничего не торгует и не пишет в очереди: только качает klines, прогоняет
classic/range тем же кодом, что live-боты, и печатает R после комиссий.

    python tools/crypto_probe.py
    python tools/crypto_probe.py --days 540 --symbols BTCUSDT ETHUSDT SOLUSDT
    python tools/crypto_probe.py --fee 0.001          # 0.1% за сторону (спот)
    python tools/crypto_probe.py --no-cache

Кеш по умолчанию — во временной папке (CRYPTO_CACHE или %TEMP%/tg_crypto).
Комиссия задаётся за сторону; round-trip = 2×fee. Для фьючерсов Binance
тейкер 0.05%, для спота 0.1%. Кроме комиссии, ничего не моделируется —
это первая проверка «есть ли вообще сигнал», а не финальный бэктест.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import tempfile
import time
import urllib.request

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bot  # noqa: E402
import range_v2  # noqa: E402
import strategy as st  # noqa: E402

BASE = "https://api.binance.com/api/v3/klines"
TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000,
         "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000}
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")


def cache_dir(no_cache: bool) -> str | None:
    if no_cache:
        return None
    path = os.getenv("CRYPTO_CACHE") or os.path.join(tempfile.gettempdir(), "tg_crypto")
    os.makedirs(path, exist_ok=True)
    return path


def _request(url: str) -> list:
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=25) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))
            print(f"  повтор запроса: {e}", file=sys.stderr)
    return []


def fetch(symbol: str, interval: str, start_ms: int, end_ms: int,
          cdir: str | None, cache_tag: str) -> np.ndarray:
    """Все закрытые klines [start_ms, end_ms) с постраничной загрузкой и кешем."""
    cache_path = None
    if cdir:
        cache_path = os.path.join(cdir, f"{cache_tag}.npz")
        if os.path.exists(cache_path):
            with np.load(cache_path) as z:
                return z["rows"]

    step = TF_MS[interval]
    rows: list[list] = []
    cur = start_ms
    while cur < end_ms:
        url = (f"{BASE}?symbol={symbol}&interval={interval}"
               f"&startTime={cur}&endTime={end_ms}&limit=1000")
        data = _request(url)
        if not data:
            break
        rows.extend(data)
        nxt = int(data[-1][0]) + step
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(0.03)

    now_ms = int(time.time() * 1000)
    rows = [r for r in rows if int(r[6]) < now_ms]      # только закрытые бары
    rows.sort(key=lambda r: int(r[0]))
    uniq = {}
    for r in rows:
        uniq[int(r[0])] = r
    rows = [uniq[k] for k in sorted(uniq)]

    arr = np.array([
        [int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]),
         float(r[4]), float(r[5])] for r in rows], dtype=float)
    if cache_path:
        np.savez_compressed(cache_path, rows=arr)
    return arr


def to_frame(arr: np.ndarray) -> st.Frame:
    return st.Frame(t=arr[:, 0].astype("int64"), o=arr[:, 1], h=arr[:, 2],
                    l=arr[:, 3], c=arr[:, 4], v=arr[:, 5])


def load_symbol(symbol: str, days: int, cdir: str | None, base_tf: str):
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000
    frames = {}
    for tf in {base_tf, "1h", "4h", "1d", "1w"}:
        tag = f"{symbol}_{tf}_{days}d"
        frames[tf] = to_frame(fetch(symbol, tf, start_ms, end_ms, cdir, tag))
    chart = frames[base_tf]
    htf = {"60": frames["1h"], "240": frames["4h"],
           "1D": frames["1d"], "1W": frames["1w"]}
    return chart, htf


def trade_cost_r(trades: list[dict], fee: float) -> np.ndarray:
    """Комиссия в R: 2×fee от ноционала / риск сделки."""
    out = []
    for t in trades:
        risk = float(t.get("risk") or 0.0)
        entry = float(t.get("entry") or 0.0)
        out.append(2.0 * fee * entry / risk if risk > 0 else 0.0)
    return np.array(out)


def summarize(trades: list[dict], fee_spot: float, fee_fut: float) -> dict:
    if not trades:
        return {"n": 0}
    gross = np.array([float(t["r"]) for t in trades])
    costs = trade_cost_r(trades, fee_spot)
    net_spot = gross - costs
    net_fut = gross - trade_cost_r(trades, fee_fut)
    eq = np.cumsum(net_spot)
    dd = float((np.maximum.accumulate(eq) - eq).max()) if len(eq) else 0.0
    pos = net_spot[net_spot > 0].sum()
    neg = abs(net_spot[net_spot < 0].sum())
    return {
        "n": len(trades),
        "gross": float(gross.sum()),
        "net_spot": float(net_spot.sum()),
        "net_fut": float(net_fut.sum()),
        "win": float((net_spot > 0).mean() * 100.0),
        "pf": float(pos / neg) if neg > 0 else float("inf"),
        "dd": dd,
        "avg": float(net_spot.mean()),
    }


def run_classic(chart: st.Frame, htf: dict, fee_spot: float, fee_fut: float) -> dict:
    p = dataclasses.replace(
        st.Params(), use_sess=False, risk_max_atr=2.0, min_rr=2.0,
        p1_pct=0.0, p2_pct=0.0)       # live classic: держим до TP3
    res = st.evaluate(chart, htf, p)
    if not res.get("ok"):
        return {"err": res.get("reason")}
    return summarize(bot.simulate(res, chart, p), fee_spot, fee_fut)


def run_range(chart: st.Frame, htf: dict, fee_spot: float, fee_fut: float,
              er_max: float, trend: str, zones, H) -> dict:
    trades = range_v2.backtest(
        chart, zones, H, stop_atr=1.0, tp_r=3.0, max_r=5.0,
        max_bars=120, max_stops=0, er_max=er_max, side_allow="both",
        trend_filter=trend, entry_model="next_open", session="")
    return summarize(trades, fee_spot, fee_fut)


def fmt(name: str, s: dict) -> str:
    if s.get("err"):
        return f"  {name:14} ошибка: {s['err']}"
    if not s.get("n"):
        return f"  {name:14} сделок нет"
    pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return (f"  {name:14} n={s['n']:4d} gross={s['gross']:+7.1f}R  "
            f"спот={s['net_spot']:+7.1f}R  фьюч={s['net_fut']:+7.1f}R  "
            f"win={s['win']:4.0f}%  PF={pf:>4}  DD={s['dd']:5.1f}R")


def main() -> int:
    ap = argparse.ArgumentParser(description="classic/range на крипте (Binance)")
    ap.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    ap.add_argument("--days", type=int, default=540)
    ap.add_argument("--tf", default="15m", choices=("15m", "1h", "4h"),
                    help="рабочий таймфрейм стратегии")
    ap.add_argument("--zone-tf", default="4h", choices=("4h", "1d"),
                    help="таймфрейм зон для range")
    ap.add_argument("--fee", type=float, default=0.001,
                    help="комиссия за сторону (0.001 = 0.1%% спот)")
    ap.add_argument("--fee-fut", type=float, default=0.0005,
                    help="комиссия за сторону для фьючерсов (0.0005 = 0.05%%)")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    cdir = cache_dir(args.no_cache)

    total = {}
    for sym in args.symbols:
        print(f"\n=== {sym} · {args.tf} · {args.days} дн. ===")
        chart, htf = load_symbol(sym, args.days, cdir, args.tf)
        span = (chart.t[-1] - chart.t[0]) / 86400.0
        print(f"  баров {args.tf} {len(chart.c)} · период {span:.0f} дн. · "
              f"{len(htf['1D'].c)} дн. D1 / {len(htf['240'].c)} H4")
        H = htf["240"] if args.zone_tf == "4h" else htf["1D"]
        zones = range_v2.zones_for_chart(
            chart, H, touches=2, flips_keep=bot.RANGE_FLIP_KEEP)

        variants = {
            "classic": run_classic(chart, htf, args.fee, args.fee_fut),
            "range er0.20": run_range(chart, htf, args.fee, args.fee_fut,
                                      0.20, "off", zones, H),
            "range er0": run_range(chart, htf, args.fee, args.fee_fut,
                                   0.0, "off", zones, H),
            "range er0+H4": run_range(chart, htf, args.fee, args.fee_fut,
                                      0.0, "with", zones, H),
        }
        for name, s in variants.items():
            print(fmt(name, s))
            if s.get("n"):
                agg = total.setdefault(name, {"n": 0, "net_spot": 0.0,
                                              "net_fut": 0.0, "gross": 0.0})
                agg["n"] += s["n"]
                agg["net_spot"] += s["net_spot"]
                agg["net_fut"] += s["net_fut"]
                agg["gross"] += s["gross"]

    print("\n=== ИТОГО по портфелю (сумма R, без учёта корреляций) ===")
    for name, s in total.items():
        print(f"  {name:14} n={s['n']:4d} gross={s['gross']:+7.1f}R  "
              f"спот={s['net_spot']:+7.1f}R  фьюч={s['net_fut']:+7.1f}R")
    print("\nОговорки: комиссия вычтена из R (2×fee/риск), проскальзывание и")
    print("фандинг не моделировались; classic стартует после 200 дней D1 EMA200.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
