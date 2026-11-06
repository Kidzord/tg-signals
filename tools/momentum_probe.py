"""Проверка импульсных стратегий на крипте (Binance, публичные klines).

Два варианта, оба только long, вход на следующем баре после закрытия сигнала:

1) impulse — H1-пробой: close > open + 1×ATR14, high >= 20-барного максимума,
   объём > mean20 + 2σ, тело/диапазон > 0.6. Стоп за low импульса − 0.25×ATR,
   цель 2R, тайм-стоп 12 баров; опционально трейлинг 1.5×ATR.
2) xs — кросс-секционный top-gainer: топ-2 по 4-баровной доходности среди
   ликвидных пар (24ч quote volume > $20M, объёмный z-score > 2), стоп 1.5×ATR,
   цель 2R, тайм-стоп 24 бара.

Считаются gross, net после спота/фьючерсов, winrate, PF, DD и половины
периода (первая/вторая) как грубая проверка устойчивости. Проскальзывание
зашито в цену входа/выхода. Это read-only research: ничего не торгует.

    python tools/momentum_probe.py
    python tools/momentum_probe.py --days 540 --fee 0.001 --slip 0.0005
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ta_core as ta  # noqa: E402
from tools.crypto_probe import TF_MS, _request, cache_dir, summarize  # noqa: E402

UNIVERSE = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
            "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT")


def fetch_full(symbol: str, interval: str, start_ms: int, end_ms: int,
               cdir: str | None, tag: str) -> np.ndarray:
    """[time, open, high, low, close, volume, quote_volume, trades]."""
    path = os.path.join(cdir, f"{tag}.npz") if cdir else None
    if path and os.path.exists(path):
        with np.load(path) as z:
            return z["rows"]

    step = TF_MS[interval]
    rows: list[list] = []
    cur = start_ms
    while cur < end_ms:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
               f"&interval={interval}&startTime={cur}&endTime={end_ms}&limit=1000")
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
    rows = [r for r in rows if int(r[6]) < now_ms]
    uniq = {int(r[0]): r for r in rows}
    rows = [uniq[k] for k in sorted(uniq)]
    arr = np.array([[int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]),
                     float(r[4]), float(r[5]), float(r[7]), float(r[8])]
                    for r in rows], dtype=float)
    if path:
        np.savez_compressed(path, rows=arr)
    return arr


def _send(sym: str, side: str, entry_i: int, exit_i: int,
          entry: float, stop: float, target: float, exit_px: float,
          slip: float, why: str) -> dict:
    risk = entry - stop
    exit_exec = exit_px * (1.0 - slip)
    return {"symbol": sym, "side": side, "entry_i": entry_i, "exit_i": exit_i,
            "entry": entry, "risk": risk,
            "r": (exit_exec - entry) / risk, "exit": why}


def impulse_trades(sym: str, arr: np.ndarray, slip: float, trail_atr: float | None,
                   look: int = 20, atr_mult: float = 1.0, body_min: float = 0.6,
                   vol_z: float = 2.0, max_hold: int = 12,
                   target_r: float = 2.0) -> list[dict]:
    t, o, h, l, c = (arr[:, k] for k in range(5))
    v = arr[:, 5]
    atr = ta.atr(h, l, c, 14)
    n = len(c)
    out: list[dict] = []
    i = look + 1
    while i < n - 1:
        rng = h[i] - l[i]
        body = abs(c[i] - o[i])
        vmean = float(np.mean(v[i - look:i]))
        vstd = float(np.std(v[i - look:i]))
        ok = (not np.isnan(atr[i]) and atr[i] > 0 and rng > 0
              and body / rng > body_min
              and c[i] > o[i] + atr_mult * atr[i]
              and h[i] >= float(np.max(h[i - look:i]))
              and v[i] > vmean + vol_z * vstd)
        if not ok:
            i += 1
            continue

        entry = float(o[i + 1]) * (1.0 + slip)
        stop = float(l[i]) - 0.25 * float(atr[i])
        if entry - stop <= 0:
            i += 1
            continue
        initial_stop = stop
        target = entry + target_r * (entry - stop)
        exit_i = exit_px = why = None
        for j in range(i + 1, min(i + 1 + max_hold, n)):
            if l[j] <= stop:
                exit_i, exit_px, why = j, stop, "SL"
                break
            if h[j] >= target:
                exit_i, exit_px, why = j, target, "TP"
                break
            if trail_atr:
                stop = max(stop, float(c[j]) - trail_atr * float(atr[j]))
        if exit_i is None:
            exit_i = min(i + 1 + max_hold, n - 1)
            exit_px, why = float(c[exit_i]), "TIME"
        out.append(_send(sym, "LONG", i + 1, exit_i, entry, initial_stop, target,
                         float(exit_px), slip, why))
        i = exit_i + 1
    return out


def xs_trades(arrays: dict[str, np.ndarray], slip: float,
              top_n: int = 2, min_qv24: float = 20e6,
              vol_z: float = 2.0, stop_atr: float = 1.5,
              max_hold: int = 24, target_r: float = 2.0) -> list[dict]:
    """Кросс-секционный top-gainer по 4-баровной доходности."""
    base = arrays[next(iter(arrays))][:, 0]
    tmap = {s: {int(tt): k for k, tt in enumerate(a[:, 0])}
            for s, a in arrays.items()}
    atr = {s: ta.atr(a[:, 2], a[:, 3], a[:, 4], 14) for s, a in arrays.items()}
    busy_until = {s: 0 for s in arrays}
    out: list[dict] = []

    for i in range(55, len(base) - 1):
        ts = int(base[i])
        cand = []
        for s, a in arrays.items():
            j = tmap[s].get(ts)
            if j is None or j < 55 or j >= len(a) - 1 or i < busy_until[s]:
                continue
            qv24 = float(a[max(0, j - 23):j + 1, 6].sum())
            if qv24 < min_qv24:
                continue
            v = a[j - 50:j, 5]
            if len(v) < 50 or float(v.std()) <= 0:
                continue
            if a[j, 5] <= float(v.mean()) + vol_z * float(v.std()):
                continue
            ret4 = float(a[j, 4] / a[j - 4, 4] - 1.0)
            if ret4 <= 0 or np.isnan(atr[s][j]) or atr[s][j] <= 0:
                continue
            cand.append((ret4, s, j))
        cand.sort(reverse=True)

        for _ret4, s, j in cand[:top_n]:
            a = arrays[s]
            entry = float(a[j + 1, 1]) * (1.0 + slip)
            stop = entry - stop_atr * float(atr[s][j])
            if entry - stop <= 0:
                continue
            target = entry + target_r * (entry - stop)
            exit_i = exit_px = why = None
            for k in range(j + 1, min(j + 1 + max_hold, len(a))):
                if a[k, 3] <= stop:
                    exit_i, exit_px, why = k, stop, "SL"
                    break
                if a[k, 2] >= target:
                    exit_i, exit_px, why = k, target, "TP"
                    break
            if exit_i is None:
                exit_i = min(j + 1 + max_hold, len(a) - 1)
                exit_px, why = float(a[exit_i, 4]), "TIME"
            out.append(_send(s, "LONG", j + 1, exit_i, entry, stop, target,
                             float(exit_px), slip, why))
            busy_until[s] = exit_i + 1
    return out


def fmt(name: str, s: dict) -> str:
    if not s.get("n"):
        return f"  {name:16} сделок нет"
    pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return (f"  {name:16} n={s['n']:4d} gross={s['gross']:+7.1f}R  "
            f"спот={s['net_spot']:+7.1f}R  фьюч={s['net_fut']:+7.1f}R  "
            f"win={s['win']:4.0f}%  PF={pf:>4}  DD={s['dd']:5.1f}R")


def halves(trades: list[dict], mid: int, fee_spot: float, fee_fut: float):
    first = [t for t in trades if t["entry_i"] < mid]
    second = [t for t in trades if t["entry_i"] >= mid]
    return summarize(first, fee_spot, fee_fut), summarize(second, fee_spot, fee_fut)


def main() -> int:
    ap = argparse.ArgumentParser(description="импульсные стратегии на крипте")
    ap.add_argument("--symbols", nargs="+", default=list(UNIVERSE))
    ap.add_argument("--days", type=int, default=540)
    ap.add_argument("--fee", type=float, default=0.001, help="спот, за сторону")
    ap.add_argument("--fee-fut", type=float, default=0.0005, help="фьючерсы, за сторону")
    ap.add_argument("--slip", type=float, default=0.0005, help="проскальзывание, за сторону")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    cdir = cache_dir(args.no_cache)
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - args.days * 86_400_000

    arrays = {}
    for sym in args.symbols:
        arr = fetch_full(sym, "1h", start_ms, end_ms, cdir, f"{sym}_1h_{args.days}d_full")
        if len(arr) > 100:
            arrays[sym] = arr
    print(f"пар: {len(arrays)} · {args.days} дн. · H1 · "
          f"fee спот {args.fee:.3%}/сторона · slip {args.slip:.3%}")

    # --- 1. импульсный пробой, по символам
    for label, trail in (("impulse 2R", None), ("impulse trail", 1.5)):
        print(f"\n=== {label} (long, H1) ===")
        all_tr = []
        for sym, arr in arrays.items():
            tr = impulse_trades(sym, arr, args.slip, trail)
            all_tr.extend(tr)
            print(fmt(sym, summarize(tr, args.fee, args.fee_fut)))
        all_tr.sort(key=lambda t: t["exit_i"])
        print(fmt("ИТОГО", summarize(all_tr, args.fee, args.fee_fut)))
        mid = max(len(a) for a in arrays.values()) // 2
        h1, h2 = halves(all_tr, mid, args.fee, args.fee_fut)
        print(fmt("  первая половина", h1))
        print(fmt("  вторая половина", h2))

    # --- 2. кросс-секционный top-gainer
    print("\n=== xs top-gainer (long, H1, топ-2) ===")
    tr = xs_trades(arrays, args.slip)
    tr.sort(key=lambda t: t["exit_i"])
    print(fmt("ИТОГО", summarize(tr, args.fee, args.fee_fut)))
    mid = max(len(a) for a in arrays.values()) // 2
    h1, h2 = halves(tr, mid, args.fee, args.fee_fut)
    print(fmt("  первая половина", h1))
    print(fmt("  вторая половина", h2))
    if tr:
        from collections import Counter
        print("  по монетам:", dict(Counter(t["symbol"] for t in tr).most_common()))
    print("\nОговорки: вселенная — текущие ликвидные монеты (survivorship bias);")
    print("фандинг и ликвидность стакана не моделировались; вход next-open.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
