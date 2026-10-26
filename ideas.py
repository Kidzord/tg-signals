"""Перебор идей из методичек поверх текущего лучшего варианта.

База: зоны по телам H4 + перевёрнутые уровни, стоп 10$, цель 2R.
Каждая строка — одна идея, добавленная к базе.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot
import range_v2 as r2
import sonar
import strategy as st
import ta_core as ta


def body_frame(fr: st.Frame) -> st.Frame:
    return st.Frame(fr.t, fr.o, np.maximum(fr.o, fr.c), np.minimum(fr.o, fr.c), fr.c, fr.v)


def build_table(H: st.Frame, extra: str = "none", flips_keep: int = 60, touches: int = 2):
    src = body_frame(H)
    atr_h = ta.atr(src.h, src.l, src.c, 14)
    table = sonar.find_levels(src, atr_h, 3, 3, 0.2, touches, 6)

    # дополнительные типы уровней
    gaps, dojis = [], []
    for i in range(1, len(H.c)):
        a = atr_h[i] if not np.isnan(atr_h[i]) else 0.0
        if extra in ("gap", "both") and a > 0:
            if H.l[i] > H.h[i - 1]:            # бычий гэп → поддержка на high[i-1]
                gaps.append({"bar": i, "kind": "sup", "price": float(H.h[i - 1]), "until": i + 120})
            if H.h[i] < H.l[i - 1]:            # медвежий гэп → сопротивление на low[i-1]
                gaps.append({"bar": i, "kind": "res", "price": float(H.l[i - 1]), "until": i + 120})
        if extra in ("doji", "both"):
            rng = H.h[i] - H.l[i]
            body = abs(H.c[i] - H.o[i])
            if rng > 0 and body / rng <= 0.2:  # доджи
                dojis.append({"bar": i, "kind": "sup", "price": float(H.c[i]), "until": i + 80})
                dojis.append({"bar": i, "kind": "res", "price": float(H.c[i]), "until": i + 80})

    merged, flips = [], []
    for i, row in enumerate(table):
        a = atr_h[i] if not np.isnan(atr_h[i]) else 0.0
        for lv in row["levels"]:
            p = float(lv["price"])
            if lv["kind"] == "res" and H.c[i] > p + a * 0.1:
                flips.append({"kind": "sup", "price": p, "until": i + flips_keep})
            elif lv["kind"] == "sup" and H.c[i] < p - a * 0.1:
                flips.append({"kind": "res", "price": p, "until": i + flips_keep})
        flips = [f for f in flips if f["until"] >= i]

        levels = [dict(x) for x in row["levels"]]
        levels += [{"kind": f["kind"], "price": f["price"], "touches": 2, "born": i} for f in flips]
        levels += [{"kind": g["kind"], "price": g["price"], "touches": 2, "born": g["bar"]}
                   for g in gaps if g["bar"] <= i <= g["until"]]
        levels += [{"kind": d["kind"], "price": d["price"], "touches": 2, "born": d["bar"]}
                   for d in dojis if d["bar"] <= i <= d["until"]]
        merged.append({"bar": row["bar"], "levels": levels})
    return merged


def to_chart(chart: st.Frame, H: st.Frame, table: list[dict]) -> list[dict]:
    idx = np.searchsorted(H.t, chart.t, side="right") - 1 - 1
    return [table[j] if j >= 0 else {"bar": -1, "levels": []} for j in idx]


def stats(tr, chart, name: str) -> None:
    if not tr:
        print(f"{name:26} 0 сделок")
        return
    rs = [t["r"] for t in tr]
    w = sum(1 for r in rs if r > 0)
    h = len(chart.c) // 2
    a = [t["r"] for t in tr if t["entry_i"] < h]
    b = [t["r"] for t in tr if t["entry_i"] >= h]
    print(f"{name:26} {len(tr):>4} · {w / len(rs) * 100:>3.0f}% · {sum(rs):>+6.0f}R · "
          f"1п {sum(a):>+5.0f} · 2п {sum(b):>+5.0f}")


def main() -> None:
    data = bot.MT5Data()
    try:
        chart, htf = bot.load_frames(data, bot.SYMBOL, bot.TF, int(os.getenv("BOT_BARS", "20000")))
    finally:
        data.close()
    H = htf["240"]

    print(f"{'вариант':26} {'сдел':>4} · {'винр':>4} · {'сумма':>6} · половины")
    print("-" * 76)

    # база
    base_tbl = build_table(H)
    base_z = to_chart(chart, H, base_tbl)
    base = r2.backtest(chart, base_z, H, stop_pts=10.0)
    stats(base, chart, "база (тела + flip)")

    # новые типы уровней
    for extra, label in (("gap", "+ GAP-SNR"), ("doji", "+ DOJI-SNR"), ("both", "+ GAP и DOJI")):
        z = to_chart(chart, H, build_table(H, extra=extra))
        stats(r2.backtest(chart, z, H, stop_pts=10.0), chart, label)

    # цель 1R (потолок 100 пунктов)
    stats(r2.backtest(chart, base_z, H, stop_pts=10.0, tp_r=1.0), chart, "цель 1R (потолок 100p)")

    # premium / discount
    rng_hi = ta.rolling_max(H.h, 30)
    rng_lo = ta.rolling_min(H.l, 30)
    eq = (rng_hi + rng_lo) / 2
    idx = np.searchsorted(H.t, chart.t, side="right") - 1 - 1
    eq15 = np.where(idx >= 0, eq[np.clip(idx, 0, None)], np.nan)
    pd_tr = [t for t in base
             if (t["side"] == "LONG" and not np.isnan(eq15[t["entry_i"]]) and t["entry"] < eq15[t["entry_i"]])
             or (t["side"] == "SHORT" and not np.isnan(eq15[t["entry_i"]]) and t["entry"] > eq15[t["entry_i"]])]
    stats(pd_tr, chart, "+ premium/discount")

    # killzones: только 07:00–16:00 UTC (Лондон + Нью-Йорк)
    kz = []
    for t in base:
        hour = dt.datetime.fromtimestamp(float(chart.t[t["entry_i"]]), dt.timezone.utc).hour
        if 7 <= hour < 16:
            kz.append(t)
    stats(kz, chart, "+ киллзоны 07–16 UTC")


if __name__ == "__main__":
    main()
