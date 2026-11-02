#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Трассировка setup-массивов evaluate() на 22.10 15:30."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import bot
import strategy as st
import ta_core as ta

data = bot.connect()
chart, htf = bot.load_frames(data, "XAUUSD", bot.TF, 8000)
t = chart.t
h, l, c = chart.h, chart.l, chart.c
n = len(t)
atr_chart = ta.atr(h, l, c, 14)
lv_tol_abs = st._nz(atr_chart) * bot.PARAMS.lv_tol
hi_cl = ta.rolling_max(c, bot.PARAMS.lv_persist)
lo_cl = ta.rolling_min(c, bot.PARAMS.lv_persist)
D, W = htf.get("1D"), htf.get("1W")

def htf_series(F, key):
    return st._htf(t, F, getattr(F, key), 1)

levels = [("PDH", htf_series(D, "h"), 1), ("PDL", htf_series(D, "l"), -1),
          ("PWH", htf_series(W, "h"), 1), ("PWL", htf_series(W, "l"), -1)]

idx = next(i for i in range(n)
           if datetime.utcfromtimestamp(float(t[i])).strftime("%Y.%m.%d %H:%M") == "2026.10.22 15:30")

for name, px, kind in levels:
    act = ~np.isnan(px)
    px_safe = np.where(act, px, np.nan)
    px_changed = act & (px_safe != st._shift(px_safe, 1))
    ev_s = act & (h >= px_safe - lv_tol_abs)
    ev_rej_s = ev_s & (c < px_safe)
    s_touch = st._ago_with_reset(ev_s, px_changed)
    m_rej = st._ago_with_reset(ev_rej_s, px_changed)
    s_touch_raw = ta.barssince(ev_s)
    m_rej_raw = ta.barssince(ev_rej_s)
    dn = act & (hi_cl < px_safe)
    print(f"{name}: px={px[idx]:.2f} dn={bool(dn[idx])} "
          f"touch ago raw={s_touch_raw[idx]:.0f} reset={s_touch[idx]:.0f} "
          f"rej ago raw={m_rej_raw[idx]:.0f} reset={m_rej[idx]:.0f} "
          f"(lv_look={bot.PARAMS.lv_look})")

# что реально в evaluate
res = bot.st.evaluate(chart, htf, bot.PARAMS)
print(f"\nr3={bool(res['r'][2][idx])} r5={bool(res['r'][4][idx])}")
data.close()
