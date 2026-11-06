"""All-market импульсный сканер на 1m/5m (Binance, публичные klines).

Идея: на каждом закрытом баре ищем монеты с резким импульсом и аномальным
объёмом, ранжируем по силе импульса и входим в топ-N на следующем открытии.
Выход: стоп N×ATR, цель M×R, тайм-стоп. Считаются комиссии и проскальзывание,
результат делится на две половины периода. Read-only research, ничего не торгует.

    python tools/pump_probe.py --tf 5m --days 90 --top 30
    python tools/pump_probe.py --tf 1m --days 30 --top 20 --ret 0.015
    python tools/pump_probe.py --tf 5m --days 90 --top 30 --vz 5 --ret 0.03

Оговорки: вселенная — текущие ликвидные пары (survivorship bias); задержка
API/стакана не моделируется, вход next-open; фандинг не учтён.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ta_core as ta  # noqa: E402
from tools.crypto_probe import _request, cache_dir, summarize  # noqa: E402
from tools.momentum_probe import fetch_full  # noqa: E402

STABLES = {"USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "PYUSD", "USDD",
           "USDE", "USD1", "USDG", "USDS", "USDY", "SUSD", "CUSD", "LUSD",
           "GUSD", "FRAX", "MIM", "DOLA", "CRVUSD", "GHO", "EUR", "GBP",
           "AUD", "TRY", "BRL", "UAH", "RUB", "JPY", "ZAR", "PLN", "RON",
           "ARS", "IDRT", "VAI", "UST", "USTC", "PAXG", "XAUT"}
WRAPPED = {"WBTC", "WETH", "WBETH", "STETH", "WSTETH", "CBETH", "RETH",
           "BNSOL", "JITOSOL", "MSOL"}
STOCK_TOKENS = {"SNDKB", "SOXLB", "SPCXB", "CRCLB", "GOOGLB", "MUB"}


def universe(top_n: int, min_qv: float) -> list[str]:
    info = _request("https://api.binance.com/api/v3/exchangeInfo")
    tick = _request("https://api.binance.com/api/v3/ticker/24hr")
    meta = {s["symbol"]: s for s in info.get("symbols", [])}
    rows = []
    for row in tick:
        sym = row.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        m = meta.get(sym) or {}
        if m.get("status") != "TRADING" or not m.get("isSpotTradingAllowed", True):
            continue
        base = m.get("baseAsset", "")
        if (base in STABLES or base in WRAPPED or base in STOCK_TOKENS
                or base.startswith("USD") or base.endswith("USD")
                or re.search(r"(UP|DOWN|BULL|BEAR)$", base)):
            continue
        qv = float(row.get("quoteVolume") or 0)
        if qv < min_qv:
            continue
        rows.append((qv, sym))
    rows.sort(reverse=True)
    return [s for _, s in rows[:top_n]]


def candidates(arr: np.ndarray, look: int, ret_min: float, vlook: int,
               vz: float, trades_min: float) -> dict[int, tuple[float, int]]:
    """ts -> (доходность импульса, индекс бара) для баров, прошедших фильтры."""
    c, qv, tr = arr[:, 4], arr[:, 6], arr[:, 7]
    out: dict[int, tuple[float, int]] = {}
    start = max(look, vlook, 15) + 1
    for i in range(start, len(c) - 1):
        if c[i - look] <= 0:
            continue
        ret = float(c[i] / c[i - look] - 1.0)
        if ret < ret_min:
            continue
        base = qv[i - vlook:i]
        if len(base) < vlook:
            continue
        sd = float(base.std())
        if sd <= 0 or qv[i] < float(base.mean()) + vz * sd:
            continue
        if trades_min and tr[i] < trades_min:
            continue
        out[int(arr[i, 0])] = (ret, i)
    return out


def simulate(arr: np.ndarray, sym: str, i: int, stop_atr: float,
             tp_r: float, hold: int, slip: float,
             tp_pct: float = 0.0, sl_pct: float = 0.0) -> dict | None:
    t, o, h, l, c = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
    atr = ta.atr(h, l, c, 14)
    if np.isnan(atr[i]) or atr[i] <= 0:
        return None
    entry = float(o[i + 1]) * (1.0 + slip)
    if tp_pct > 0 and sl_pct > 0:
        stop = entry * (1.0 - sl_pct)
        target = entry * (1.0 + tp_pct)
    else:
        stop = entry - stop_atr * float(atr[i])
        target = entry + tp_r * (entry - stop)
    risk = entry - stop
    if risk <= 0:
        return None
    exit_i = exit_px = why = None
    for j in range(i + 1, min(i + 1 + hold, len(c))):
        if l[j] <= stop:
            exit_i, exit_px, why = j, stop, "SL"
            break
        if h[j] >= target:
            exit_i, exit_px, why = j, target, "TP"
            break
    if exit_i is None:
        exit_i = min(i + 1 + hold, len(c) - 1)
        exit_px, why = float(c[exit_i]), "TIME"
    exit_exec = float(exit_px) * (1.0 - slip)
    return {"symbol": sym, "side": "LONG", "entry_i": i + 1, "exit_i": exit_i,
            "entry": entry, "risk": risk, "r": (exit_exec - entry) / risk,
            "exit": why, "entry_ts": float(t[i + 1]),
            "exit_ts": float(t[exit_i])}


def fmt(name: str, s: dict) -> str:
    if not s.get("n"):
        return f"  {name:14} сделок нет"
    pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return (f"  {name:14} n={s['n']:5d} gross={s['gross']:+8.1f}R  "
            f"спот={s['net_spot']:+8.1f}R  фьюч={s['net_fut']:+8.1f}R  "
            f"win={s['win']:4.0f}%  PF={pf:>4}  DD={s['dd']:6.1f}R")


def main() -> int:
    ap = argparse.ArgumentParser(description="all-market импульсный сканер")
    ap.add_argument("--tf", choices=("1m", "5m"), default="5m")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--top", type=int, default=30, help="сколько пар брать по 24ч объёму")
    ap.add_argument("--min-qv", type=float, default=20e6)
    ap.add_argument("--look", type=int, help="баров в импульсе")
    ap.add_argument("--ret", type=float, help="минимальный импульс, доля")
    ap.add_argument("--vlook", type=int, help="окно z-score объёма")
    ap.add_argument("--vz", type=float, default=3.0)
    ap.add_argument("--trades-min", type=float, default=0.0)
    ap.add_argument("--hold", type=int, help="тайм-стоп в барах")
    ap.add_argument("--stop-atr", type=float, default=1.5)
    ap.add_argument("--tp-r", type=float, default=2.0)
    ap.add_argument("--tp-pct", type=float, default=0.0,
                    help="фиксированный тейк, доля (0 — считать от ATR/TP-R)")
    ap.add_argument("--sl-pct", type=float, default=0.0,
                    help="фиксированный стоп, доля")
    ap.add_argument("--topn", type=int, default=3, help="сколько сигналов брать на бар")
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--fee-fut", type=float, default=0.0005)
    ap.add_argument("--slip", type=float, default=0.0005)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    if args.look is None:
        args.look = 5 if args.tf == "1m" else 3
    if args.ret is None:
        args.ret = 0.015 if args.tf == "1m" else 0.02
    if args.vlook is None:
        args.vlook = 120 if args.tf == "1m" else 60
    if args.hold is None:
        args.hold = 30 if args.tf == "1m" else 12

    syms = universe(args.top, args.min_qv)
    print(f"вселенная: {len(syms)} пар по 24ч объёму ≥ ${args.min_qv/1e6:.0f}M")
    print("  " + ", ".join(syms))
    cdir = cache_dir(args.no_cache)
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - args.days * 86_400_000

    arrays = {}
    for k, sym in enumerate(syms, 1):
        try:
            arr = fetch_full(sym, args.tf, start_ms, end_ms, cdir,
                             f"{sym}_{args.tf}_{args.days}d_full")
            if len(arr) > 100:
                arrays[sym] = arr
        except Exception as e:
            print(f"  {sym}: нет данных ({e})")
        if k % 10 == 0:
            print(f"  загружено {k}/{len(syms)}")
    print(f"пар с данными: {len(arrays)} · {args.tf} · {args.days} дн. · "
          f"импульс {args.look} бар ≥ {args.ret:.2%} · vz>{args.vz} · "
          f"hold {args.hold} · top{args.topn}")

    cand = {s: candidates(a, args.look, args.ret, args.vlook, args.vz,
                          args.trades_min) for s, a in arrays.items()}
    all_ts = sorted(set().union(*(set(c) for c in cand.values())))
    busy: dict[str, float] = {s: 0.0 for s in arrays}
    trades: list[dict] = []

    for ts in all_ts:
        picks = [(cand[s][ts][0], s, cand[s][ts][1])
                 for s in arrays if ts in cand[s] and ts >= busy[s]]
        picks.sort(reverse=True)
        for _ret, sym, i in picks[:args.topn]:
            tr = simulate(arrays[sym], sym, i, args.stop_atr, args.tp_r,
                          args.hold, args.slip, args.tp_pct, args.sl_pct)
            if tr:
                trades.append(tr)
                busy[sym] = tr["exit_ts"]

    trades.sort(key=lambda t: t["exit_ts"])
    print()
    print(fmt("ИТОГО", summarize(trades, args.fee, args.fee_fut)))
    mid = start_ms / 1000.0 + args.days * 43_200.0
    first = [t for t in trades if t["entry_ts"] < mid]
    second = [t for t in trades if t["entry_ts"] >= mid]
    print(fmt("  первая половина", summarize(first, args.fee, args.fee_fut)))
    print(fmt("  вторая половина", summarize(second, args.fee, args.fee_fut)))
    if trades:
        from collections import Counter
        print("  по монетам:", dict(Counter(t["symbol"] for t in trades).most_common(10)))
        print("  выходы:", dict(Counter(t["exit"] for t in trades)))
    print("\nОговорки: текущая вселенная (survivorship bias), без фандинга и")
    print("задержки API; вход next-open, проскальзывание зашито в цену.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
