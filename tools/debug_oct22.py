#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Дебаг 2026.10.22: почему у bot.py нет шортового сетапа уровня, а у EA есть."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import bot
import strategy as st
import ta_core as ta

data = bot.connect()
chart, htf = bot.load_frames(data, "XAUUSD", bot.TF, 8000)
t = chart.t
D = htf.get("1D")
W = htf.get("1W")

# PDH/PWL вокруг 22.10
for i in range(len(D.t)):
    d = datetime.utcfromtimestamp(float(D.t[i]))
    if datetime(2026, 10, 19) <= d <= datetime(2026, 10, 23):
        print(f"D {d:%m.%d %a}: H={D.h[i]:.2f} L={D.l[i]:.2f}")

idx = next(i for i in range(len(t))
           if datetime.utcfromtimestamp(float(t[i])).strftime("%Y.%m.%d %H:%M") == "2026.10.22 01:00")
res = bot.st.evaluate(chart, htf, bot.PARAMS)
print(f"\nбар 22.10 01:00: close={chart.c[idx]:.2f} high={chart.h[idx]:.2f}")
print(f"  r3={bool(res['r'][2][idx])} r5={bool(res['r'][4][idx])}")

# вручную: hi_cl, lvTol, PDH/PWH
hi_cl = ta.rolling_max(chart.c, bot.PARAMS.lv_persist)[idx]
lo_cl = ta.rolling_min(chart.c, bot.PARAMS.lv_persist)[idx]
atr_chart = ta.atr(chart.h, chart.l, chart.c, 14)[idx]
tol = atr_chart * bot.PARAMS.lv_tol
pdh = float(D.h[np.searchsorted(D.t, t[idx]) - 1]) if False else None
# PDH = вчерашний хай: D-бар, предшествующий дню бара
day = datetime.utcfromtimestamp(float(t[idx])).date()
pdh = pdl = pwh = pwl = None
for i in range(len(D.t)):
    d = datetime.utcfromtimestamp(float(D.t[i])).date()
    if d == day - dt_days(1) if False else False:
        pass
import datetime as dt_mod
for i in range(len(D.t)):
    d = datetime.utcfromtimestamp(float(D.t[i])).date()
    if d == day - dt_mod.timedelta(days=1):
        pdh, pdl = float(D.h[i]), float(D.l[i])
    if d >= day - dt_mod.timedelta(days=7):
        if pwh is None or float(D.h[i]) > (pwh or 0):
            pwh = float(D.h[i])
        if pwl is None or float(D.l[i]) < (pwl or 1e9):
            pwl = float(D.l[i])
print(f"  hi_cl(6)={hi_cl:.2f} lo_cl(6)={lo_cl:.2f} lvTol={tol:.2f}")
print(f"  PDH={pdh} PDL={pdl} PWH={pwh} PWL={pwl}")
for nm, px in (("PDH", pdh), ("PWH", pwh)):
    if px and hi_cl < px:
        print(f"  {nm}: resistance ok (hi_cl {hi_cl:.2f} < {px:.2f}); "
              f"touch зона [{px - tol:.2f}, ∞); reject если close < {px:.2f}")
data.close()
