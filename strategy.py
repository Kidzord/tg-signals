"""Перенос логики KidzordFx V1 (Pine) на Python.

Считаем все условия сразу массивами по всем барам: это даёт и сигнал на последнем
баре для бота, и историю сигналов для сверки с графиком.

Имена переменных повторяют индикатор, чтобы правки можно было сверять построчно.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import numpy as np

import ta_core as ta
from feeds import TF_MINUTES

NAN = float("nan")
BIG = 999999.0


@dataclass
class Params:
    """Значения по умолчанию = дефолты индикатора."""
    # таймфреймы
    htf_tf: str = "240"
    touch_tf: str = "60"
    htf_ema_len: int = 200
    ltf_ema_len: int = 200
    side_allow: str = "Обе"
    # контекст
    slope_len: int = 20
    min_slope: float = 0.2
    use_day_ctx: bool = True
    tol_htf: float = 0.6
    mem_hours: int = 24
    chop_len: int = 100
    max_cross: int = 6
    # триггер
    tol_ltf: float = 0.15
    test_look: int = 20
    trig_look: int = 5
    pin_mult: float = 1.5
    use_pin: bool = True
    use_engulf: bool = True
    use_micro: bool = True
    trig_bos_len: int = 5
    use_ifvg: bool = True
    ifvg_look: int = 60
    ifvg_tol: float = 0.10
    min_range_k: float = 0.40
    # риск и цель
    swing_len: int = 10
    stop_buf: float = 0.20
    stop_mode: str = "За манипуляцией"
    manip_look: int = 20
    risk_atr_mode: bool = True
    risk_min_atr: float = 0.15
    risk_max_atr: float = 2.50
    risk_min: float = 0.05
    risk_max: float = 1.50
    liq_len: int = 60
    tgt_min: float = 0.15
    tgt_max: float = 5.00
    min_rr: float = 1.5
    use_day_bud: bool = True
    day_budget_k: float = 1.0
    # уровни
    lv_mode: str = "Уровни + EMA200"
    lv: tuple = (0.0, 0.0, 0.0, 0.0, 0.0)
    lv_persist: int = 6
    lv_tol: float = 0.15
    lv_look: int = 60
    use_auto: bool = True
    use_ib: bool = False
    # SMC-факторы
    sweep_len: int = 20
    use_frac_sweep: bool = True
    frac_l: int = 2
    frac_r: int = 2
    bos_len: int = 20
    fvg_look: int = 30
    range_len: int = 30
    range_min_p: float = 0.30
    range_max_p: float = 6.00
    fac_look: int = 30
    need_fac: int = 3
    # доп. фильтры
    use_vwap: bool = False
    use_stack: bool = False
    use_macd: bool = False
    use_rsi: bool = False
    ema_f1: int = 9
    ema_f2: int = 21
    ema_f3: int = 50
    macd_fast: int = 12
    macd_slow: int = 26
    macd_sig: int = 9
    rsi_len: int = 14
    rsi_ob: float = 70.0
    rsi_os: float = 30.0
    # режим проверки
    mode_soft: bool = True
    soft_need: int = 2
    # сделка
    tp1_r: float = 1.0
    tp2_r: float = 2.0
    tp3_r: float = 3.0
    p1_pct: float = 40.0
    p2_pct: float = 30.0
    be_after: int = 1
    max_bars: int = 120
    sweep_delay: int = 1
    use_sess: bool = False
    sess_in: str = "1630-2300"
    tz_name: str = "Europe/Moscow"


@dataclass
class Frame:
    """OHLC одного таймфрейма. t — epoch-секунды (UTC)."""
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray


def _nz(x, default=0.0):
    return np.where(np.isnan(x), default, x)


def _nz_big(x):
    return np.where(np.isnan(x), BIG, x)


def _htf(base_t: np.ndarray, htf: Frame, values: np.ndarray, offset: int = 1) -> np.ndarray:
    """Значение старшего ТФ как в request.security(..., expr[offset], lookahead_on).

    Для базового бара находим содержащий его бар старшего ТФ (j) и берём values[j - offset].
    offset=1 — последний закрытый бар старшего ТФ.
    """
    idx = np.searchsorted(htf.t, base_t, side="right") - 1 - offset
    out = np.full(len(base_t), NAN)
    ok = idx >= 0
    out[ok] = values[idx[ok]]
    return out


def _tf_sec(tf: str) -> float:
    return TF_MINUTES.get(tf.upper(), 15) * 60.0


def _session_mask(epoch_utc: np.ndarray, session: str, tz_name: str) -> np.ndarray:
    """Попадание бара в торговое окно вида "1630-2300" (может пересекать полночь)."""
    try:
        start_s, end_s = session.split("-")
        sh, sm = int(start_s[:2]), int(start_s[2:4])
        eh, em = int(end_s[:2]), int(end_s[2:4])
        tz = ZoneInfo(tz_name)
    except Exception:
        return np.zeros(len(epoch_utc), dtype=bool)

    out = np.zeros(len(epoch_utc), dtype=bool)
    for i, e in enumerate(epoch_utc):
        if np.isnan(e):
            continue
        loc = dt.datetime.fromtimestamp(float(e), tz)
        minutes = loc.hour * 60 + loc.minute
        a, b = sh * 60 + sm, eh * 60 + em
        out[i] = (a <= minutes < b) if a <= b else (minutes >= a or minutes < b)
    return out


def evaluate(frame: Frame, htf: dict[str, Frame], p: Params) -> dict:
    """Возвращает массивы условий и значений. Последний элемент — текущий бар."""
    t, o, h, l, c = frame.t, frame.o, frame.h, frame.l, frame.c
    n = len(c)
    nan_arr = np.full(n, NAN)
    false_arr = np.zeros(n, dtype=bool)

    # ---------------- старший ТФ: контекст 4H ----------------
    H = htf.get(p.htf_tf)
    if H is None or len(H.c) < p.htf_ema_len + p.slope_len + 2:
        return {"ok": False, "reason": f"мало данных {p.htf_tf}"}
    htf_ema = _htf(t, H, ta.ema(H.c, p.htf_ema_len), 1)
    htf_ema_past = _htf(t, H, ta.ema(H.c, p.htf_ema_len), p.slope_len + 1)
    htf_atr = _htf(t, H, ta.atr(H.h, H.l, H.c, 14), 1)
    htf_close = _htf(t, H, H.c, 1)

    with np.errstate(divide="ignore", invalid="ignore"):
        slope_atr = (htf_ema - htf_ema_past) / htf_atr
    slope_val = np.nan_to_num(slope_atr, nan=0.0, posinf=0.0, neginf=0.0)

    ctx_known = (~np.isnan(htf_ema)) & (~np.isnan(htf_close))
    ctx_l = ctx_known & (htf_close > htf_ema)
    ctx_s = ctx_known & (htf_close < htf_ema)
    slope_l = slope_val >= p.min_slope
    slope_s = slope_val <= -p.min_slope
    flat_mkt = np.abs(slope_val) < p.min_slope

    # ---------------- касание EMA200 на ТФ касания ----------------
    T = htf.get(p.touch_tf)
    if T is None or len(T.c) < p.htf_ema_len + 2:
        return {"ok": False, "reason": f"мало данных {p.touch_tf}"}
    ema_touch = _htf(t, T, ta.ema(T.c, p.htf_ema_len), 1)
    atr_touch = _htf(t, T, ta.atr(T.h, T.l, T.c, 14), 1)
    zone_half = _nz(atr_touch) * p.tol_htf

    touch_l = (l <= ema_touch + zone_half) & (c >= ema_touch - zone_half)
    touch_s = (h >= ema_touch - zone_half) & (c <= ema_touch + zone_half)

    tf_sec = _tf_sec_of_frame(frame)
    touch_bars = max(1, round(p.mem_hours * 3600.0 / tf_sec))
    bars_touch_l = _nz_big(ta.barssince(touch_l))
    bars_touch_s = _nz_big(ta.barssince(touch_s))
    touch_l_recent = bars_touch_l <= touch_bars
    touch_s_recent = bars_touch_s <= touch_bars

    # ---------------- нет болтанки ----------------
    flips = ta.cross(c, ema_touch).astype(float)
    cross_cnt = _nz_big(ta.rolling_sum(flips, p.chop_len))
    no_chop = cross_cnt <= p.max_cross

    # ---------------- тест EMA200 рабочего ТФ ----------------
    ema_ltf = ta.ema(c, p.ltf_ema_len)
    atr_chart = ta.atr(h, l, c, 14)
    test_band = _nz(atr_chart) * p.tol_ltf
    test_l = (l <= ema_ltf + test_band) & (c >= ema_ltf)
    test_s = (h >= ema_ltf - test_band) & (c <= ema_ltf)
    bars_test_l = _nz_big(ta.barssince(test_l))
    bars_test_s = _nz_big(ta.barssince(test_s))
    test_l_recent = bars_test_l <= p.test_look
    test_s_recent = bars_test_s <= p.test_look

    # ---------------- уровни ----------------
    ema_src_on = p.lv_mode != "Только уровни"
    lv_src_on = p.lv_mode != "Только EMA200"
    lv_tol_abs = _nz(atr_chart) * p.lv_tol
    hi_cl = ta.rolling_max(c, p.lv_persist)
    lo_cl = ta.rolling_min(c, p.lv_persist)

    levels: list[tuple[str, np.ndarray]] = []
    for i, px in enumerate(p.lv[:5], start=1):
        if px > 0 and lv_src_on:
            levels.append((f"LV{i}", np.full(n, float(px))))

    if p.use_auto and lv_src_on:
        D = htf.get("1D")
        W = htf.get("1W")
        if D is not None and len(D.c) > 2:
            levels.append(("PDH", _htf(t, D, D.h, 1)))
            levels.append(("PDL", _htf(t, D, D.l, 1)))
        if W is not None and len(W.c) > 2:
            levels.append(("PWH", _htf(t, W, W.h, 1)))
            levels.append(("PWL", _htf(t, W, W.l, 1)))

    lv_touch_s = false_arr.copy()
    lv_test_s = false_arr.copy()
    lv_touch_l = false_arr.copy()
    lv_test_l = false_arr.copy()
    best_s_ago = np.full(n, BIG)
    best_l_ago = np.full(n, BIG)
    lv_price_s = nan_arr.copy()
    lv_price_l = nan_arr.copy()
    lv_name_s = np.array([""] * n, dtype=object)
    lv_name_l = np.array([""] * n, dtype=object)

    for name, px in levels:
        act = ~np.isnan(px)
        px_safe = np.where(act, px, NAN)

        # сопротивление: цена консолидируется ниже уровня
        dn = act & (hi_cl < px_safe)
        s_touch = _nz_big(ta.barssince(act & (h >= px_safe - lv_tol_abs)))
        m_rej = _nz_big(ta.barssince(act & (h >= px_safe - lv_tol_abs) & (c < px_safe)))
        lv_touch_s |= dn & (s_touch <= p.lv_look)
        lv_test_s |= dn & (m_rej <= p.lv_look)
        better = m_rej < best_s_ago
        best_s_ago = np.where(better, m_rej, best_s_ago)
        lv_price_s = np.where(better, px_safe, lv_price_s)
        lv_name_s = np.where(better, name, lv_name_s)

        # поддержка: цена консолидируется выше уровня
        up = act & (lo_cl > px_safe)
        l_touch = _nz_big(ta.barssince(act & (l <= px_safe + lv_tol_abs)))
        l_rej = _nz_big(ta.barssince(act & (l <= px_safe + lv_tol_abs) & (c > px_safe)))
        lv_touch_l |= up & (l_touch <= p.lv_look)
        lv_test_l |= up & (l_rej <= p.lv_look)
        better = l_rej < best_l_ago
        best_l_ago = np.where(better, l_rej, best_l_ago)
        lv_price_l = np.where(better, px_safe, lv_price_l)
        lv_name_l = np.where(better, name, lv_name_l)

    lv_price_s = np.where(lv_test_s, lv_price_s, NAN)
    lv_price_l = np.where(lv_test_l, lv_price_l, NAN)

    # цели по уровням: ближайший в сторону сделки
    tgt_lv_s = np.full(n, NAN)
    tgt_lv_l = np.full(n, NAN)
    for name, px in levels:
        below = (~np.isnan(px)) & (px < c)
        above = (~np.isnan(px)) & (px > c)
        tgt_lv_s = np.where(below & (np.isnan(tgt_lv_s) | (px > tgt_lv_s)), px, tgt_lv_s)  # ближайший снизу = максимальный
        tgt_lv_l = np.where(above & (np.isnan(tgt_lv_l) | (px < tgt_lv_l)), px, tgt_lv_l)  # ближайший сверху = минимальный

    touch_ok_l = (ema_src_on & touch_l_recent) | (lv_src_on & lv_touch_l)
    touch_ok_s = (ema_src_on & touch_s_recent) | (lv_src_on & lv_touch_s)
    test_ok_l = (ema_src_on & test_l_recent) | (lv_src_on & lv_test_l)
    test_ok_s = (ema_src_on & test_s_recent) | (lv_src_on & lv_test_s)

    ref_l = np.where(~np.isnan(lv_price_l), lv_price_l, ema_ltf)
    ref_s = np.where(~np.isnan(lv_price_s), lv_price_s, ema_ltf)

    # ---------------- триггер ----------------
    rng = h - l
    body_abs = np.abs(c - o)
    ref_body = np.maximum(body_abs, 1e-9)
    lower_w = np.minimum(o, c) - l
    upper_w = h - np.maximum(o, c)
    big_enough = rng >= _nz(atr_chart) * p.min_range_k

    pin_l = bool(p.use_pin) & (lower_w >= p.pin_mult * ref_body) & (c > (h + l) / 2.0)
    pin_s = bool(p.use_pin) & (upper_w >= p.pin_mult * ref_body) & (c < (h + l) / 2.0)
    eng_l = bool(p.use_engulf) & (c > o) & (_shift(c, 1) < _shift(o, 1)) & (c >= _shift(o, 1)) & (o <= _shift(c, 1))
    eng_s = bool(p.use_engulf) & (c < o) & (_shift(c, 1) > _shift(o, 1)) & (c <= _shift(o, 1)) & (o >= _shift(c, 1))

    micro_hi = _shift(ta.rolling_max(h, p.trig_bos_len), 1)
    micro_lo = _shift(ta.rolling_min(l, p.trig_bos_len), 1)
    mic_l = bool(p.use_micro) & (c > micro_hi)
    mic_s = bool(p.use_micro) & (c < micro_lo)

    bull_fvg = l > _shift(h, 2)
    bear_fvg = h < _shift(l, 2)
    fvg_bot_s = ta.valuewhen(bull_fvg, _shift(h, 2))
    fvg_top_l = ta.valuewhen(bear_fvg, _shift(l, 2))
    fvg_ago_s = _nz_big(ta.barssince(bull_fvg))
    fvg_ago_l = _nz_big(ta.barssince(bear_fvg))
    ifvg_tol_abs = _nz(atr_chart) * p.ifvg_tol
    inv_s = p.use_ifvg & (~np.isnan(fvg_bot_s)) & (fvg_ago_s <= p.ifvg_look) & (c < fvg_bot_s) & (h >= fvg_bot_s - ifvg_tol_abs)
    inv_l = p.use_ifvg & (~np.isnan(fvg_top_l)) & (fvg_ago_l <= p.ifvg_look) & (c > fvg_top_l) & (l <= fvg_top_l + ifvg_tol_abs)

    pat_l = (((pin_l | eng_l) & big_enough) | mic_l | inv_l) & (c > ref_l)
    pat_s = (((pin_s | eng_s) & big_enough) | mic_s | inv_s) & (c < ref_s)
    bars_trig_l = _nz_big(ta.barssince(pat_l))
    bars_trig_s = _nz_big(ta.barssince(pat_s))
    trig_l = bars_trig_l <= p.trig_look
    trig_s = bars_trig_s <= p.trig_look

    # ---------------- риск ----------------
    piv_hi = ta.pivot_high(h, p.frac_l, p.frac_r)
    piv_lo = ta.pivot_low(l, p.frac_l, p.frac_r)
    frac_hi = ta.valuewhen(~np.isnan(piv_hi), piv_hi)
    frac_lo = ta.valuewhen(~np.isnan(piv_lo), piv_lo)

    sweep_ref_l = _shift(ta.rolling_min(l, p.sweep_len), 1)
    sweep_ref_s = _shift(ta.rolling_max(h, p.sweep_len), 1)
    sweep_l_raw = (l < sweep_ref_l) & (c > sweep_ref_l)
    sweep_s_raw = (h > sweep_ref_s) & (c < sweep_ref_s)
    sweep_l_fr = (~np.isnan(frac_lo)) & (l < frac_lo) & (c > frac_lo)
    sweep_s_fr = (~np.isnan(frac_hi)) & (h > frac_hi) & (c < frac_hi)
    sweep_l = np.where(p.use_frac_sweep & (~np.isnan(frac_lo)), sweep_l_fr, sweep_l_raw)
    sweep_s = np.where(p.use_frac_sweep & (~np.isnan(frac_hi)), sweep_s_fr, sweep_s_raw)
    since_sweep_l = _nz_big(ta.barssince(sweep_l))
    since_sweep_s = _nz_big(ta.barssince(sweep_s))

    swing_base_l = ta.rolling_min(l, p.swing_len)
    swing_base_s = ta.rolling_max(h, p.swing_len)
    manip_lo = ta.valuewhen(sweep_l, l)
    manip_hi = ta.valuewhen(sweep_s, h)
    manip_ago_l = _nz_big(ta.barssince(sweep_l))
    manip_ago_s = _nz_big(ta.barssince(sweep_s))
    use_manip_l = (p.stop_mode == "За манипуляцией") & (manip_ago_l <= p.manip_look) & (~np.isnan(manip_lo)) & (manip_lo < c)
    use_manip_s = (p.stop_mode == "За манипуляцией") & (manip_ago_s <= p.manip_look) & (~np.isnan(manip_hi)) & (manip_hi > c)

    stop_buf_abs = _nz(atr_chart) * p.stop_buf
    stop_l = np.where(use_manip_l, manip_lo, swing_base_l) - stop_buf_abs
    stop_s = np.where(use_manip_s, manip_hi, swing_base_s) + stop_buf_abs

    with np.errstate(divide="ignore", invalid="ignore"):
        risk_l = (c - stop_l) / c * 100.0
        risk_s = (stop_s - c) / c * 100.0
        risk_atr_l = (c - stop_l) / atr_chart
        risk_atr_s = (stop_s - c) / atr_chart

    if p.risk_atr_mode:
        risk_ok_l = (~np.isnan(risk_atr_l)) & (risk_atr_l >= p.risk_min_atr) & (risk_atr_l <= p.risk_max_atr)
        risk_ok_s = (~np.isnan(risk_atr_s)) & (risk_atr_s >= p.risk_min_atr) & (risk_atr_s <= p.risk_max_atr)
    else:
        risk_ok_l = (~np.isnan(risk_l)) & (risk_l >= p.risk_min) & (risk_l <= p.risk_max)
        risk_ok_s = (~np.isnan(risk_s)) & (risk_s >= p.risk_min) & (risk_s <= p.risk_max)

    # ---------------- цель ----------------
    swing_hi = _shift(ta.rolling_max(h, p.liq_len), 1)
    swing_lo = _shift(ta.rolling_min(l, p.liq_len), 1)
    liq_l = np.where(~np.isnan(tgt_lv_l), tgt_lv_l, swing_hi)
    liq_s = np.where(~np.isnan(tgt_lv_s), tgt_lv_s, swing_lo)

    with np.errstate(divide="ignore", invalid="ignore"):
        tgt_l = np.where(~np.isnan(liq_l) & (liq_l > c), (liq_l - c) / c * 100.0, NAN)
        tgt_s = np.where(~np.isnan(liq_s) & (liq_s < c), (c - liq_s) / c * 100.0, NAN)
        rr_l = np.where((~np.isnan(tgt_l)) & (risk_l > 0), tgt_l / risk_l, NAN)
        rr_s = np.where((~np.isnan(tgt_s)) & (risk_s > 0), tgt_s / risk_s, NAN)

    tgt_ok_l = (~np.isnan(tgt_l)) & (tgt_l >= p.tgt_min) & (tgt_l <= p.tgt_max) & (~np.isnan(rr_l)) & (rr_l >= p.min_rr)
    tgt_ok_s = (~np.isnan(tgt_s)) & (tgt_s >= p.tgt_min) & (tgt_s <= p.tgt_max) & (~np.isnan(rr_s)) & (rr_s >= p.min_rr)

    # ---------------- SMC-факторы ----------------
    f_sweep_l = since_sweep_l <= p.fac_look
    f_sweep_s = since_sweep_s <= p.fac_look
    bos_ref_l = _shift(ta.rolling_max(h, p.bos_len), 1)
    bos_ref_s = _shift(ta.rolling_min(l, p.bos_len), 1)
    bos_l = c > bos_ref_l
    bos_s = c < bos_ref_s
    f_bos_l = _nz_big(ta.barssince(bos_l)) <= p.fac_look
    f_bos_s = _nz_big(ta.barssince(bos_s)) <= p.fac_look
    f_fvg_l = _nz_big(ta.barssince(bull_fvg)) <= p.fvg_look
    f_fvg_s = _nz_big(ta.barssince(bear_fvg)) <= p.fvg_look

    rng_hi = ta.rolling_max(h, p.range_len)
    rng_lo = ta.rolling_min(l, p.range_len)
    with np.errstate(divide="ignore", invalid="ignore"):
        rng_pct = (rng_hi - rng_lo) / c * 100.0
        rng_pos = np.where(rng_hi > rng_lo, (c - rng_lo) / (rng_hi - rng_lo), 0.5)
    f_range = (rng_pct >= p.range_min_p) & (rng_pct <= p.range_max_p)
    f_disc_l = rng_pos <= 0.5
    f_disc_s = rng_pos >= 0.5

    score_l = f_sweep_l.astype(int) + f_bos_l.astype(int) + f_fvg_l.astype(int) + f_disc_l.astype(int)
    score_s = f_sweep_s.astype(int) + f_bos_s.astype(int) + f_fvg_s.astype(int) + f_disc_s.astype(int)

    # ---------------- доп. фильтры ----------------
    if p.use_stack:
        ef1, ef2, ef3 = ta.ema(c, p.ema_f1), ta.ema(c, p.ema_f2), ta.ema(c, p.ema_f3)
        stack_l = (ef1 > ef2) & (ef2 > ef3) & (c > ef3)
        stack_s = (ef1 < ef2) & (ef2 < ef3) & (c < ef3)
    else:
        stack_l = stack_s = np.ones(n, dtype=bool)

    if p.use_macd:
        macd_line = ta.ema(c, p.macd_fast) - ta.ema(c, p.macd_slow)
        macd_hist = macd_line - ta.ema(macd_line, p.macd_sig)
        macd_l = (macd_hist > 0) & (macd_hist > _shift(macd_hist, 1))
        macd_s = (macd_hist < 0) & (macd_hist < _shift(macd_hist, 1))
    else:
        macd_l = macd_s = np.ones(n, dtype=bool)

    if p.use_rsi:
        rsi_v = ta.rsi(c, p.rsi_len)
        rsi_l = (~np.isnan(rsi_v)) & (rsi_v < p.rsi_ob)
        rsi_s = (~np.isnan(rsi_v)) & (rsi_v > p.rsi_os)
    else:
        rsi_l = rsi_s = np.ones(n, dtype=bool)

    if p.use_vwap:
        vwap = _session_vwap(frame, p.tz_name)
        vwap_l = np.isnan(vwap) | (c > vwap)
        vwap_s = np.isnan(vwap) | (c < vwap)
    else:
        vwap_l = vwap_s = np.ones(n, dtype=bool)

    # ---------------- сторона и итог ----------------
    use_long = np.where(ctx_known, htf_close >= htf_ema, True)
    is_l = use_long

    allow_l = p.side_allow != "Только шорт"
    allow_s = p.side_allow != "Только лонг"

    if p.use_sess:
        sess_mask = _session_mask(frame.t, p.sess_in, p.tz_name)
    else:
        sess_mask = np.ones(n, dtype=bool)

    D = htf.get("1D")
    if p.use_day_ctx and D is not None and len(D.c) > 2:
        d_ema = _htf(t, D, ta.ema(D.c, p.htf_ema_len), 1)
        day_up = (~np.isnan(d_ema)) & (c > d_ema)
        day_dn = (~np.isnan(d_ema)) & (c < d_ema)
        day_ok_l, day_ok_s = day_up, day_dn
    else:
        day_ok_l = day_ok_s = np.ones(n, dtype=bool)

    r1 = np.where(is_l, allow_l, allow_s)
    r2 = np.where(is_l, ctx_l & day_ok_l, ctx_s & day_ok_s)
    r3 = np.where(is_l, touch_ok_l, touch_ok_s)
    r4 = np.where(is_l, slope_l, slope_s)
    r5 = np.where(is_l, test_ok_l, test_ok_s)
    r6 = np.where(is_l, trig_l, trig_s)
    r7 = np.where(is_l, risk_ok_l, risk_ok_s)

    if p.use_day_bud and D is not None and len(D.c) > 2:
        d_atr = _htf(t, D, ta.atr(D.h, D.l, D.c, 14), 1)
        with np.errstate(divide="ignore", invalid="ignore"):
            day_cap = d_atr * p.day_budget_k / c * 100.0
        sel_tgt = np.where(is_l, tgt_l, tgt_s)
        budget_ok = np.isnan(day_cap) | ((~np.isnan(sel_tgt)) & (sel_tgt <= day_cap))
    else:
        budget_ok = np.ones(n, dtype=bool)

    r8 = np.where(is_l, tgt_ok_l, tgt_ok_s) & budget_ok
    r9 = no_chop & f_range
    r10 = np.where(is_l, score_l, score_s) >= p.need_fac

    ok_vwap = np.where(is_l, vwap_l, vwap_s)
    ok_stack = np.where(is_l, stack_l, stack_s)
    ok_macd = np.where(is_l, macd_l, macd_s)
    ok_rsi = np.where(is_l, rsi_l, rsi_s)
    r11 = ok_vwap & ok_stack & ok_macd & ok_rsi
    r12 = sess_mask

    soft_names = [r4, r9, r10, r11]
    soft_cnt = np.sum([x.astype(int) for x in soft_names], axis=0)
    core = [r1, r2, r3, r5, r6, r7, r8, r12]
    core_miss = np.sum([(~x).astype(int) for x in core], axis=0)
    strict_miss = core_miss + np.sum([(~x).astype(int) for x in soft_names], axis=0)

    if p.mode_soft:
        missing = core_miss + np.maximum(0, p.soft_need - soft_cnt)
    else:
        missing = strict_miss

    all_ok = missing == 0
    long_sig = all_ok & is_l
    short_sig = all_ok & (~is_l)
    # Событие = первый бар серии: в Pine именно так работает арм-логика (armedL/armedS).
    long_evt = long_sig & (~_shift_bool(long_sig, 1))
    short_evt = short_sig & (~_shift_bool(short_sig, 1))

    return {
        "ok": True,
        "n": n,
        "all_ok": all_ok,
        "long_sig": long_sig,
        "short_sig": short_sig,
        "long_evt": long_evt,
        "short_evt": short_evt,
        "is_long": is_l,
        "r": [r1, r2, r3, r4, r5, r6, r7, r8, r9, r10, r11, r12],
        "missing": missing,
        "soft_cnt": soft_cnt,
        "stop_l": stop_l,
        "stop_s": stop_s,
        "risk_pct_l": risk_l,
        "risk_pct_s": risk_s,
        "rr_l": rr_l,
        "rr_s": rr_s,
        "score_l": score_l,
        "score_s": score_s,
        "since_sweep_l": since_sweep_l,
        "since_sweep_s": since_sweep_s,
        "ctx_known": ctx_known,
        "atr": atr_chart,
    }


def _shift_bool(x: np.ndarray, k: int) -> np.ndarray:
    out = np.zeros(len(x), dtype=bool)
    if k < len(x):
        out[k:] = x[:-k]
    return out


def _shift(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(x), NAN)
    if k < len(x):
        out[k:] = x[:-k] if k else x
    return out


def _session_vwap(frame: Frame, tz_name: str) -> np.ndarray:
    """VWAP с посуточным сбросом."""
    n = len(frame.c)
    out = np.full(n, NAN)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return out
    day = None
    pv = vv = 0.0
    for i in range(n):
        loc = dt.datetime.fromtimestamp(float(frame.t[i]), tz)
        d = loc.date()
        if d != day:
            day, pv, vv = d, 0.0, 0.0
        tp = (frame.h[i] + frame.l[i] + frame.c[i]) / 3.0
        v = frame.v[i] if frame.v[i] > 0 else 1.0
        pv += tp * v
        vv += v
        out[i] = pv / vv if vv else NAN
    return out


def _tf_sec_of_frame(frame: Frame) -> float:
    diffs = np.diff(frame.t)
    diffs = diffs[diffs > 0]
    if len(diffs) == 0:
        return 900.0
    return float(np.median(diffs))


def _tf_label_from_sec(sec: float) -> str:
    m = int(round(sec / 60))
    if m >= 1440:
        return "1D"
    return str(m if m in (1, 3, 5, 15, 30, 45) else max(1, m))


def in_session(ts: float, window: str) -> bool:
    """Попадание времени бара в окно вида "0800-2300".

    ts — время сервера MT5 в epoch, поэтому часы окна задаются по часам сервера
    (у большинства брокеров это UTC+2/+3, а не UTC).
    Пустое окно или неразобранная строка — считаем, что фильтра нет.
    Окно может пересекать полночь ("2200-0600").
    """
    window = (window or "").strip()
    if "-" not in window:
        return True
    try:
        a_s, b_s = window.split("-")
        a = int(a_s[:2]) * 60 + int(a_s[2:4])
        b = int(b_s[:2]) * 60 + int(b_s[2:4])
    except Exception:
        return True
    cur = dt.datetime.fromtimestamp(float(ts), dt.timezone.utc)
    minutes = cur.hour * 60 + cur.minute
    return (a <= minutes < b) if a <= b else (minutes >= a or minutes < b)
