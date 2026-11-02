#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Прогрев EMA200(1H) на момент 2026.07.28: сколько баров истории было у тестера."""
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
T = htf.get(bot.PARAMS.touch_tf)
n_T = len(T.c)
t_end = datetime(2026, 7, 28, 2, 0, tzinfo=timezone.utc).timestamp()

cut = int(np.searchsorted(T.t, t_end))          # H1 баров до 02:00 28.07
print(f"H1 баров до 28.07 02:00: {cut}")

for depth in (2000, 1200, 800, 500, 350):
    if depth > cut:
        continue
    seg = T.c[cut - depth:cut]
    h_seg, l_seg, c_seg = T.h[cut-depth:cut], T.l[cut-depth:cut], T.c[cut-depth:cut]
    ema = ta.ema(seg, 200)[-1]
    atr = (lambda tr: None)
    # ATR по сегменту
    atr_v = h_seg[0] - l_seg[0]
    for i in range(1, len(seg)):
        tr = max(h_seg[i] - l_seg[i], max(abs(h_seg[i] - c_seg[i-1]), abs(l_seg[i] - c_seg[i-1])))
        atr_v = (atr_v * 13 + tr) / 14
    print(f"depth={depth:>5}: ema200(1H)={ema:.2f} atr={atr_v:.2f} test_zone={atr_v*0.15:.2f} "
          f"-> test_s при close 4077: {'да' if 4077 < ema else 'нет'}")
data.close()
