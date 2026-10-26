"""Временный бэктест: 15м сетап у зоны 4H + подтверждение на 1м (триггер-вход).

Идея (выбор пользователя): модель и зоны — 4H, сетап ловится на 15м (касание
границы зоны и закрытие обратно), но вход разрешается только когда на 1м
появляется подтверждение. Сравниваем с текущей боевой схемой (вход сразу на 15м).

Режимы триггера (TRIG_MODE):
  bos    — ждём 1м закрытие выше максимума 15м-бара сетапа (импульсный пробой);
  retest — ждём 1м свечу, которая вернулась к зоне и закрылась обратно
           (уровень удержан на ретесте), вход у самой зоны.

Окно ограничено историей M1 в MT5 (~45 дней).
"""
import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot
import feeds
import strategy as st
import range_v2 as r2
import ta_core as ta

DAYS = int(os.getenv("BT_DAYS", "30"))
START = float(os.getenv("PREVIEW_START", "5000"))
RISK_PCT = float(os.getenv("PREVIEW_RISK", "0.5"))
TP_R = float(os.getenv("RANGE_TP_R", "3.0"))
TRIG_WIN = int(os.getenv("TRIG_WIN", "30"))          # сколько 1м баров ждём подтверждение
MODE = os.getenv("TRIG_MODE", "bos").strip().lower()  # bos | retest
SYMBOLS = ["XAUUSD", "AUDUSD", "USDCAD", "GBPUSD", "US500",
           "EUSTX50", "UK100", "DE40", "JPN225"]


def load_chart(data, sym, tf, bars):
    """График без незакрытого бара (как в bot.load_frames)."""
    f = data.frame(sym, tf, bars)
    return st.Frame(f.t[:-1], f.o[:-1], f.h[:-1], f.l[:-1], f.c[:-1], f.v[:-1])


def _trigger(m1, start_j, side, ref, zone_price):
    """Индекс 1м бара-триггера и цена входа, либо (None, None).

    start_j — первый 1м бар, доступный после закрытия 15м-бара сетапа.
    """
    end_j = min(start_j + TRIG_WIN, len(m1.c))
    for j in range(start_j, end_j):
        if side == "LONG":
            if MODE == "bos" and m1.c[j] > ref:
                return j, float(m1.c[j])
            if MODE == "retest" and m1.l[j] <= zone_price and m1.c[j] > zone_price:
                return j, float(m1.c[j])
        else:
            if MODE == "bos" and m1.c[j] < ref:
                return j, float(m1.c[j])
            if MODE == "retest" and m1.h[j] >= zone_price and m1.c[j] < zone_price:
                return j, float(m1.c[j])
    return None, None


def run_symbol(data, sym, bars15, bars1, max_bars1):
    """Сделки по одному символу. Возвращает (baseline, trigger, skipped_no_trig)."""
    chart = load_chart(data, sym, "15", bars15)
    m1 = load_chart(data, sym, "1", bars1)

    span = float(chart.t[-1] - chart.t[0])
    zmin = feeds.TF_MINUTES.get(bot.RANGE_ZONE_TF.upper(), 240)
    H = data.frame(sym, bot.RANGE_ZONE_TF, int(span / (zmin * 60)) + 320)

    zones = r2.zones_for_chart(chart, H, touches=2, flips_keep=bot.RANGE_FLIP_KEEP)
    atr15 = ta.atr(chart.h, chart.l, chart.c, 14)
    er = ta.efficiency_ratio(chart.c)

    # --- базовая (боевая) схема: вход сразу на 15м ---
    base = r2.backtest(chart, zones, H, stop_atr=bot.RANGE_STOP_ATR,
                       max_stops=bot.RANGE_MAX_STOPS, tp_r=TP_R, max_r=TP_R + 2.0,
                       session=bot.RANGE_SESSION, max_bars=1440, smt=None, smt_look=0)
    base_out = []
    for t in base:
        if np.isnan(er[t["entry_i"]]) or er[t["entry_i"]] > bot.RANGE_ER_MAX:
            continue
        t = dict(t)
        t["symbol"] = sym
        t["t_in"] = float(chart.t[t["entry_i"]])
        t["t_out"] = float(chart.t[t["exit_i"]])
        base_out.append(t)

    # --- схема с триггером на 1м ---
    j_of = np.searchsorted(m1.t, chart.t, side="left")
    trig, no_trig = [], 0
    i, n = 1, len(chart.c)
    while i < n - 1:
        if not st.in_session(float(chart.t[i]), bot.RANGE_SESSION):
            i += 1
            continue
        if np.isnan(er[i]) or er[i] > bot.RANGE_ER_MAX:
            i += 1
            continue
        lv_list = zones[i]["levels"]
        sig = None
        for lv in lv_list:
            price = float(lv["price"])
            if lv["kind"] == "sup" and chart.l[i] <= price and chart.c[i] > price:
                sig = ("LONG", price)
                break
            if lv["kind"] == "res" and chart.h[i] >= price and chart.c[i] < price:
                sig = ("SHORT", price)
                break
        if not sig:
            i += 1
            continue

        side, zone_price = sig
        sgn = 1 if side == "LONG" else -1
        a_c = atr15[i]
        if np.isnan(a_c) or a_c <= 0:
            i += 1
            continue
        risk = float(a_c) * bot.RANGE_STOP_ATR
        if risk <= 0:
            i += 1
            continue

        # что делал 15м-бар сетапа в 1м-выражении
        j0 = int(j_of[i])
        j1 = int(j_of[i + 1]) if i + 1 < len(j_of) else len(m1.c)
        if j1 <= j0 or j1 >= len(m1.c):
            i += 1
            continue
        if side == "LONG":
            ref = float(np.max(m1.h[j0:j1]))
        else:
            ref = float(np.min(m1.l[j0:j1]))

        tj, entry = _trigger(m1, j1, side, ref, zone_price)
        if tj is None:
            no_trig += 1
            i += 1
            continue

        stop = entry - sgn * risk
        # стоп мог выбить на баре входа
        if (side == "LONG" and m1.l[tj] <= stop) or (side == "SHORT" and m1.h[tj] >= stop):
            r, exit_j, why = -1.0, tj, "SL-bar"
        else:
            target = r2.pick_target(lv_list, side, entry, risk, TP_R, max_r=TP_R + 2.0)
            exit_j = why = None
            for j in range(tj + 1, min(tj + 1 + max_bars1, len(m1.c))):
                hit_sl = m1.l[j] <= stop if side == "LONG" else m1.h[j] >= stop
                hit_tp = m1.h[j] >= target if side == "LONG" else m1.l[j] <= target
                if hit_sl:
                    exit_j, why = j, "SL"
                    break
                if hit_tp:
                    exit_j, why = j, "TP"
                    break
            if exit_j is None:
                exit_j, why = min(tj + max_bars1, len(m1.c) - 1), "TIME"
            px = stop if why == "SL" else (target if why == "TP" else float(m1.c[exit_j]))
            r = (px - entry) * sgn / risk

        trig.append({"symbol": sym, "side": side, "r": r, "exit": why,
                     "entry": entry, "stop": stop, "t_in": float(m1.t[tj]),
                     "t_out": float(m1.t[exit_j])})
        # следующий сетап ищем после выхода из сделки
        i = int(np.searchsorted(chart.t, m1.t[exit_j], side="right"))
        if i <= 0:
            i = 1

    return base_out, trig, no_trig


def curve(sel):
    bal, rows, day_stops, skipped = START, [], {}, 0
    for t in sel:
        day = dt.datetime.fromtimestamp(t["t_in"], dt.timezone.utc).strftime("%Y-%m-%d")
        if bot.MAX_DAILY_STOPS > 0 and day_stops.get(day, 0) >= bot.MAX_DAILY_STOPS:
            skipped += 1
            continue
        risk = bal * RISK_PCT / 100.0
        pnl = t["r"] * risk
        bal += pnl
        if t["r"] < 0:
            day_stops[day] = day_stops.get(day, 0) + 1
        rows.append((t, pnl, bal))
    return rows, skipped


def stats(name, rows, skipped, days):
    if not rows:
        print(f"  {name:>16}: сделок нет")
        return
    rs = [t["r"] for t, _, _ in rows]
    wins = sum(1 for r in rs if r > 0)
    bal = rows[-1][2]
    peak, dd = START, 0.0
    for _, _, b in rows:
        peak = max(peak, b)
        dd = min(dd, (b - peak) / peak * 100.0)
    print(f"  {name:>16}: сделок {len(rs):>4} ({len(rs)/max(days,1):.1f}/дн) · "
          f"прибыльных {wins} ({wins/len(rs)*100:.0f}%) · сумма {sum(rs):+.1f}R · "
          f"баланс {START:.0f}->{bal:.0f}$ ({(bal-START)/START*100:+.1f}%) · "
          f"макс.просадка {dd:.1f}%" + (f" · пропущено {skipped}" if skipped else ""))


def main():
    data = bot.MT5Data()
    res = {}
    try:
        bars15 = int(DAYS * 24 * 60 / 15) + 400
        bars1 = int(DAYS * 24 * 60 / 1) + 400
        loaded = {}
        for sym in SYMBOLS:
            try:
                loaded[sym] = load_chart(data, sym, "1", bars1)
            except Exception as e:
                print(f"  {sym}: нет данных 1м ({e})")
        if loaded:
            ref = max(float(c.t[-1]) for c in loaded.values())
            for s in [s for s, c in loaded.items() if ref - float(c.t[-1]) > 3 * 86400]:
                print(f"  {s}: устаревшие данные 1м — пропуск")
                loaded.pop(s)

        base_all, trig_all, no_trig_all, spans = [], [], 0, {}
        for sym in loaded:
            try:
                b, tg, nt = run_symbol(data, sym, bars15, bars1, 1440)
            except Exception as e:
                print(f"  {sym}: ошибка ({e})")
                continue
            base_all += b
            trig_all += tg
            no_trig_all += nt
            spans[sym] = (float(loaded[sym].t[0]), float(loaded[sym].t[-1]), len(loaded[sym].c))
            print(f"    {sym:<8} {len(loaded[sym].c):>6} 1м · "
                  f"{dt.datetime.utcfromtimestamp(spans[sym][0]):%d.%m.%y} - "
                  f"{dt.datetime.utcfromtimestamp(spans[sym][1]):%d.%m.%y}")
    finally:
        data.close()

    base_all.sort(key=lambda x: x["t_in"])
    trig_all.sort(key=lambda x: x["t_in"])
    ws = max(v[0] for v in spans.values())
    we = min(v[1] for v in spans.values())
    days = max((we - ws) / 86400.0, 1)

    print(f"\n--- общее окно M1 ---")
    print(f"окно: {dt.datetime.utcfromtimestamp(ws):%d.%m.%Y} - "
          f"{dt.datetime.utcfromtimestamp(we):%d.%m.%Y} ({days:.0f} дн.) · символов {len(spans)}")
    print(f"правила: риск {RISK_PCT}% · лимит стопов {bot.MAX_DAILY_STOPS}/день · "
          f"TP {TP_R}R · ER<={bot.RANGE_ER_MAX} · стоп {bot.RANGE_STOP_ATR} ATR(15м) · "
          f"триггер 1м «{MODE}», окно {TRIG_WIN} бар")

    for name, data_all in (("15м/4H (боевая)", base_all),
                           (f"триггер 1м «{MODE}»", trig_all)):
        sel = [t for t in data_all if t["t_in"] >= ws and t["t_out"] <= we]
        rows, skipped = curve(sel)
        stats(name, rows, skipped, days)
    print(f"  сетапов без 1м-подтверждения: {no_trig_all}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
