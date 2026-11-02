#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OOS-проверка classic: комбо-параметры подобраны на 2026.07.28-10.24 (IS),
значит сделки ДО 28 июля — честный out-of-sample."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import bot
import strategy as st
import ta_core as ta
from datetime import timezone

SPLIT = datetime(2026, 7, 28, tzinfo=timezone.utc).timestamp()

data = bot.connect()
chart, htf = bot.load_frames(data, "XAUUSD", bot.TF, 18000)
res = bot.st.evaluate(chart, htf, bot.PARAMS)
trades = bot.simulate(res, chart, bot.PARAMS)
print(f"период {len(chart.c)} баров "
      f"({datetime.utcfromtimestamp(float(chart.t[0])):%d.%m.%Y} - "
      f"{datetime.utcfromtimestamp(float(chart.t[-1])):%d.%m.%Y}), сделок {len(trades)}")

is_t = [t for t in trades if float(chart.t[t["ei"]]) >= SPLIT]
oos_t = [t for t in trades if float(chart.t[t["ei"]]) < SPLIT]

def stat(name, ts):
    if not ts:
        print(f"{name}: сделок нет")
        return
    r = sum(t["r"] for t in ts)
    wins = sum(1 for t in ts if t["r"] > 0)
    print(f"{name}: {len(ts)} сделок · win {100*wins/len(ts):.0f}% · сумма {r:+.2f}R · "
          f"средняя {r/len(ts):+.2f}R")

stat("IS  (28.07-30.10, параметры подобраны здесь)", is_t)
stat("OOS (апрель-27.07, параметры НЕ видели эти данные)", oos_t)
data.close()
