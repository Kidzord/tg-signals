"""Снятие экстремума предыдущего дня (PDH/PDL sweep) с возвратом.

Правила (по мотивам бэктеста ikeawesom/xauusd-backtest: XAUUSD, 2004-2024,
~71% прибыльных на M15):
  * дневной уклон: закрытие прошлого дня выше открытия — день лонг-бай,
    ниже — шорт;
  * лонг: цена прокалывает минимум прошлого дня (PDL) и закрытый бар
    возвращается выше него;
  * шорт: зеркально от максимума прошлого дня (PDH);
  * стоп — за экстремум свипа текущего дня с буфером;
  * цель — фиксированные R; незакрытое закрывается в конце дня.

Вход — по рынку на открытии следующего бара (entry_model как в range_v2).
"""
from __future__ import annotations

import datetime as dt

import numpy as np

import strategy as st
import ta_core as ta


def _day_key(ts: float) -> str:
    return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).strftime("%Y-%m-%d")


def backtest(chart: st.Frame, tp_r: float = 2.0, stop_buf_atr: float = 0.25,
             max_bars: int = 96, atr_len: int = 14) -> list[dict]:
    """Прогон по истории. Сделки помечены exit: TP / SL / EOD (конец дня)."""
    n = len(chart.c)
    if n < 100:
        return []
    atr = ta.atr(chart.h, chart.l, chart.c, atr_len)

    # уровни прошлого дня на каждый бар: сначала статистика по дням, потом
    # разметка баров предыдущим днём
    pdh = np.full(n, np.nan)
    pdl = np.full(n, np.nan)
    bull_day = np.zeros(n, dtype=bool)
    days: dict[str, dict] = {}
    for i in range(n):
        d = _day_key(chart.t[i])
        dd = days.setdefault(d, {"hi": -np.inf, "lo": np.inf,
                                 "o": float(chart.o[i]), "c": float(chart.c[i])})
        dd["hi"] = max(dd["hi"], float(chart.h[i]))
        dd["lo"] = min(dd["lo"], float(chart.l[i]))
        dd["c"] = float(chart.c[i])
    keys = sorted(days)
    day_pos = {d: j for j, d in enumerate(keys)}
    for i in range(n):
        j = day_pos[_day_key(chart.t[i])]
        if j == 0:
            continue
        p = days[keys[j - 1]]
        pdh[i], pdl[i] = p["hi"], p["lo"]
        bull_day[i] = p["c"] > p["o"]

    trades: list[dict] = []
    i = 1
    while i < n - 1:
        if np.isnan(pdl[i]) or np.isnan(atr[i]) or atr[i] <= 0:
            i += 1
            continue
        day = _day_key(chart.t[i])

        sig = None
        # лонг: прокол PDL и возврат выше
        if bull_day[i] and chart.l[i] < pdl[i] and chart.c[i] > pdl[i]:
            sig = ("LONG", pdl[i])
        # шорт: прокол PDH и возврат ниже
        elif (not bull_day[i]) and chart.h[i] > pdh[i] and chart.c[i] < pdh[i]:
            sig = ("SHORT", pdh[i])
        if not sig:
            i += 1
            continue

        side, level = sig
        sgn = 1 if side == "LONG" else -1

        # экстремум свипа в текущем дне до бара i
        j0 = i
        while j0 > 0 and _day_key(chart.t[j0 - 1]) == day:
            j0 -= 1
        if side == "LONG":
            sweep_ext = float(np.min(chart.l[j0:i + 1]))
            stop_raw = sweep_ext - float(atr[i]) * stop_buf_atr
        else:
            sweep_ext = float(np.max(chart.h[j0:i + 1]))
            stop_raw = sweep_ext + float(atr[i]) * stop_buf_atr

        if i + 1 >= n - 1:
            break
        entry = float(chart.o[i + 1])
        risk = abs(entry - stop_raw)
        if risk <= 0:
            i += 1
            continue
        stop = entry - sgn * risk
        target = entry + sgn * risk * tp_r

        exit_i = exit_price = why = None
        for j in range(i + 1, min(i + 1 + max_bars, n)):
            # конец дня: первый бар следующего дня закрываем по его открытию
            if _day_key(chart.t[j]) != day:
                exit_i, exit_price, why = j, float(chart.o[j]), "EOD"
                break
            hit_sl = chart.l[j] <= stop if side == "LONG" else chart.h[j] >= stop
            hit_tp = chart.h[j] >= target if side == "LONG" else chart.l[j] <= target
            if j == i + 1:
                if side == "LONG":
                    if chart.o[j] <= stop:
                        exit_i, exit_price, why = j, stop, "SL"
                        break
                    if chart.o[j] >= target:
                        exit_i, exit_price, why = j, target, "TP"
                        break
                else:
                    if chart.o[j] >= stop:
                        exit_i, exit_price, why = j, stop, "SL"
                        break
                    if chart.o[j] <= target:
                        exit_i, exit_price, why = j, target, "TP"
                        break
            if hit_sl:
                exit_i, exit_price, why = j, stop, "SL"
                break
            if hit_tp:
                exit_i, exit_price, why = j, target, "TP"
                break
        if exit_i is None:
            j = min(i + max_bars, n - 1)
            exit_i, exit_price, why = j, float(chart.c[j]), "EOD"

        r = (exit_price - entry) * sgn / risk
        trades.append({"side": side, "entry_i": i, "exit_i": exit_i, "entry": entry,
                       "stop": stop, "target": target, "r": r, "exit": why})
        i = exit_i + 1
    return trades


def main() -> None:
    import bot
    import feeds

    data = bot.MT5Data()
    try:
        bars = int(__import__("os").getenv("BOT_BARS", "20000"))
        chart, _htf = bot.load_frames(data, bot.SYMBOL, bot.TF, bars)
    finally:
        data.close()

    trades = backtest(chart)
    if not trades:
        print("сделок нет")
        return
    rs = [t["r"] for t in trades]
    wins = sum(1 for r in rs if r > 0)
    longs = sum(1 for t in trades if t["side"] == "LONG")
    span = (chart.t[-1] - chart.t[0]) / 86400.0
    by_exit: dict[str, int] = {}
    for t in trades:
        by_exit[t["exit"]] = by_exit.get(t["exit"], 0) + 1
    print(f"PDH/PDL sweep · {bot.SYMBOL} {feeds.tf_label(bot.TF)} · {span:.0f} дн. · {len(trades)} сделок")
    print(f"лонг {longs}, шорт {len(trades) - longs} · прибыльных {wins} ({wins / len(rs) * 100:.0f}%)")
    print(f"сумма {sum(rs):+.2f}R · средняя {sum(rs) / len(rs):+.2f}R · "
          f"лучшая {max(rs):+.2f}R · худшая {min(rs):+.2f}R")
    print("выходы:", ", ".join(f"{k} {v}" for k, v in sorted(by_exit.items(), key=lambda x: -x[1])))
    print("\nпоследние:")
    for t in trades[-8:]:
        when = dt.datetime.fromtimestamp(float(chart.t[t["entry_i"]]), dt.timezone.utc)
        print(f"  {when:%d.%m %H:%M}  {t['side']:5}  {t['entry']:.2f} → {t['exit']:3}  {t['r']:+.2f}R")


if __name__ == "__main__":
    main()
