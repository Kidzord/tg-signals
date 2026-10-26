"""Модель ICT 2022: свип ликвидности → дисплейсмент с FVG → вход на возврате в гэп.

Правила:
  * свип: бар снимает экстремум последних swing_len баров (низ для лонга);
  * дисплейсмент: после свипа импульс образует FVG (бычий: l[i] > h[i-2]);
  * вход: цена возвращается в гэп (лоу залезает в его верхнюю границу),
    но гэп не перекрыт целиком;
  * стоп — за экстремум свипа с буфером; цель — фиксированные R.

Вход — по рынку на открытии следующего бара, как в range_v2/pdl_sweep.
"""
from __future__ import annotations

import datetime as dt

import numpy as np

import strategy as st
import ta_core as ta


def signal_at(state: dict, chart: st.Frame, i: int, atr_i: float,
              swing_len: int = 20, fvg_window: int = 20, retest_look: int = 40,
              stop_buf_atr: float = 0.25, bar_sec: int = 900) -> tuple[str, float] | None:
    """Один шаг машины состояний ICT 2022 на закрытом баре i.

    state — {"l": setup|None, "s": setup|None}, живёт между барами (у бота
    хранится в bot-state, у бэктеста — локальный словарь). Сетап:
    {"sweep": ts, "ext": float, "fvg": ts, "top": float, "bot": float}.

    Возвращает ("LONG"|"SHORT", stop_raw) на баре сигнала или None.
    """
    if i < swing_len + 2 or np.isnan(atr_i) or atr_i <= 0:
        return None
    t = int(chart.t[i])
    h, l, c = chart.h, chart.l, chart.c
    a = float(atr_i)

    swept_dn = l[i] < np.min(l[i - swing_len:i])
    swept_up = h[i] > np.max(h[i - swing_len:i])

    sig = None

    # --- лонг: свип низа -> первый бычий FVG -> возврат в гэп
    L = state["l"]
    if L is not None and swept_dn:
        L["ext"] = min(L["ext"], float(l[i]))
    if L is None and swept_dn:
        state["l"] = L = {"sweep": t, "ext": float(l[i]), "fvg": 0, "top": 0.0, "bot": 0.0}
    if L is not None:
        if not L["fvg"]:
            if t - L["sweep"] > fvg_window * bar_sec:
                state["l"] = None
            elif l[i] > h[i - 2]:
                L["fvg"] = t
                L["top"] = float(l[i])
                L["bot"] = float(h[i - 2])
        elif t > L["fvg"]:
            if t - L["fvg"] > retest_look * bar_sec:
                state["l"] = None
            elif l[i] <= L["top"] and c[i] > L["bot"]:
                look = retest_look + fvg_window
                ext = float(np.min(l[max(0, i - look):i]))
                sig = ("LONG", ext - a * stop_buf_atr)
                state["l"] = None
            elif c[i] < L["bot"] - a:
                state["l"] = None

    # --- шорт: свип верха -> первый медвежий FVG -> возврат в гэп
    S = state["s"]
    if S is not None and swept_up:
        S["ext"] = max(S["ext"], float(h[i]))
    if S is None and swept_up:
        state["s"] = S = {"sweep": t, "ext": float(h[i]), "fvg": 0, "top": 0.0, "bot": 0.0}
    if S is not None:
        if not S["fvg"]:
            if t - S["sweep"] > fvg_window * bar_sec:
                state["s"] = None
            elif h[i] < l[i - 2]:
                S["fvg"] = t
                S["top"] = float(h[i])
                S["bot"] = float(l[i - 2])
        elif t > S["fvg"]:
            if t - S["fvg"] > retest_look * bar_sec:
                state["s"] = None
            elif h[i] >= S["bot"] and c[i] < S["top"]:
                look = retest_look + fvg_window
                ext = float(np.max(h[max(0, i - look):i]))
                if sig is None:                 # при двойном сигнале лонг приоритетнее (как в бэктесте)
                    sig = ("SHORT", ext + a * stop_buf_atr)
                state["s"] = None
            elif c[i] > S["top"] + a:
                state["s"] = None

    return sig


def backtest(chart: st.Frame, swing_len: int = 20, fvg_window: int = 20,
             retest_look: int = 40, stop_buf_atr: float = 0.25,
             tp_r: float = 2.0, max_bars: int = 120, atr_len: int = 14) -> list[dict]:
    n = len(chart.c)
    if n < swing_len + fvg_window + retest_look + 10:
        return []
    atr = ta.atr(chart.h, chart.l, chart.c, atr_len)

    state = {"l": None, "s": None}
    trades: list[dict] = []
    i = swing_len + 2
    busy_until = -1                       # пока открыта сделка, машина состояний работает, но входы пропускаем
    while i < n - 1:
        sig = signal_at(state, chart, i, atr[i], swing_len, fvg_window,
                        retest_look, stop_buf_atr)
        if sig is None or i <= busy_until:
            i += 1
            continue

        side, stop_raw = sig
        sgn = 1 if side == "LONG" else -1

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
            hit_sl = chart.l[j] <= stop if sgn > 0 else chart.h[j] >= stop
            hit_tp = chart.h[j] >= target if sgn > 0 else chart.l[j] <= target
            if j == i + 1:
                # в баре заполнения стоп/цель могут выбить уже на открытии
                if sgn > 0:
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
            exit_i, exit_price, why = j, float(chart.c[j]), "TIME"

        r = (exit_price - entry) * sgn / risk
        trades.append({"side": side, "entry_i": i, "exit_i": exit_i, "entry": entry,
                       "stop": stop, "target": target, "r": r, "exit": why})
        busy_until = exit_i
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
    print(f"ICT 2022 · {bot.SYMBOL} {feeds.tf_label(bot.TF)} · {span:.0f} дн. · {len(trades)} сделок")
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
