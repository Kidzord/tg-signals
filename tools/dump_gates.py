#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Выгрузка per-bar гейтов st.evaluate для сверки с KidzordEA.

    python tools/dump_gates.py            # XAUUSD M15, BOT_BARS баров
Пишет gates_bot_XAUUSD.csv (time;isL;r1..r12) в папку mt5_ea.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402  (импорт безопасен: запуск под __main__)


def main() -> int:
    symbol = os.getenv("BOT_SYMBOL", "XAUUSD")
    bars = int(os.getenv("BOT_BARS", "8000"))
    data = bot.connect()
    if data is None:
        print("MT5 недоступен")
        return 1
    chart, htf = bot.load_frames(data, symbol, bot.TF, bars)
    res = bot.st.evaluate(chart, htf, bot.PARAMS)
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "mt5_ea", f"gates_bot_{symbol}.csv")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("time;isL;r1;r2;r3;r4;r5;r6;r7;r8;r9;r10;r11;r12\n")
        for i in range(len(chart.t)):
            ts = datetime.utcfromtimestamp(float(chart.t[i])).strftime("%Y.%m.%d %H:%M")
            row = [ts, "1" if bool(res["is_long"][i]) else "0"]
            row += ["1" if bool(res["r"][k][i]) else "0" for k in range(12)]
            f.write(";".join(row) + "\n")
    print(f"выгружено {len(chart.t)} баров -> {out}")
    data.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
