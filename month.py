"""Статистика за последние N дней по всем инструментам на одном счёте.

    python month.py [дней]

Собирает сделки всех символов, сортирует по времени входа и ведёт один баланс.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot
import range_v2 as r2

START = float(os.getenv("PREVIEW_START", "5000"))
RISK_PCT = float(os.getenv("PREVIEW_RISK", "0.5"))
TP_R = float(os.getenv("RANGE_TP_R", "2.0"))
try:
    DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
except ValueError:
    DAYS = 30
SYMBOLS = ["XAUUSD", "AUDUSD", "USDCAD", "GBPUSD", "US500", "EUSTX50", "UK100", "DE40", "JPN225"]


def collect(data) -> list[dict]:
    out = []
    for sym in SYMBOLS:
        try:
            chart, htf = bot.load_frames(data, sym, "15", int(os.getenv("BOT_BARS", "20000")))
        except Exception as e:
            print(f"  {sym}: нет данных ({e})")
            continue
        H = htf["240"]
        zones = r2.zones_for_chart(chart, H, touches=2, flips_keep=bot.RANGE_FLIP_KEEP)
        smt = None
        if bot.SMT_LOOK > 0 and sym == bot.SMT_FOR:
            try:
                ref = data.frame(bot.SMT_SYMBOL, "15", int(os.getenv("BOT_BARS", "20000")))
                smt = r2.smt_ref(chart, ref)
            except Exception as e:
                print(f"  {sym}: нет данных {bot.SMT_SYMBOL} ({e})")
        trades = r2.backtest(chart, zones, H, stop_atr=bot.RANGE_STOP_ATR,
                             max_stops=bot.RANGE_MAX_STOPS, tp_r=TP_R,
                             max_r=TP_R + 2.0, session=bot.RANGE_SESSION,
                             smt=smt, smt_look=bot.SMT_LOOK,
                             max_bars=bot.PARAMS.max_bars, er_max=bot.RANGE_ER_MAX)
        for t in trades:
            t["symbol"] = sym
            t["t_in"] = float(chart.t[t["entry_i"]])
            t["t_out"] = float(chart.t[t["exit_i"]])
            out.append(t)
    out.sort(key=lambda x: x["t_in"])
    return out


def balance_curve(sel: list[dict]) -> tuple[list[tuple[dict, float, float]], int]:
    """Баланс по сделкам с дневным риск-гардом, как в живом боте.

    После N стопов за сутки новых входов нет (открытые доводятся до конца).
    Возвращает список (сделка, pnl, баланс) и сколько входов пропущено по лимиту.
    """
    bal = START
    rows: list[tuple[dict, float, float]] = []
    day_stops: dict[str, int] = {}
    skipped = 0
    for t in sel:
        day = dt.datetime.fromtimestamp(t["t_in"], dt.timezone.utc).strftime("%Y-%m-%d")
        if bot.MAX_DAILY_STOPS > 0 and day_stops.get(day, 0) >= bot.MAX_DAILY_STOPS:
            skipped += 1
            continue
        risk_usd = bal * RISK_PCT / 100.0
        pnl = t["r"] * risk_usd
        bal += pnl
        if t["r"] < 0:
            day_stops[day] = day_stops.get(day, 0) + 1
        rows.append((t, pnl, bal))
    return rows, skipped


def main() -> None:
    data = bot.MT5Data()
    try:
        trades = collect(data)
    finally:
        data.close()

    if not trades:
        print("сделок нет")
        return

    last = max(t["t_out"] for t in trades)
    cutoff = last - DAYS * 86400
    sel = [t for t in trades if t["t_in"] >= cutoff]

    print(f"период: {DAYS} дн. · сигналов {len(sel)} · инструментов {len({t['symbol'] for t in sel})}\n")
    if not sel:
        return

    rows, skipped = balance_curve(sel)
    bal = rows[-1][2]

    rs = [t["r"] for t, _, _ in rows]
    wins = sum(1 for r in rs if r > 0)
    print(f"итог: {len(rs)} сделок · прибыльных {wins} ({wins / len(rs) * 100:.0f}%) · "
          f"сумма {sum(rs):+.1f}R" + (f" · пропущено по лимиту {skipped}" if skipped else ""))
    print(f"баланс: {START:.0f} → {bal:.0f}$ ({(bal - START) / START * 100:+.1f}%)\n")

    # риск: худший день и максимальная просадка баланса от пика
    by_day: dict[str, float] = {}
    by_day_usd: dict[str, float] = {}
    for t, pnl, _ in rows:
        day = dt.datetime.fromtimestamp(t["t_in"], dt.timezone.utc).strftime("%d.%m")
        by_day[day] = by_day.get(day, 0.0) + t["r"]
        by_day_usd[day] = by_day_usd.get(day, 0.0) + pnl
    worst = min(by_day, key=lambda d: by_day[d])
    peak = START
    dd = 0.0
    for _, _, b in rows:
        peak = max(peak, b)
        dd = min(dd, (b - peak) / peak * 100.0)
    print(f"риск: дней в сделках {len(by_day)} · худший день {worst} "
          f"{by_day[worst]:+.1f}R ({by_day_usd[worst]:+.1f}$) · макс. просадка {dd:.1f}%\n")

    by_sym: dict[str, list[float]] = {}
    for t, _, _ in rows:
        by_sym.setdefault(t["symbol"], []).append(t["r"])
    print("по инструментам:")
    for s, v in sorted(by_sym.items(), key=lambda x: -sum(x[1])):
        print(f"  {s:9} {len(v):>3} сделок · {sum(v):>+6.1f}R")

    print("\nсделки:")
    print(f"{'вход':13} {'символ':9} {'сторона':7} {'вход':>10} {'выход':>10} {'R':>6} {'$':>7} {'баланс':>8}")
    for t, pnl, b in rows:
        when = dt.datetime.fromtimestamp(t["t_in"], dt.timezone.utc)
        print(f"{when:%d.%m %H:%M}   {t['symbol']:9} {t['side']:7} {t['entry']:>10.2f} "
              f"{t['stop'] if t['exit'].startswith('SL') else t['target']:>10.2f} "
              f"{t['r']:>+6.2f} {pnl:>+7.1f} {b:>8.0f}")


if __name__ == "__main__":
    main()
