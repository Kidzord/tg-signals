"""Превью: как выглядели бы сигналы в группе за последние N дней.

    python preview.py [дней]

Рисует карточки по бару входа (без будущего), считает баланс от 5000$
с риском 0.5% на сделку и складывает тексты постов в preview_20d.md.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot
import feeds
import range_v2 as r2
import render as card
import ta_core as ta
import voice

START = float(os.getenv("PREVIEW_START", "5000"))
RISK_PCT = float(os.getenv("PREVIEW_RISK", "0.5"))
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview")


def main() -> None:
    data = bot.MT5Data()
    try:
        chart, htf = bot.load_frames(data, bot.SYMBOL, bot.TF, int(os.getenv("BOT_BARS", "20000")))
        smt = None
        if bot.SMT_LOOK > 0 and bot.SYMBOL == bot.SMT_FOR:
            try:
                ref = data.frame(bot.SMT_SYMBOL, bot.TF, int(os.getenv("BOT_BARS", "20000")))
                smt = r2.smt_ref(chart, ref)
            except Exception as e:
                print(f"SMT: нет данных {bot.SMT_SYMBOL} ({e})")
    finally:
        data.close()

    H = htf["240"]
    zones = r2.zones_for_chart(chart, H, touches=2, flips_keep=bot.RANGE_FLIP_KEEP)
    trades = r2.backtest(chart, zones, H, stop_pts=bot.RANGE_STOP_PTS,
                         smt=smt, smt_look=bot.SMT_LOOK)
    er = ta.efficiency_ratio(chart.c)
    trades = [t for t in trades if not np.isnan(er[t["entry_i"]]) and er[t["entry_i"]] <= bot.RANGE_ER_MAX]

    cutoff = chart.t[-1] - DAYS * 86400
    sel = [t for t in trades if chart.t[t["entry_i"]] >= cutoff]
    if not sel:
        print(f"за {DAYS} дней сделок нет")
        return

    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        if f.endswith(".png"):
            os.remove(os.path.join(OUT, f))

    balance = START
    rows = []
    md = [f"# Сигналы за {DAYS} дней\n",
          f"Старт {START:.0f}$ · риск {RISK_PCT}% на сделку · {bot.SYMBOL} {feeds.tf_label(bot.TF)}\n"]

    for t in sel:
        i = t["entry_i"]
        when = dt.datetime.fromtimestamp(float(chart.t[i]), dt.timezone.utc)
        sgn = 1 if t["side"] == "LONG" else -1
        entry = float(t["entry"])
        stop = float(t["stop"])
        tp = float(t["target"])

        risk_usd = balance * RISK_PCT / 100.0
        pnl = t["r"] * risk_usd
        balance += pnl

        sig = card.Signal(side=t["side"], symbol=bot.SYMBOL, tf=bot.TF,
                          entry=entry, sl=stop, tp1=tp, when=when)

        lo = max(0, i - 119)
        bars = [feeds.Bar(dt.datetime.fromtimestamp(float(chart.t[j]), dt.timezone.utc),
                          float(chart.o[j]), float(chart.h[j]), float(chart.l[j]), float(chart.c[j]))
                for j in range(lo, i + 1)]
        fname = f"{when:%Y%m%d_%H%M}_{t['side']}.png"
        card.render_card(sig, bars, "MT5", False, os.path.join(OUT, fname))

        rows.append({"when": when, "side": t["side"], "entry": entry, "exit": t["exit"],
                     "r": t["r"], "pnl": pnl, "balance": balance, "file": fname})
        md.append(f"## {when:%d.%m %H:%M} · {t['side']}\n")
        md.append("```\n" + voice.signal_caption(sig, source="MT5").replace("<b>", "").replace("</b>", "")
                  .replace("<i>", "").replace("</i>", "") + "\n```\n")
        md.append(f"Результат: **{t['r']:+.2f}R** ({pnl:+.1f}$) · выход {t['exit']} · баланс {balance:.0f}$\n")

    rs = [r["r"] for r in rows]
    wins = sum(1 for r in rs if r > 0)
    print(f"{DAYS} дней · {len(rows)} сигналов · прибыльных {wins} ({wins / len(rs) * 100:.0f}%)")
    print(f"сумма {sum(rs):+.1f}R · {START:.0f} → {balance:.0f}$ "
          f"({(balance - START) / START * 100:+.1f}%)\n")
    print(f"{'дата':14} {'сторона':7} {'вход':>9} {'выход':>7} {'R':>6} {'$':>8} {'баланс':>8}")
    for r in rows:
        print(f"{r['when']:%d.%m %H:%M}   {r['side']:7} {r['entry']:>9.2f} {r['exit']:>7} "
              f"{r['r']:>+6.2f} {r['pnl']:>+8.1f} {r['balance']:>8.0f}")

    md.append(f"\n---\n\n**Итог:** {len(rows)} сигналов · прибыльных {wins} · "
              f"сумма {sum(rs):+.2f}R · {START:.0f} → {balance:.0f}$ "
              f"({(balance - START) / START * 100:+.2f}%)\n")
    open(os.path.join(OUT, "preview.md"), "w", encoding="utf-8").write("\n".join(md))
    print(f"\nкарточки и текст: {OUT}")


if __name__ == "__main__":
    main()
