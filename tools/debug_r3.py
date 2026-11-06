#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Дебаг r3/r5 на барах расхождения: какие промежуточные значения у bot.py."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import timezone
import bot
import strategy as st
import ta_core as ta

TARGETS = ["2026.07.27 22:45", "2026.07.28 01:00", "2026.07.28 02:00"]

data = bot.connect()
chart, htf = bot.load_frames(data, "XAUUSD", bot.TF, 8000)
res = bot.st.evaluate(chart, htf, bot.PARAMS)
t = chart.t

T = htf.get(bot.PARAMS.touch_tf)
ema_touch = st._htf(t, T, ta.ema(T.c, bot.PARAMS.htf_ema_len), 1)
atr_touch = st._htf(t, T, ta.atr(T.h, T.l, T.c, 14), 1)
zone_half = st._nz(atr_touch) * bot.PARAMS.tol_htf
test_band = st._nz(atr_touch) * bot.PARAMS.tol_ltf

touch_s = (chart.h >= ema_touch - zone_half) & (chart.c <= ema_touch + zone_half)
test_s = (chart.h >= ema_touch - test_band) & (chart.c < ema_touch)

for tgt in TARGETS:
    dt_t = datetime.strptime(tgt, "%Y.%m.%d %H:%M")
    epoch = dt_t.replace(tzinfo=timezone.utc).timestamp() if False else None
    # ищем бар по времени
    idx = None
    for i in range(len(t)):
        if datetime.utcfromtimestamp(float(t[i])).strftime("%Y.%m.%d %H:%M") == tgt:
            idx = i
            break
    if idx is None:
        print(f"{tgt}: бара нет в окне bot.py")
        continue
    ago_s = ta.barssince(touch_s)
    ago_test = ta.barssince(test_s)
    print(f"{tgt}: idx={idx}")
    print(f"  close={chart.c[idx]:.2f} high={chart.h[idx]:.2f}")
    print(f"  ema_touch={ema_touch[idx]:.2f} atr_touch={atr_touch[idx]:.3f} "
          f"zone={zone_half[idx]:.3f} test_band={test_band[idx]:.3f}")
    print(f"  touch_s сейчас={bool(touch_s[idx])} ago={ago_s[idx]} "
          f"(лимит {int(bot.PARAMS.mem_hours*3600/900)})")
    print(f"  test_s сейчас={bool(test_s[idx])} ago={ago_test[idx]} (лимит {bot.PARAMS.test_look})")
    print(f"  r3={bool(res['r'][2][idx])} r5={bool(res['r'][4][idx])}")
    # ближайшие касания вокруг
    near = [j for j in range(max(0, idx-100), idx+1) if touch_s[j]]
    print(f"  касания sh за 100 баров: {len(near)} шт, последнее {near[-1] if near else '-'}")
data.close()
