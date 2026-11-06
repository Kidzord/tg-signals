#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Аудит гейтов чек-листа KidzordFx на реальных данных MT5.

Задача узкая и проверяемая: воспроизвести условия чек-листа на истории и
посчитать, сколько баров проходило вход V1.2 (восемь «жёстких» пунктов) и
сколько из них таблица при этом показывала как «ЖДЁМ». Именно это было P0:
метка LONG и alert уходили при невыполненных r4/r9/r10/r11.

Это НЕ порт индикатора. Упрощения (одинаковые для обоих вариантов):
  * ручные уровни lv1..lv5 не задаются, IB/ON выключены (как по умолчанию);
  * триггер IFVG не реализован (пин-бар, поглощение, микро-BOS);
  * свип по N-баровому экстремуму, фрактальный вариант выключен;
  * доп. фильтры (VWAP/стек/MACD/RSI/дисконт) по умолчанию выключены,
    поэтому r11 в модели всегда истинен и отдельно не считается.

Прибыльность здесь не измеряется — для неё есть strategy-двойник: цифры R
зависят от модели выходов, а этот скрипт отвечает только на вопрос «какие
входы существовали и что о них говорила таблица».

Запуск (интерпретатор с MetaTrader5, см. pyfind.bat):

    export PYTHONUSERBASE="C:/Users/admin/AppData/Roaming/AutoClaw-oversea-official/toolchain/python"
    "$PY" tools/gate_audit.py --symbol XAUUSD --start 2025-01-01 --tf 15
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import ta_core as ta  # noqa: E402

TF_NAME = {
    "5": "TIMEFRAME_M5", "15": "TIMEFRAME_M15", "30": "TIMEFRAME_M30",
    "60": "TIMEFRAME_H1", "240": "TIMEFRAME_H4", "D": "TIMEFRAME_D1",
    "W": "TIMEFRAME_W1",
}
BIG = 999999.0


# ─────────────────────────────── данные ────────────────────────────────

def load(mt5, sym, tf, start, end):
    r = mt5.copy_rates_range(sym, getattr(mt5, TF_NAME[tf]), start, end)
    if r is None or len(r) == 0:
        raise SystemExit(f"нет данных {sym} {tf}: {mt5.last_error()}")
    return {k: np.asarray(r[k]) for k in ("time", "open", "high", "low", "close")}


def map_prev(t_chart, t_htf, vals):
    """Последний закрытый бар старшего ТФ — аналог security с [1], без репейнта."""
    idx = np.searchsorted(t_htf, t_chart, side="right") - 2
    out = np.full(len(t_chart), np.nan)
    ok = idx >= 0
    out[ok] = vals[idx[ok]]
    return out


def map_lagged(t_chart, t_htf, vals, lag):
    """Бар старшего ТФ, содержащий момент t, со сдвигом на lag баров."""
    idx = np.searchsorted(t_htf, t_chart, side="right") - 1 - lag
    out = np.full(len(t_chart), np.nan)
    ok = idx >= 0
    out[ok] = vals[idx[ok]]
    return out


def shift(a, n):
    out = np.full(len(a), np.nan)
    if 0 < n < len(a):
        out[n:] = a[: len(a) - n]
    elif n == 0:
        out[:] = a
    return out


def since(cond):
    out = np.full(len(cond), BIG)
    last = -1
    for i in range(len(cond)):
        if cond[i]:
            last = i
        if last >= 0:
            out[i] = i - last
    return out


# ─────────────────────────── подготовка данных ──────────────────────────

def prepare(d, p):
    c, h, l, o, t = d["c"], d["h"], d["l"], d["o"], d["t"]
    n = len(c)
    atr_c = ta.atr(h, l, c, 14)

    ema_t = map_prev(t, d["t_h1"], d["ema_h1"])
    atr_t = map_prev(t, d["t_h1"], d["atr_h1"])
    htf_ema = map_prev(t, d["t_h4"], d["ema_h4"])
    htf_close = map_prev(t, d["t_h4"], d["close_h4"])
    htf_atr = map_prev(t, d["t_h4"], d["atr_h4"])
    htf_past = map_lagged(t, d["t_h4"], d["ema_h4"], p["slope"] + 1)

    atr_d = map_prev(t, d["t_d1"], d["atr_d1"])
    d_ema = map_prev(t, d["t_d1"], d["ema_d1"])
    pdh = map_prev(t, d["t_d1"], d["high_d1"])
    pdl = map_prev(t, d["t_d1"], d["low_d1"])
    pwh = map_prev(t, d["t_w1"], d["high_w1"])
    pwl = map_prev(t, d["t_w1"], d["low_w1"])

    with np.errstate(invalid="ignore", divide="ignore"):
        slope = (htf_ema - htf_past) / htf_atr / p["slope"]
        er = np.abs(c - shift(c, p["chop"])) / ta.rolling_sum(
            np.abs(c - shift(c, 1)), p["chop"])
        rng_hi = ta.rolling_max(h, p["range"])
        rng_lo = ta.rolling_min(l, p["range"])
        rng_atr = (rng_hi - rng_lo) / atr_c
        zone = atr_t * 0.6
        tzone = atr_t * 0.15

    touch_l = (l <= ema_t + zone) & (c >= ema_t - zone)
    touch_s = (h >= ema_t - zone) & (c <= ema_t + zone)
    test_l = (l <= ema_t + tzone) & (c > ema_t)
    test_s = (h >= ema_t - tzone) & (c < ema_t)

    sw_ref_l = shift(ta.rolling_min(l, p["sweep"]), 1)
    sw_ref_s = shift(ta.rolling_max(h, p["sweep"]), 1)
    sweep_l = (l < sw_ref_l) & (c > sw_ref_l)
    sweep_s = (h > sw_ref_s) & (c < sw_ref_s)

    frac_hi = ta.pivot_high(h, p["frac_l"], p["frac_r"])
    frac_lo = ta.pivot_low(l, p["frac_l"], p["frac_r"])
    last_frac_hi = ta.valuewhen(~np.isnan(frac_hi), np.nan_to_num(frac_hi))
    last_frac_lo = ta.valuewhen(~np.isnan(frac_lo), np.nan_to_num(frac_lo))
    bos_ref_l = np.fmax(last_frac_hi, shift(ta.rolling_max(h, p["bos"]), 1))
    bos_ref_s = np.fmin(last_frac_lo, shift(ta.rolling_min(l, p["bos"]), 1))
    bos_l = c > bos_ref_l
    bos_s = c < bos_ref_s

    bull_fvg = l > shift(h, 2)
    bear_fvg = h < shift(l, 2)
    fvg_bot_l = ta.valuewhen(bull_fvg, shift(h, 2))
    fvg_top_l = ta.valuewhen(bull_fvg, l)
    fvg_top_s = ta.valuewhen(bear_fvg, shift(l, 2))
    fvg_bot_s = ta.valuewhen(bear_fvg, h)
    prox = atr_c * 0.5
    near_bull = (c >= fvg_bot_l - prox) & (c <= fvg_top_l + prox)
    near_bear = (c <= fvg_top_s + prox) & (c >= fvg_bot_s - prox)
    fvg_l = (since(bull_fvg) <= p["fac"]) & near_bull
    fvg_s = (since(bear_fvg) <= p["fac"]) & near_bear

    return dict(
        n=n, o=o, h=h, l=l, c=c, t=t, atr_c=atr_c, ema_t=ema_t, atr_t=atr_t,
        htf_ema=htf_ema, htf_close=htf_close, atr_d=atr_d, d_ema=d_ema,
        slope=slope, er=er, rng_atr=rng_atr,
        touch_l=touch_l, touch_s=touch_s, test_l=test_l, test_s=test_s,
        sweep_l=sweep_l, sweep_s=sweep_s, bos_l=bos_l, bos_s=bos_s,
        fvg_l=fvg_l, fvg_s=fvg_s, bear_fvg=bear_fvg,
        pdh=pdh, pdl=pdl, pwh=pwh, pwl=pwl,
        swing_lo=ta.rolling_min(l, 10), swing_hi=ta.rolling_max(h, 10),
        manip_lo=ta.valuewhen(sweep_l, l), manip_hi=ta.valuewhen(sweep_s, h),
        liq_hi=shift(ta.rolling_max(h, 60), 1),
        liq_lo=shift(ta.rolling_min(l, 60), 1),
        mic_hi5=shift(ta.rolling_max(h, 5), 1),
        mic_lo5=shift(ta.rolling_min(l, 5), 1),
        ag_sweep_l=since(sweep_l), ag_sweep_s=since(sweep_s),
        ag_touch_l=since(touch_l), ag_touch_s=since(touch_s),
        ag_test_l=since(test_l), ag_test_s=since(test_s),
        ag_bos_l=since(bos_l), ag_bos_s=since(bos_s),
    )


# ──────────────────────────────── аудит ────────────────────────────────

def core_gates(g, p, i, variant, lv):
    """Один бар: считает r1..r12 и состояние сетапа. Меняет lv и sticky-состояния."""
    c, h, l, _ = g["c"], g["h"], g["l"], g["t"]
    atr, ema_t = g["atr_c"][i], g["ema_t"][i]

    # уровни: касание и отбой
    setup_l = setup_s = None
    for j in range(4):
        px = lv["px"][j][i]
        if np.isnan(px):
            continue
        kind = lv["kind"][j]
        tol = atr * 0.15
        band = h[i] >= px - tol and l[i] <= px + tol
        for side in (0, 1):
            if not band:
                lv["inband"][j][side] = False
                continue
            if variant == "v12":
                lv["inband"][j][side] = True
                if side == 0 and h[i] >= px - tol:
                    lv["touch"][j][side] = i
                    if c[i] < px:
                        lv["reject"][j][side] = i
                if side == 1 and l[i] <= px + tol:
                    lv["touch"][j][side] = i
                    if c[i] > px:
                        lv["reject"][j][side] = i
            else:
                if not lv["inband"][j][side]:
                    lv["inband"][j][side] = True
                    lv["touch"][j][side] = i
                    lv["reject"][j][side] = -1
                if side == 0 and c[i] < px:
                    lv["reject"][j][side] = i
                if side == 1 and c[i] > px:
                    lv["reject"][j][side] = i
        if kind >= 0 and c[i] < px:
            ts, rs = lv["touch"][j][0], lv["reject"][j][0]
            if ts >= 0 and i - ts <= 60 and rs >= 0 and i - rs <= 60:
                if setup_s is None or rs > setup_s[1]:
                    setup_s = (px, rs, ts)
        if kind <= 0 and c[i] > px:
            ts, rs = lv["touch"][j][1], lv["reject"][j][1]
            if ts >= 0 and i - ts <= 60 and rs >= 0 and i - rs <= 60:
                if setup_l is None or rs > setup_l[1]:
                    setup_l = (px, rs, ts)

    ema_touch_l = g["ag_touch_l"][i] <= 96
    ema_touch_s = g["ag_touch_s"][i] <= 96
    ema_test_l = g["ag_test_l"][i] <= 20
    ema_test_s = g["ag_test_s"][i] <= 20
    if variant == "v13":
        ema_test_l = ema_test_l and g["ag_test_l"][i] <= g["ag_touch_l"][i]
        ema_test_s = ema_test_s and g["ag_test_s"][i] <= g["ag_touch_s"][i]

    use_lv_l = setup_l is not None and (
        not (ema_touch_l and ema_test_l) or (i - setup_l[1]) <= g["ag_test_l"][i])
    use_lv_s = setup_s is not None and (
        not (ema_touch_s and ema_test_s) or (i - setup_s[1]) <= g["ag_test_s"][i])

    if variant == "v12":
        r3_l = (setup_l is not None and ema_touch_l and ema_test_l) or use_lv_l
        r5_l = use_lv_l or (setup_l is not None and ema_touch_l and ema_test_l)
        r3_s = (setup_s is not None and ema_touch_s and ema_test_s) or use_lv_s
        r5_s = use_lv_s or (setup_s is not None and ema_touch_s and ema_test_s)
    else:
        r3_l = use_lv_l or ema_touch_l
        r5_l = use_lv_l or ema_test_l
        r3_s = use_lv_s or ema_touch_s
        r5_s = use_lv_s or ema_test_s

    # контекст и расширение
    ctx_l = g["htf_close"][i] >= g["htf_ema"][i]
    day_up = c[i] > g["d_ema"][i]
    ext = abs(c[i] - ema_t) / atr
    ext_ok = ext <= p["ext_max"]
    r2_l = ctx_l and day_up and ext_ok
    r2_s = (not ctx_l) and (not day_up) and ext_ok

    # триггер
    rng = h[i] - l[i]
    o_i = g["o"][i]
    body = abs(c[i] - o_i)
    lower_w = min(o_i, c[i]) - l[i]
    upper_w = h[i] - max(o_i, c[i])
    big = rng >= atr * 0.4
    pin_l = lower_w >= 1.5 * max(body, 1e-9) and c[i] > (h[i] + l[i]) / 2
    pin_s = upper_w >= 1.5 * max(body, 1e-9) and c[i] < (h[i] + l[i]) / 2
    eng_l = c[i] > o_i and c[i - 1] < g["o"][i - 1] and c[i] >= g["o"][i - 1] and o_i <= c[i - 1]
    eng_s = c[i] < o_i and c[i - 1] > g["o"][i - 1] and c[i] <= g["o"][i - 1] and o_i >= c[i - 1]
    mic_l = not np.isnan(g["mic_hi5"][i]) and c[i] > g["mic_hi5"][i]
    mic_s = not np.isnan(g["mic_lo5"][i]) and c[i] < g["mic_lo5"][i]
    ref_l = setup_l[0] if use_lv_l else ema_t
    ref_s = setup_s[0] if use_lv_s else ema_t
    r6_l = (((pin_l or eng_l) and big) or mic_l) and c[i] > ref_l
    r6_s = (((pin_s or eng_s) and big) or mic_s) and c[i] < ref_s

    # риск
    manip_l = g["manip_lo"][i]
    base_l = manip_l if (g["ag_sweep_l"][i] <= 20 and not np.isnan(manip_l)
                         and manip_l < c[i]) else g["swing_lo"][i]
    sl_l = base_l - atr * 0.2
    manip_h = g["manip_hi"][i]
    base_s = manip_h if (g["ag_sweep_s"][i] <= 20 and not np.isnan(manip_h)
                         and manip_h > c[i]) else g["swing_hi"][i]
    sl_s = base_s + atr * 0.2
    risk_l, risk_s = c[i] - sl_l, sl_s - c[i]
    r7_l = 0 <= risk_l / atr <= 4
    r7_s = 0 <= risk_s / atr <= 4

    # цель: ближайший уровень или свинг с RR >= 1.5
    def target_long():
        best = None
        for px in (lv["px"][0][i], lv["px"][1][i], lv["px"][2][i], lv["px"][3][i],
                   g["liq_hi"][i]):
            if px is None or np.isnan(px) or px <= c[i] or risk_l <= 0:
                continue
            pct = (px - c[i]) / c[i] * 100
            if pct < 0.15 or pct > 5.0:
                continue
            if pct / (risk_l / c[i] * 100) < 1.5:
                continue
            if best is None or px < best:
                best = px
        return best

    def target_short():
        best = None
        for px in (lv["px"][0][i], lv["px"][1][i], lv["px"][2][i], lv["px"][3][i],
                   g["liq_lo"][i]):
            if px is None or np.isnan(px) or px >= c[i] or risk_s <= 0:
                continue
            pct = (c[i] - px) / c[i] * 100
            if pct < 0.15 or pct > 5.0:
                continue
            if pct / (risk_s / c[i] * 100) < 1.5:
                continue
            if best is None or px > best:
                best = px
        return best

    r8_l = target_long() is not None
    r8_s = target_short() is not None

    # флет / диапазон / SMC / окно
    r9 = bool(g["er"][i] >= 0.2 and 2.0 <= (g["rng_atr"][i] if not np.isnan(g["rng_atr"][i]) else -1) <= 20.0)
    sweep_ok_l = g["ag_sweep_l"][i] <= 30
    sweep_ok_s = g["ag_sweep_s"][i] <= 30
    st_l = g["ag_bos_l"][i] <= 30
    st_s = g["ag_bos_s"][i] <= 30
    ob_l, ob_s = lv["ob_l"], lv["ob_s"]
    r10_l = (int(sweep_ok_l) + int(st_l) + int(g["fvg_l"][i]) + int(ob_l)) >= 2
    r10_s = (int(sweep_ok_s) + int(st_s) + int(g["fvg_s"][i]) + int(ob_s)) >= 2
    minutes = (int(g["t"][i]) // 60) % 1440
    utc_m = (minutes - 180) % 1440          # GMT+3 -> UTC
    r12 = 13 * 60 + 30 <= utc_m < 20 * 60   # 16:30-23:00 GMT+3

    core_l = ctx_l and r2_l and r3_l and r5_l and r6_l and r7_l and r8_l and r12
    core_s = (not ctx_l) and r2_s and r3_s and r5_s and r6_s and r7_s and r8_s and r12
    return dict(core_l=core_l, core_s=core_s,
                r4_l=bool(g["slope"][i] >= 0.01), r4_s=bool(g["slope"][i] <= -0.01),
                r9=r9, r10_l=bool(r10_l), r10_s=bool(r10_s),
                r3_l=r3_l, r3_s=r3_s, r5_l=r5_l, r5_s=r5_s)


def audit(d, g, variant, p):
    lv = dict(px=[g["pdh"], g["pdl"], g["pwh"], g["pwl"]],
              kind=[1, -1, 1, -1],
              touch=[[-1, -1] for _ in range(4)],
              reject=[[-1, -1] for _ in range(4)],
              inband=[[False, False] for _ in range(4)],
              ob_l=False, ob_s=False)
    stats = dict(bars=0, core=0, core_long=0, core_short=0,
                 miss_r4=0, miss_r9=0, miss_r10=0,
                 all12=0, level_touch_bars=0, touch_events=0)
    obs = []
    prev_touch = [[False, False] for _ in range(4)]

    for i in range(220, g["n"] - 1):
        if np.isnan(g["atr_c"][i]) or g["atr_c"][i] <= 0:
            continue
        if np.isnan(g["htf_ema"][i]) or np.isnan(g["htf_close"][i]) or np.isnan(g["ema_t"][i]):
            continue
        stats["bars"] += 1

        # order blocks (упрощённо, как в индикаторе)
        if g["c"][i] > g["o"][i] and g["c"][i] > g["h"][i - 1] and \
                (g["c"][i] - g["o"][i]) >= g["atr_c"][i] * p["ob_impulse"]:
            k = i - 1
            limit = max(0, i - p["ob_search"])
            while k > limit and not (g["c"][k] < g["o"][k]):
                k -= 1
            if g["c"][k] < g["o"][k]:
                obs.append((g["h"][k], g["l"][k], 1, k))
        if g["c"][i] < g["o"][i] and g["c"][i] < g["l"][i - 1] and \
                (g["o"][i] - g["c"][i]) >= g["atr_c"][i] * p["ob_impulse"]:
            k = i - 1
            limit = max(0, i - p["ob_search"])
            while k > limit and not (g["c"][k] > g["o"][k]):
                k -= 1
            if g["c"][k] > g["o"][k]:
                obs.append((g["h"][k], g["l"][k], -1, k))
        obs = [x for x in obs if i - x[3] <= 60 and
               (g["c"][i] < x[0] if x[2] == 1 else g["c"][i] > x[1])]
        obs = obs[-6:]
        tol_ob = g["atr_c"][i] * 0.15
        lv["ob_l"] = any(x[2] == 1 and x[1] - tol_ob <= g["c"][i] <= x[0] + tol_ob for x in obs)
        lv["ob_s"] = any(x[2] == -1 and x[1] - tol_ob <= g["c"][i] <= x[0] + tol_ob for x in obs)

        r = core_gates(g, p, i, variant, lv)

        # сколько раз цена входила в полосу уровня (визиты)
        for j, arr in enumerate(lv["px"]):
            px = arr[i]
            if np.isnan(px):
                prev_touch[j] = [False, False]
                continue
            tol = g["atr_c"][i] * 0.15
            inb = g["h"][i] >= px - tol and g["l"][i] <= px + tol
            for side in (0, 1):
                if inb and not prev_touch[j][side]:
                    stats["touch_events"] += 1
                prev_touch[j][side] = inb

        if r["core_l"] or r["core_s"]:
            stats["core"] += 1
            stats["core_long"] += int(r["core_l"])
            stats["core_short"] += int(r["core_s"])
            miss4 = not (r["r4_l"] if r["core_l"] else r["r4_s"])
            miss9 = not r["r9"]
            miss10 = not (r["r10_l"] if r["core_l"] else r["r10_s"])
            stats["miss_r4"] += int(miss4)
            stats["miss_r9"] += int(miss9)
            stats["miss_r10"] += int(miss10)
            if not (miss4 or miss9 or miss10):
                stats["all12"] += 1
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--tf", default="15")
    ap.add_argument("--htf", default="240")
    ap.add_argument("--touch", default="60")
    ap.add_argument("--slope", type=int, default=20)
    ap.add_argument("--chop", type=int, default=100)
    ap.add_argument("--range", type=int, default=30)
    ap.add_argument("--sweep", type=int, default=20)
    ap.add_argument("--bos", type=int, default=20)
    ap.add_argument("--frac-l", type=int, default=2)
    ap.add_argument("--frac-r", type=int, default=2)
    ap.add_argument("--fac", type=int, default=30)
    ap.add_argument("--ext-max", type=float, default=3.0)
    ap.add_argument("--ob-impulse", type=float, default=1.0)
    ap.add_argument("--ob-search", type=int, default=5)
    p = ap.parse_args()
    p = vars(p)

    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit(f"MT5 init failed: {mt5.last_error()}")
    mt5.symbol_select(p["symbol"], True)
    start = dt.datetime.strptime(p["start"], "%Y-%m-%d")
    end = dt.datetime.now() + dt.timedelta(days=1)

    raw = load(mt5, p["symbol"], p["tf"], start, end)
    d = {"c": raw["close"].astype(float), "h": raw["high"].astype(float),
         "l": raw["low"].astype(float), "o": raw["open"].astype(float),
         "t": raw["time"].astype(np.int64)}
    for tf, key in ((p["htf"], "h4"), (p["touch"], "h1"), ("D", "d1"), ("W", "w1")):
        b = load(mt5, p["symbol"], tf, start, end)
        cc, hh, ll = b["close"].astype(float), b["high"].astype(float), b["low"].astype(float)
        d[f"t_{key}"] = b["time"].astype(np.int64)
        d[f"high_{key}"] = hh
        d[f"low_{key}"] = ll
        if key in ("h4", "h1"):
            d[f"close_{key}"] = cc
            d[f"ema_{key}"] = ta.ema(cc, 200)
            d[f"atr_{key}"] = ta.atr(hh, ll, cc, 14)
        if key == "d1":
            d["ema_d1"] = ta.ema(cc, 200)
            d["atr_d1"] = ta.atr(hh, ll, cc, 14)

    g = prepare(d, p)
    v12 = audit(d, g, "v12", p)
    v13 = audit(d, g, "v13", p)

    def pct(a, b):
        return f"{a / b * 100:.1f}%" if b else "—"

    print(f"\n{p['symbol']} · {p['tf']}m · с {p['start']} · баров {v12['bars']}")
    print(f"контекст {p['htf']} · касание {p['touch']} · окно 16:30-23:00 GMT+3\n")
    print(f"{'':34s}{'V1.2':>10s}{'V1.3':>10s}")
    print(f"{'баров с входом (core-8)':34s}{v12['core']:>10d}{v13['core']:>10d}")
    print(f"{'  из них long / short':34s}{str(v12['core_long']) + '/' + str(v12['core_short']):>10s}"
          f"{str(v13['core_long']) + '/' + str(v13['core_short']):>10s}")
    print(f"{'входов после всех 12':34s}{v12['all12']:>10d}{v13['all12']:>10d}")
    print(f"{'проходят новый гейт':34s}{pct(v12['all12'], v12['core']):>10s}"
          f"{pct(v13['all12'], v13['core']):>10s}")
    print("\nиз входов V1.2 таблица говорила «ЖДЁМ» (доля от core-8):")
    print(f"  наклон r4        {v12['miss_r4']:6d}  {pct(v12['miss_r4'], v12['core'])}")
    print(f"  ER/диапазон r9   {v12['miss_r9']:6d}  {pct(v12['miss_r9'], v12['core'])}")
    print(f"  SMC-факторы r10  {v12['miss_r10']:6d}  {pct(v12['miss_r10'], v12['core'])}")
    print("\nвходы в полосу уровня (визитов):")
    print(f"  V1.2 (касание обновляется каждый бар) {v12['touch_events']}")
    print(f"  V1.3 (один визит = одно касание)      {v13['touch_events']}")
    mt5.shutdown()


if __name__ == "__main__":
    main()
