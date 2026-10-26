"""Версия 2: торговля от границ зон старшего ТФ (по видео и MSNR-стратегии).

Отличия от sonar.py:
  * зоны строятся на H4/D1, а не на рабочем ТФ;
  * вход — лимиткой от границы зоны (цена вошла в зону и закрылась обратно);
  * стоп короткий, за границей зоны (по видео 3–5$ на золоте);
  * цель — противоположная зона, иначе фиксированные 2R.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sonar
import strategy as st
import ta_core as ta


def zones_for_chart(chart: st.Frame, htf_frame: st.Frame, touches: int = 2,
                    tol_mult: float = 0.2, frac: int = 3, body: bool = True,
                    flips_keep: int = 0, flip_tol: float = 0.1) -> list[dict]:
    """Зоны старшего ТФ, разложенные по барам рабочего ТФ.

    body=True — уровни по телам свечей (как в курсе MSNR).
    flips_keep>0 — добавлять перевёрнутые уровни: пробитая телом зона какое-то
    время работает с другой стороны (support→resistance), как в курсе.
    """
    src = htf_frame
    if body:
        bh = np.maximum(htf_frame.o, htf_frame.c)
        bl = np.minimum(htf_frame.o, htf_frame.c)
        src = st.Frame(htf_frame.t, htf_frame.o, bh, bl, htf_frame.c, htf_frame.v)

    atr_h = ta.atr(src.h, src.l, src.c, 14)
    table = sonar.find_levels(src, atr_h, frac_l=frac, frac_r=frac,
                              tol_mult=tol_mult, min_touches=touches, min_age=6)

    if flips_keep > 0:
        merged_table = []
        flips: list[dict] = []
        flipped_at: dict[float, int] = {}
        for i, row in enumerate(table):
            a = atr_h[i] if not np.isnan(atr_h[i]) else 0.0
            for lv in row["levels"]:
                p = float(lv["price"])
                key = round(p, 2)
                if lv["kind"] == "res" and htf_frame.c[i] > p + a * flip_tol:
                    flips.append({"kind": "sup", "price": p, "until": i + flips_keep})
                    flipped_at[key] = i
                elif lv["kind"] == "sup" and htf_frame.c[i] < p - a * flip_tol:
                    flips.append({"kind": "res", "price": p, "until": i + flips_keep})
                    flipped_at[key] = i
            flips = [f for f in flips if f["until"] >= i]

            # пробитый уровень больше не работает в исходную сторону
            levels = [dict(x) for x in row["levels"]
                      if flipped_at.get(round(float(x["price"]), 2), 10 ** 9) > i]
            levels += [{"kind": f["kind"], "price": f["price"], "touches": 2, "born": i}
                       for f in flips]
            merged_table.append({"bar": row["bar"], "levels": levels})
        table = merged_table

    idx = np.searchsorted(htf_frame.t, chart.t, side="right") - 1
    out = []
    for i in range(len(chart.c)):
        # берём ЗАКРЫТЫЙ бар H4 (j-1): внутри текущего H4 зоны ещё не известны
        j = idx[i] - 1
        out.append(table[j] if j >= 0 else {"bar": -1, "levels": []})
    return out


def pick_target(levels: list[dict], side: str, entry: float, risk: float,
                tp_r: float, min_rr: float = 1.0, max_r: float = 4.0) -> float:
    """Цель: фиксированные tp_r, но если есть противоположная зона в диапазоне
    [tp_r, max_r] — тянем цель до неё. Одну и ту же функцию использует и
    бэктест, и живой бот, чтобы статистика не расходилась с торговлей."""
    sgn = 1 if side == "LONG" else -1
    target = entry + sgn * risk * tp_r
    best = None
    for lv in levels:
        p = float(lv["price"])
        if side == "LONG" and lv["kind"] == "res" and p > entry + risk * min_rr:
            if best is None or p < best:
                best = p
        if side == "SHORT" and lv["kind"] == "sup" and p < entry - risk * min_rr:
            if best is None or p > best:
                best = p
    if best is not None:
        dist_r = abs(best - entry) / risk
        if tp_r <= dist_r <= max_r:
            target = best
    return target


def smt_ref(chart: st.Frame, ref: st.Frame | None):
    """Свечи парного инструмента, выровненные по времени бара рабочего графика.

    Инструменты торгуются в одни часы, но бары могут не совпадать (пропуски,
    разное открытие), поэтому берём не индекс, а время бара. None — нет данных.
    """
    if ref is None or len(ref.c) == 0:
        return None
    j = np.clip(np.searchsorted(ref.t, chart.t, side="right") - 1, 0, len(ref.c) - 1)
    return ref.h[j], ref.l[j]


def smt_ok(chart: st.Frame, ref, i: int, side: str, look: int) -> bool:
    """SMT-дивергенция (Gold/Silver): наш инструмент снял экстремум, парный — нет.

    LONG: золото обновило минимум за look баров, серебро свой удержало —
    продавец слабее, чем кажется по золоту.
    SHORT: золото обновило максимум, серебро нет.
    ref=None или look<=0 — подтверждения не требуем (фильтр выключен).
    """
    if ref is None or look <= 0 or i < look:
        return True
    bh, bl = ref
    if side == "LONG":
        return (float(chart.l[i]) <= float(np.min(chart.l[i - look:i]))
                and float(bl[i]) > float(np.min(bl[i - look:i])))
    return (float(chart.h[i]) >= float(np.max(chart.h[i - look:i]))
            and float(bh[i]) < float(np.max(bh[i - look:i])))


def _note_stop(stops: dict, entry: float, side: str) -> None:
    """Учесть стоп по уровню: ключ — цена уровня и его роль, с которой шли в сделку."""
    key = (round(float(entry), 2), "sup" if side == "LONG" else "res")
    stops[key] = stops.get(key, 0) + 1


def backtest(chart: st.Frame, zones: list[dict], htf_frame: st.Frame,
             stop_pts: float = 4.0, tp_r: float = 2.0, max_bars: int = 120,
             min_rr: float = 1.0, stop_mode: str = "points", swing_len: int = 12,
             stop_buf: float = 0.0, max_stop_pts: float = 20.0,
             max_stops: int = 2, stop_atr: float = 0.0, max_r: float = 4.0,
             session: str = "", smt=None, smt_look: int = 0,
             entry_model: str = "next_open", er_max: float = 0.0) -> list[dict]:
    """entry_model:
      next_open — вход по рынку на открытии следующего бара (реалистично);
      limit     — лимитка по цене уровня исполняется на баре сигнала (старая
                  оптимистичная модель, для сравнения).
    er_max > 0 — фильтр Kaufman ER до входа, как в живом боте."""
    n = len(chart.c)
    atr_h = ta.atr(htf_frame.h, htf_frame.l, htf_frame.c, 14)
    idx = np.searchsorted(htf_frame.t, chart.t, side="right") - 1

    atr_c = ta.atr(chart.h, chart.l, chart.c, 14)
    er = ta.efficiency_ratio(chart.c) if er_max > 0 else None
    trades: list[dict] = []
    # Стопы по уровням. После max_stops неудач уровень блокируем — но не по
    # таймеру, а структурно: пока он не исчезнет из таблицы зон, т.е. пока его
    # не сломали. Так один «мёртвый» уровень не даёт серию стопов днями.
    stops: dict[tuple, int] = {}
    i = 1
    while i < n - 1:
        if not st.in_session(float(chart.t[i]), session):
            i += 1
            continue
        # фильтр режима: во флэте преимущество, в тренде его почти нет
        if er is not None and (np.isnan(er[i]) or er[i] > er_max):
            i += 1
            continue
        lv_list = zones[i]["levels"]
        present = {(round(float(x["price"]), 2), x["kind"]) for x in lv_list}
        for k in list(stops):
            if k not in present:
                stops.pop(k, None)
        sig = None
        for lv in lv_list:
            price = float(lv["price"])
            if max_stops > 0 and stops.get((round(price, 2), lv["kind"]), 0) >= max_stops:
                continue
            if lv["kind"] == "sup" and chart.l[i] <= price and chart.c[i] > price:
                sig = ("LONG", price)
                break
            if lv["kind"] == "res" and chart.h[i] >= price and chart.c[i] < price:
                sig = ("SHORT", price)
                break
        if not sig:
            i += 1
            continue

        side, level_px = sig
        sgn = 1 if side == "LONG" else -1

        # SMT-подтверждение входа (парный инструмент не снял тот же экстремум)
        if not smt_ok(chart, smt, i, side, smt_look):
            i += 1
            continue

        if stop_mode == "swing":
            # стоп за ближайший свинг до бара входа
            lo_w = chart.l[max(0, i - swing_len):i]
            hi_w = chart.h[max(0, i - swing_len):i]
            if len(lo_w) == 0:
                i += 1
                continue
            if side == "LONG":
                stop = float(np.min(lo_w)) - stop_buf
            else:
                stop = float(np.max(hi_w)) + stop_buf
            risk = abs(level_px - stop)
            if risk <= 0 or risk > max_stop_pts:
                i += 1
                continue
        else:
            if stop_atr > 0:
                a_c = atr_c[i]
                if np.isnan(a_c) or a_c <= 0:
                    i += 1
                    continue
                risk = float(a_c) * stop_atr
            else:
                risk = stop_pts

        if risk <= 0:
            i += 1
            continue

        if entry_model == "next_open":
            # сигнал виден только на закрытии бара: вход — по рынку на открытии
            # следующего. Стоп и цель привязываем к фактической цене входа.
            if i + 1 >= n - 1:
                break
            entry = float(chart.o[i + 1])
        else:
            entry = float(level_px)

        stop = entry - sgn * risk
        # цель — ближайшая противоположная зона, иначе tp_r
        target = pick_target(zones[i]["levels"], side, entry, risk, tp_r, min_rr, max_r)

        # стоп мог сработать на самом баре входа: у лимитки цена могла пройти
        # сквозь неё вниз и выбить стоп в том же баре; при входе по рынку это
        # покрывает цикл выхода ниже (он начинается с бара заполнения)
        if entry_model == "limit" and (
                (side == "LONG" and chart.l[i] <= stop) or (side == "SHORT" and chart.h[i] >= stop)):
            r = -1.0
            trades.append({"side": side, "entry_i": i, "exit_i": i, "entry": entry,
                           "stop": stop, "target": target, "r": r, "exit": "SL-bar"})
            _note_stop(stops, entry, side)
            i += 1
            continue

        exit_i = exit_price = why = None
        for j in range(i + 1, min(i + 1 + max_bars, n)):
            hit_sl = chart.l[j] <= stop if side == "LONG" else chart.h[j] >= stop
            hit_tp = chart.h[j] >= target if side == "LONG" else chart.l[j] <= target
            # в баре заполнения стоп/цель честно могут выбить уже на открытии
            if entry_model == "next_open" and j == i + 1:
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
            exit_i, exit_price, why = j, float(chart.c[j]), "TIME"

        r = (exit_price - entry) * sgn / risk
        trades.append({"side": side, "entry_i": i, "exit_i": exit_i, "entry": entry,
                       "stop": stop, "target": target, "r": r, "exit": why})
        if why == "SL":
            _note_stop(stops, entry, side)
        i = exit_i + 1
    return trades


def main() -> None:
    import bot
    import feeds

    data = bot.MT5Data()
    try:
        chart, htf = bot.load_frames(data, bot.SYMBOL, bot.TF, int(os.getenv("BOT_BARS", "20000")))
    finally:
        data.close()

    best = None
    print(f"{'tf':>4} {'touch':>5} {'стоп$':>6} {'tp':>4} {'сдел':>5} {'винр':>5} {'суммаR':>8}")
    for tf_name in ("240", "1D"):
        H = htf.get(tf_name)
        if H is None or len(H.c) < 300:
            continue
        for touches in (2, 3):
            for stop_pts in (3.0, 4.0, 6.0):
                zones = zones_for_chart(chart, H, touches=touches)
                tr = backtest(chart, zones, H, stop_pts=stop_pts)
                if not tr:
                    print(f"{tf_name:>4} {touches:>5} {stop_pts:>6.1f}    —       0")
                    continue
                rs = [t["r"] for t in tr]
                w = sum(1 for r in rs if r > 0)
                print(f"{tf_name:>4} {touches:>5} {stop_pts:>6.1f} {2.0:>4.1f} {len(tr):>5} "
                      f"{w / len(rs) * 100:>4.0f}% {sum(rs):>+8.2f}")
                if best is None or sum(rs) > best[3]:
                    best = (tf_name, touches, stop_pts, sum(rs), len(tr), w / len(rs) * 100)
    if best:
        print(f"\nлучшее: {best[0]} · касаний {best[1]} · стоп {best[2]}$ → "
              f"{best[3]:+.2f}R на {best[4]} сделках ({best[5]:.0f}%)")


if __name__ == "__main__":
    main()
