"""Проверка: живёт ли преимущество только во флэте, и что остаётся после издержек.

Режим определяется коэффициентом эффективности (Kaufman):
    ER = |close[i] - close[i-N]| / сумма |close[k] - close[k-1]|
ER → 1 — чистый тренд, ER → 0 — пила/флэт.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot
import range_v2 as r2
import ta_core as ta

SPREADS = (0.0, 0.30, 0.60)
N_ER = 50


def main() -> None:
    data = bot.MT5Data()
    try:
        chart, htf = bot.load_frames(data, bot.SYMBOL, bot.TF, int(os.getenv("BOT_BARS", "20000")))
    finally:
        data.close()

    H = htf["240"]
    zones = r2.zones_for_chart(chart, H, touches=2, flips_keep=60)
    trades = r2.backtest(chart, zones, H, stop_pts=10.0)
    er = ta.efficiency_ratio(chart.c, N_ER)
    stop = 10.0

    for t in trades:
        t["er"] = er[t["entry_i"]] if not np.isnan(er[t["entry_i"]]) else 0.5
        t["when"] = dt.datetime.fromtimestamp(float(chart.t[t["exit_i"]]), dt.timezone.utc)

    ers = np.array([t["er"] for t in trades])
    q1, q2 = np.quantile(ers, 1 / 3), np.quantile(ers, 2 / 3)
    print(f"сделок {len(trades)} · границы режима по ER: {q1:.3f} / {q2:.3f}\n")

    def bucket(t):
        return "флэт" if t["er"] <= q1 else ("средний" if t["er"] <= q2 else "тренд")

    print(f"{'режим':>9} {'сдел':>5} {'винр':>5} {'сумма R':>8} {'ср.R':>6}"
          + "".join(f" {'спред ' + str(s):>10}" for s in SPREADS[1:]))
    for name in ("флэт", "средний", "тренд"):
        sub = [t for t in trades if bucket(t) == name]
        if not sub:
            continue
        rs = np.array([t["r"] for t in sub])
        extra = ""
        for s in SPREADS[1:]:
            net = rs - s / stop
            extra += f" {net.sum():>+10.1f}"
        print(f"{name:>9} {len(sub):>5} {(rs > 0).mean() * 100:>4.0f}% {rs.sum():>+8.1f} "
              f"{rs.mean():>+6.2f}{extra}")

    print("\nпо месяцам:")
    months: dict[str, list[dict]] = {}
    for t in trades:
        months.setdefault(t["when"].strftime("%Y-%m"), []).append(t)
    print(f"{'месяц':>8} {'сдел':>5} {'сумма R':>8} {'ср.R':>6} {'ср.ER':>6}")
    for m in sorted(months):
        sub = months[m]
        rs = np.array([t["r"] for t in sub])
        print(f"{m:>8} {len(sub):>5} {rs.sum():>+8.1f} {rs.mean():>+6.2f} "
              f"{np.mean([t['er'] for t in sub]):>6.2f}")


if __name__ == "__main__":
    main()
