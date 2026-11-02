#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Гипотеза: расхождение r3/r5 — разный прогрев EMA200(1H) (тестер MT5 подгружает
мало истории перед стартом, bot.py берёт 2300+ баров)."""
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
T = htf.get(bot.PARAMS.touch_tf)
n_T = len(T.c)
print(f"H1 баров в кадре: {n_T}")

for depth in (n_T, 700, 500, 300):
    c_T = T.c[-depth:]
    e_trunc = ta.ema(c_T, 200)
    e = np.full(n_T, np.nan)
    e[n_T - depth:] = e_trunc
    ema = st._htf(t, T, e, 1)
    atr = st._htf(t, T, ta.atr(T.h, T.l, T.c, 14), 1)
    band = st._nz(atr) * bot.PARAMS.tol_ltf
    test_s = (chart.h >= ema - band) & (chart.c < ema)
    # скільки баров r5-актуальных на заданных барах
    out = []
    for tgt in ("2026.07.27 22:45", "2026.07.28 01:00", "2026.07.28 02:00"):
        idx = next(i for i in range(len(t))
                   if datetime.utcfromtimestamp(float(t[i])).strftime("%Y.%m.%d %H:%M") == tgt)
        ago = ta.barssince(test_s)[idx]
        out.append(f"{tgt.split()[1]}: ema={ema[idx]:.1f} test_ago={ago:.0f}")
    print(f"depth={depth:>5}: " + " | ".join(out))
data.close()
