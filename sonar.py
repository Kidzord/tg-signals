"""Sonar-подобный вход: реакция от кластера уровней после манипуляции.

Логика по мотивам разобранного видео:
  1. Уровень — цена, где рынок несколько раз не смог пройти (кластер пивотов).
  2. Манипуляция — цена прокалывает уровень, но закрывается обратно.
  3. Вход — по закрытию бара-реакции, против прокола.
  4. Стоп — за экстремум прокола, цель — следующий уровень или 2R.

Считаем по барам без заглядывания вперёд: пивот подтверждается через `frac_r` баров.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import numpy as np

# скрипт может запускаться не из своей папки
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import strategy as st
import ta_core as ta


def build_atr(frame: st.Frame, n: int = 14) -> np.ndarray:
    return ta.atr(frame.h, frame.l, frame.c, n)


def find_levels(
    frame: st.Frame,
    atr: np.ndarray,
    frac_l: int = 3,
    frac_r: int = 3,
    tol_mult: float = 0.25,
    min_touches: int = 2,
    min_age: int = 8,
    break_mult: float = 2.0,
) -> list[dict]:
    """Уровни, известные на каждом баре. Возвращает список (bar, level)."""
    n = len(frame.c)
    ph = ta.pivot_high(frame.h, frac_l, frac_r)
    pl = ta.pivot_low(frame.l, frac_l, frac_r)

    active: list[dict] = []
    out: list[dict] = []

    for i in range(n):
        a = atr[i] if not np.isnan(atr[i]) else 0.0
        tol = a * tol_mult

        for kind, piv in (("res", ph[i]), ("sup", pl[i])):
            if np.isnan(piv):
                continue
            merged = False
            for lv in active:
                if lv["kind"] == kind and abs(lv["price"] - piv) <= tol:
                    lv["touches"] += 1
                    lv["price"] = (lv["price"] * (lv["touches"] - 1) + piv) / lv["touches"]
                    merged = True
                    break
            if not merged:
                active.append({"kind": kind, "price": float(piv), "touches": 1, "born": i})

        # снимаем пробитые уровни
        keep = []
        for lv in active:
            broken = (lv["kind"] == "res" and frame.c[i] > lv["price"] + tol * break_mult) or \
                     (lv["kind"] == "sup" and frame.c[i] < lv["price"] - tol * break_mult)
            if not broken:
                keep.append(lv)
        active = keep

        out.append({
            "bar": i,
            "levels": [dict(lv) for lv in active if lv["touches"] >= min_touches
                       and i - lv["born"] >= min_age],
        })
    return out


def detect_at(frame: st.Frame, atr: np.ndarray, levels_by_bar: list[dict], i: int,
              sweep_max: float = 0.8, stop_buf: float = 0.25) -> tuple[str, float] | None:
    """Есть ли вход на баре i: прокол уровня с возвратом за него."""
    a = atr[i]
    if np.isnan(a) or a <= 0:
        return None
    for lv in levels_by_bar[i]["levels"]:
        price = lv["price"]
        if lv["kind"] == "res" and frame.h[i] > price and frame.c[i] < price:
            if frame.h[i] - price <= a * sweep_max:
                return "SHORT", float(frame.h[i] + a * stop_buf)
        if lv["kind"] == "sup" and frame.l[i] < price and frame.c[i] > price:
            if price - frame.l[i] <= a * sweep_max:
                return "LONG", float(frame.l[i] - a * stop_buf)
    return None


def backtest(
    frame: st.Frame,
    atr: np.ndarray,
    levels_by_bar: list[dict],
    sweep_max: float = 0.8,
    stop_buf: float = 0.25,
    target_r: float = 2.0,
    max_bars: int = 96,
    tol_mult: float = 0.25,
) -> list[dict]:
    n = len(frame.c)
    trades: list[dict] = []
    i = 1
    while i < n - 1:
        a = atr[i] if not np.isnan(atr[i]) else 0.0
        if a <= 0:
            i += 1
            continue
        tol = a * tol_mult
        sig = detect_at(frame, atr, levels_by_bar, i, sweep_max, stop_buf)

        if not sig:
            i += 1
            continue

        side, stop = sig
        entry = float(frame.c[i])
        risk = abs(entry - stop)
        if risk <= 0:
            i += 1
            continue

        sgn = 1 if side == "LONG" else -1

        # цель — следующий уровень в сторону сделки, но не ближе 1.5R и не дальше 4R
        target = entry + sgn * risk * target_r
        for lv in levels_by_bar[i]["levels"]:
            p = lv["price"]
            if side == "LONG" and lv["kind"] == "res" and p > entry + sgn * risk * 1.5:
                if abs(p - entry) < abs(target - entry):
                    target = p
            if side == "SHORT" and lv["kind"] == "sup" and p < entry - risk * 1.5:
                if abs(p - entry) < abs(target - entry):
                    target = p

        rr = abs(target - entry) / risk
        exit_i, exit_price, why = None, None, None
        for j in range(i + 1, min(i + 1 + max_bars, n)):
            hit_sl = frame.l[j] <= stop if side == "LONG" else frame.h[j] >= stop
            hit_tp = frame.h[j] >= target if side == "LONG" else frame.l[j] <= target
            if hit_sl:
                exit_i, exit_price, why = j, stop, "SL"
                break
            if hit_tp:
                exit_i, exit_price, why = j, target, "TP"
                break
        if exit_i is None:
            j = min(i + max_bars, n - 1)
            exit_i, exit_price, why = j, float(frame.c[j]), "TIME"

        r = (exit_price - entry) * sgn / risk
        trades.append({
            "side": side, "entry_i": i, "exit_i": exit_i, "entry": entry,
            "stop": stop, "target": target, "exit_price": exit_price,
            "r": r, "exit": why, "rr": rr,
        })
        i = exit_i + 1
    return trades


def main() -> None:
    import bot
    import feeds

    data = bot.MT5Data()
    try:
        bars = int(__import__("os").getenv("BOT_BARS", "20000"))
        chart, htf = bot.load_frames(data, bot.SYMBOL, bot.TF, bars)
    finally:
        data.close()

    atr = build_atr(chart)
    levels = find_levels(chart, atr)
    trades = backtest(chart, atr, levels)

    if not trades:
        print("сделок нет")
        return

    rs = [t["r"] for t in trades]
    wins = sum(1 for r in rs if r > 0)
    span = (chart.t[-1] - chart.t[0]) / 86400.0
    longs = sum(1 for t in trades if t["side"] == "LONG")
    by_exit: dict[str, int] = {}
    for t in trades:
        by_exit[t["exit"]] = by_exit.get(t["exit"], 0) + 1

    print(f"Sonar-вход · {bot.SYMBOL} {feeds.tf_label(bot.TF)} · {span:.0f} дн. · {len(trades)} сделок")
    print(f"лонг {longs}, шорт {len(trades) - longs} · прибыльных {wins} ({wins / len(rs) * 100:.0f}%)")
    print(f"сумма {sum(rs):+.2f}R · средняя {sum(rs) / len(rs):+.2f}R · "
          f"лучшая {max(rs):+.2f}R · худшая {min(rs):+.2f}R")
    print("выходы:", ", ".join(f"{k} {v}" for k, v in sorted(by_exit.items(), key=lambda x: -x[1])))
    print("\nпоследние:")
    for t in trades[-8:]:
        when = dt.datetime.fromtimestamp(float(chart.t[t["entry_i"]]), dt.timezone.utc)
        print(f"  {when:%d.%m %H:%M}  {t['side']:5}  {t['entry']:.2f} → {t['exit_price']:.2f}  "
              f"{t['exit']:4}  {t['r']:+.2f}R  (RR план {t['rr']:.1f})")


if __name__ == "__main__":
    main()
