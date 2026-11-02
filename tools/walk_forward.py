#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Walk-forward для вшитых стратегий bot.py на XAUUSD M15.

Методика (протокол IS -> OOS): скользящие фолды. На окне оптимизации
(60 дней) подбираем параметры из маленькой сетки по сумме R, затем
торгуем следующие 15 дней выбранными параметрами — эти сделки идут в
итог как out-of-sample. Склеиваем OOS-сделки всех фолдов.

Сетка маленькая (4-6 комбинаций на стратегию) — чтобы не плодить
новую подгонку. Стоимость исполнения: -0.05R на сделку (спред золота).

Запуск:
    python tools/walk_forward.py              # все стратегии
    python tools/walk_forward.py classic      # одну
"""
from __future__ import annotations

import dataclasses
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import bot
import strategy as st
import ta_core as ta

BARS = int(os.getenv("WF_BARS", "18000"))
OPT_DAYS = 60
TEST_DAYS = 15
BARS_PER_DAY = 96          # M15, 24/7 (выходные дают запас)
COST_R = 0.05              # спред/проскальзывание на сделку, R


def slice_frame(chart, a: int, b: int):
    return st.Frame(chart.t[a:b], chart.o[a:b], chart.h[a:b],
                    chart.l[a:b], chart.c[a:b], chart.v[a:b])


# ---------------- runner'ы стратегий ----------------

def run_classic(chart, htf, params):
    res = st.evaluate(chart, htf, params)
    if not res.get("ok"):
        return []
    return bot.simulate(res, chart, params)


def run_sonar(chart, htf, params):
    import sonar
    atr = sonar.build_atr(chart)
    levels = sonar.find_levels(chart, atr, min_touches=params["touches"])
    return sonar.backtest(chart, atr, levels, target_r=params["target_r"])


def run_range(chart, htf, params):
    import range_v2
    H = htf[bot.RANGE_ZONE_TF]
    zones = range_v2.zones_for_chart(chart, H, touches=2,
                                     flips_keep=bot.RANGE_FLIP_KEEP)
    return range_v2.backtest(chart, zones, H, stop_atr=params["stop_atr"],
                             tp_r=params["tp_r"], max_r=params["tp_r"] + 2.0,
                             session="", smt=None, smt_look=0,
                             max_bars=bot.PARAMS.max_bars, er_max=params["er_max"])


def run_ict(chart, htf, params):
    import ict2022
    return ict2022.backtest(chart, swing_len=bot.ICT_SWING,
                            fvg_window=bot.ICT_FVG_WINDOW,
                            retest_look=bot.ICT_RETEST_LOOK,
                            stop_buf_atr=params["stop_buf"],
                            tp_r=params["tp_r"], max_bars=120)


STRATEGIES = {
    "classic": {
        "runner": run_classic,
        "grid": [{"risk_max_atr": a, "min_rr": rr}
                 for a in (2.0, 4.0) for rr in (1.5, 2.0)],
        "apply": lambda p: dataclasses.replace(bot.PARAMS, **p),
    },
    "sonar": {
        "runner": run_sonar,
        "grid": [{"target_r": t, "touches": k}
                 for t in (1.5, 2.0, 3.0) for k in (2, 3)],
    },
    "range": {
        "runner": run_range,
        "grid": [{"tp_r": t, "stop_atr": 1.0, "er_max": e}
                 for t in (2.0, 3.0, 4.0) for e in (0.0, 0.20)],
    },
    "ict": {
        "runner": run_ict,
        "grid": [{"tp_r": t, "stop_buf": b} for t in (2.0, 4.0) for b in (0.25, 0.5)],
    },
}


def wfa(name: str, cfg: dict, chart, htf) -> None:
    runner = cfg["runner"]
    grid = cfg["grid"]
    opt = OPT_DAYS * BARS_PER_DAY
    test = TEST_DAYS * BARS_PER_DAY
    n = len(chart.c)
    print(f"\n{'=' * 66}\n{name.upper()} · фолды: окно {OPT_DAYS}д -> тест {TEST_DAYS}д, "
          f"сетка {len(grid)} комб.")
    print(f"{'=' * 66}")

    all_oos: list[float] = []
    fold_rows = []
    a = 0
    while a + opt + test <= n:
        opt_slice = slice_frame(chart, a, a + opt)
        test_slice = slice_frame(chart, a + opt, a + opt + test)
        when = time.strftime("%d.%m", time.gmtime(chart.t[a + opt]))

        # 1) подбор на окне оптимизации
        best_r, best_params = None, None
        for gp in grid:
            pp = cfg["apply"](gp) if "apply" in cfg else gp
            try:
                trades = runner(opt_slice, htf, pp)
            except Exception as e:
                print(f"  ошибка на фолде {when}: {e!r}")
                return
            r = sum(t["r"] for t in trades) - COST_R * len(trades)
            if best_r is None or r > best_r:
                best_r, best_params = r, gp

        # 2) OOS-тест выбранных параметров
        pp = cfg["apply"](best_params) if "apply" in cfg else best_params
        trades = runner(test_slice, htf, pp)
        raw = sum(t["r"] for t in trades)
        net = raw - COST_R * len(trades)
        all_oos.extend(t["r"] - COST_R for t in trades)
        fold_rows.append((when, best_params, len(trades), raw, net))
        a += test

    total_raw = sum(f[4] + COST_R * f[2] for f in fold_rows)
    total_net = sum(f[4] for f in fold_rows)
    pos_folds = sum(1 for f in fold_rows if f[4] > 0)
    print(f"{'фолд':>7} {'сделок':>7} {'raw R':>8} {'net R':>8}  параметры")
    for when, gp, cnt, raw, net in fold_rows:
        print(f"{when:>7} {cnt:>7} {raw:>+8.2f} {net:>+8.2f}  {gp}")
    avg = total_net / len(all_oos) if all_oos else 0.0
    print(f"\nOOS итог: {len(all_oos)} сделок · net {total_net:+.2f}R · "
          f"средняя {avg:+.3f}R · фолдов в плюсе {pos_folds}/{len(fold_rows)}")
    verdict = ("ПРОХОДИТ" if (total_net > 5 and pos_folds >= 0.6 * len(fold_rows) and avg > 0.05)
               else "НЕ ПРОХОДИТ")
    print(f"Вердикт walk-forward: {verdict}")


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    symbol = os.getenv("BOT_SYMBOL", "XAUUSD").strip()
    data = bot.connect()
    chart, htf = bot.load_frames(data, symbol, bot.TF, BARS)
    days = (chart.t[-1] - chart.t[0]) / 86400
    global BARS_PER_DAY
    # у индексов сессии короче суток: считаем фактические бары/день
    BARS_PER_DAY = max(1, int(round(len(chart.c) / days)))
    print(f"данные {symbol}: {len(chart.c)} баров ({days:.0f} дн., ~{BARS_PER_DAY}/день), "
          f"фолдов на стратегию ~{int((days - OPT_DAYS) // TEST_DAYS)}")
    names = [only] if only else list(STRATEGIES)
    for name in names:
        if only and only != name and len(sys.argv) > 1:
            continue
        wfa(name, STRATEGIES[name], chart, htf)
    data.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
