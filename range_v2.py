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


def side_ok(side: str, mode: str) -> bool:
    """Разрешена ли сторона сделки: both / short / long."""
    mode = (mode or "both").strip().lower()
    if mode in ("short", "шорт", "sell"):
        return side.upper() == "SHORT"
    if mode in ("long", "лонг", "buy"):
        return side.upper() == "LONG"
    return True


def trend_ok(htf_frame: st.Frame, ts: float, side: str, mode: str) -> bool:
    """Совпадает ли сделка с направлением H4 EMA200 (последний закрытый бар).

    mode: off — без фильтра; with — только по тренду; against — только против.
    Нет EMA (мало истории) — пропускаем сделку (fail-open, как в live).
    """
    mode = (mode or "off").strip().lower()
    if mode not in ("with", "against"):
        return True
    if htf_frame is None or len(htf_frame.c) < 210:
        return True
    ema = ta.ema(htf_frame.c, 200)
    j = int(np.searchsorted(htf_frame.t, ts, side="right")) - 2
    if j < 0 or j >= len(ema) or np.isnan(ema[j]):
        return True
    up = float(htf_frame.c[j]) > float(ema[j])
    long_ = side.upper() == "LONG"
    if mode == "with":
        return up if long_ else not up
    return (not up) if long_ else up


def backtest(chart: st.Frame, zones: list[dict], htf_frame: st.Frame,
             stop_pts: float = 4.0, tp_r: float = 2.0, max_bars: int = 120,
             min_rr: float = 1.0, stop_mode: str = "points", swing_len: int = 12,
             stop_buf: float = 0.0, max_stop_pts: float = 20.0,
             max_stops: int = 2, stop_atr: float = 0.0, max_r: float = 4.0,
             session: str = "", smt=None, smt_look: int = 0,
             entry_model: str = "next_open", er_max: float = 0.0,
             side_allow: str = "both", limit_bars: int = 8,
             stats: dict | None = None,
             be_after_r: float = 0.0,
             trend_filter: str = "off",
             atr_ratio_min: float = 0.0, atr_ratio_max: float = 0.0,
             confirm_atr: float = 0.0,
             trail_after_r: float = 0.0, trail_k: float = 2.0,
             zone_min_age: int = 0, zone_max_age: int = 0,
             d1_tol: float = 0.0) -> list[dict]:
    """entry_model:
      next_open    — вход по рынку на открытии следующего бара (реалистично);
      limit        — лимитка по цене уровня исполняется на баре сигнала (старая
                     оптимистичная модель, для сравнения);
      limit_retest — лимитка по цене уровня, активная limit_bars баров ПОСЛЕ
                     сигнала; заполняется только если цена вернулась к уровню.
                     stats (если передан) получает signals/fills/cancelled.
    er_max > 0 — фильтр Kaufman ER до входа, как в живом боте.

    Доп. фильтры (0/off — выключены): be_after_r — перевод стопа в БУ после
    N R; trend_filter — with/against направления H4 EMA200; atr_ratio_min/max —
    коридор ATR14/ATR100; confirm_atr — закрытие за границей зоны на N ATR;
    trail_after_r/trail_k — трейлинг по закрытию на K ATR после N R;
    zone_min_age/zone_max_age — возраст зоны в H4-барах;
    d1_tol — уровень зоны должен быть в N ATR от PDH/PDL прошлого дня."""
    n = len(chart.c)
    atr_h = ta.atr(htf_frame.h, htf_frame.l, htf_frame.c, 14)
    idx = np.searchsorted(htf_frame.t, chart.t, side="right") - 1

    atr_c = ta.atr(chart.h, chart.l, chart.c, 14)
    er = ta.efficiency_ratio(chart.c) if er_max > 0 else None
    atr_slow = ta.atr(chart.h, chart.l, chart.c, 100) \
        if (atr_ratio_min > 0 or atr_ratio_max > 0) else None
    trend_up = trend_dn = None
    if trend_filter != "off":
        ema_h = ta.ema(htf_frame.c, 200)
        # последний ЗАКРЫТЫЙ H4: -2, потому что htf_frame содержит текущий бар
        jh = np.searchsorted(htf_frame.t, chart.t, side="right") - 2
        trend_up = np.zeros(n, dtype=bool)
        trend_dn = np.zeros(n, dtype=bool)
        for k in range(n):
            j = int(jh[k])
            if 0 <= j < len(ema_h) and not np.isnan(ema_h[j]):
                trend_up[k] = htf_frame.c[j] > ema_h[j]
                trend_dn[k] = htf_frame.c[j] < ema_h[j]
    pdh = pdl = None
    if d1_tol > 0:
        # предыдущий ПОЛНЫЙ день по H4-барам
        day_hi: dict = {}
        day_lo: dict = {}
        order: list = []
        for k in range(len(htf_frame.t)):
            d = dt.datetime.fromtimestamp(float(htf_frame.t[k]), dt.timezone.utc).date()
            if d not in day_hi:
                day_hi[d] = float(htf_frame.h[k])
                day_lo[d] = float(htf_frame.l[k])
                order.append(d)
            else:
                day_hi[d] = max(day_hi[d], float(htf_frame.h[k]))
                day_lo[d] = min(day_lo[d], float(htf_frame.l[k]))
        prev = {}
        for pos, d in enumerate(order):
            if pos > 0:
                pd = order[pos - 1]
                prev[d] = (day_hi[pd], day_lo[pd])
        pdh = np.full(n, np.nan)
        pdl = np.full(n, np.nan)
        for i in range(n):
            d = dt.datetime.fromtimestamp(float(chart.t[i]), dt.timezone.utc).date()
            if d in prev:
                pdh[i], pdl[i] = prev[d]
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
        if atr_slow is not None and not np.isnan(atr_slow[i]) and atr_slow[i] > 0:
            ratio = atr_c[i] / atr_slow[i]
            if (atr_ratio_min > 0 and ratio < atr_ratio_min) or \
                    (atr_ratio_max > 0 and ratio > atr_ratio_max):
                i += 1
                continue
        conf = confirm_atr * (atr_c[i] if not np.isnan(atr_c[i]) else 0.0)
        lv_list = zones[i]["levels"]
        present = {(round(float(x["price"]), 2), x["kind"]) for x in lv_list}
        for k in list(stops):
            if k not in present:
                stops.pop(k, None)
        sig = None
        for lv in lv_list:
            price = float(lv["price"])
            if pdh is not None and not np.isnan(pdh[i]):
                tol = d1_tol * (atr_c[i] if not np.isnan(atr_c[i]) else 0.0)
                if abs(price - pdh[i]) > tol and abs(price - pdl[i]) > tol:
                    continue
            if lv.get("born") is not None:
                age = int(zones[i].get("bar", 0)) - int(lv["born"])
                if (zone_min_age > 0 and age < zone_min_age) or \
                        (zone_max_age > 0 and age > zone_max_age):
                    continue
            if max_stops > 0 and stops.get((round(price, 2), lv["kind"]), 0) >= max_stops:
                continue
            if lv["kind"] == "sup" and chart.l[i] <= price and chart.c[i] > price + conf:
                sig = ("LONG", price)
                break
            if lv["kind"] == "res" and chart.h[i] >= price and chart.c[i] < price - conf:
                sig = ("SHORT", price)
                break
        if not sig:
            i += 1
            continue

        side, level_px = sig
        sgn = 1 if side == "LONG" else -1
        if not side_ok(side, side_allow):
            i += 1
            continue
        if trend_filter == "with" and (
                (side == "LONG" and not trend_up[i]) or (side == "SHORT" and not trend_dn[i])):
            i += 1
            continue
        if trend_filter == "against" and (
                (side == "LONG" and not trend_dn[i]) or (side == "SHORT" and not trend_up[i])):
            i += 1
            continue

        # SMT-подтверждение входа (парный инструмент не снял тот же экстремум)
        if not smt_ok(chart, smt, i, side, smt_look):
            i += 1
            continue
        if stats is not None:
            stats["signals"] = stats.get("signals", 0) + 1

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

        if entry_model == "limit_retest":
            # лимитка живёт limit_bars баров после сигнала; бар сигнала не
            # считается — на его закрытии мы только узнали о сетапе
            end = min(i + limit_bars, n - 1)
            fill_j = fill_px = None
            for j in range(i + 1, end + 1):
                if side == "LONG" and chart.l[j] <= level_px:
                    fill_j, fill_px = j, min(float(level_px), float(chart.o[j]))
                    break
                if side == "SHORT" and chart.h[j] >= level_px:
                    fill_j, fill_px = j, max(float(level_px), float(chart.o[j]))
                    break
            if fill_j is None:
                if stats is not None:
                    stats["cancelled"] = stats.get("cancelled", 0) + 1
                i = max(i + 1, end)      # не плодим сигналы, пока висит лимитка
                continue
            if stats is not None:
                stats["fills"] = stats.get("fills", 0) + 1

            entry = fill_px
            stop = entry - sgn * risk
            target = pick_target(zones[i]["levels"], side, entry, risk, tp_r, min_rr, max_r)

            exit_i = exit_price = why = None
            be_moved = False
            trail_on = False
            # на баре заполнения проверяем только стоп: путь внутри бара
            # неизвестен, считать цель было бы оптимистично
            if (side == "LONG" and chart.l[fill_j] <= stop) or \
                    (side == "SHORT" and chart.h[fill_j] >= stop):
                exit_i, exit_price, why = fill_j, stop, "SL"
            else:
                for j in range(fill_j + 1, min(fill_j + max_bars, n)):
                    hit_sl = chart.l[j] <= stop if side == "LONG" else chart.h[j] >= stop
                    hit_tp = chart.h[j] >= target if side == "LONG" else chart.l[j] <= target
                    if hit_sl:
                        exit_i, exit_price, why = j, stop, "SL"
                        break
                    if hit_tp:
                        exit_i, exit_price, why = j, target, "TP"
                        break
                    if be_after_r > 0 and not be_moved:
                        trig = entry + sgn * risk * be_after_r
                        if (side == "LONG" and chart.h[j] >= trig) or \
                                (side == "SHORT" and chart.l[j] <= trig):
                            stop, be_moved = entry, True
                    if trail_after_r > 0 and not np.isnan(atr_c[j]):
                        trig = entry + sgn * risk * trail_after_r
                        if not trail_on and ((side == "LONG" and chart.h[j] >= trig) or
                                             (side == "SHORT" and chart.l[j] <= trig)):
                            trail_on = True
                        if trail_on:
                            tr = float(chart.c[j]) - sgn * trail_k * float(atr_c[j])
                            stop = max(stop, tr) if side == "LONG" else min(stop, tr)
            if exit_i is None:
                j = min(fill_j + max_bars, n - 1)
                exit_i, exit_price, why = j, float(chart.c[j]), "TIME"
            r = (exit_price - entry) * sgn / risk
            trades.append({"side": side, "entry_i": fill_j, "exit_i": exit_i,
                           "entry": entry, "stop": stop, "target": target, "risk": risk,
                           "r": r, "exit": why, "limit": True})
            if why == "SL":
                _note_stop(stops, entry, side)
            i = exit_i + 1
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
                           "stop": stop, "target": target, "risk": risk,
                           "r": r, "exit": "SL-bar"})
            _note_stop(stops, entry, side)
            i += 1
            continue

        exit_i = exit_price = why = None
        be_moved = False
        trail_on = False
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
            if be_after_r > 0 and not be_moved:
                trig = entry + sgn * risk * be_after_r
                if (side == "LONG" and chart.h[j] >= trig) or \
                        (side == "SHORT" and chart.l[j] <= trig):
                    stop, be_moved = entry, True
            if trail_after_r > 0 and not np.isnan(atr_c[j]):
                trig = entry + sgn * risk * trail_after_r
                if not trail_on and ((side == "LONG" and chart.h[j] >= trig) or
                                     (side == "SHORT" and chart.l[j] <= trig)):
                    trail_on = True
                if trail_on:
                    tr = float(chart.c[j]) - sgn * trail_k * float(atr_c[j])
                    stop = max(stop, tr) if side == "LONG" else min(stop, tr)
        if exit_i is None:
            j = min(i + max_bars, n - 1)
            exit_i, exit_price, why = j, float(chart.c[j]), "TIME"

        r = (exit_price - entry) * sgn / risk
        trades.append({"side": side, "entry_i": i, "exit_i": exit_i, "entry": entry,
                       "stop": stop, "target": target, "risk": risk,
                       "r": r, "exit": why})
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
