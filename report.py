"""Отчёт по сделкам: баланс от стартовой суммы, проценты, график эквити.

    python report.py          — посчитать и сохранить график
    python report.py send     — ещё и отправить в Telegram

Настройки через окружение: REPORT_START (5000), REPORT_RISK (1.0 — % баланса на сделку).
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot
import feeds
import render as card
import strategy as st

START = float(os.getenv("REPORT_START", "5000"))
RISK = float(os.getenv("REPORT_RISK", "1.0"))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples", "report_equity.png")


def build():
    data = bot.MT5Data()
    try:
        chart, htf = bot.load_frames(data, bot.SYMBOL, bot.TF, bot.BARS)
        res = st.evaluate(chart, htf, bot.PARAMS)
        if not res.get("ok"):
            raise RuntimeError(res.get("reason"))
        trades = bot.simulate(res, chart, bot.PARAMS)
    finally:
        data.close()

    balance = START
    rows = []
    for t in trades:
        pnl_pct = t["r"] * RISK
        pnl_usd = balance * pnl_pct / 100.0
        balance += pnl_usd
        rows.append({
            "when": dt.datetime.fromtimestamp(float(chart.t[t["i"]]), dt.timezone.utc),
            "side": "LONG" if t["dir"] == 1 else "SHORT",
            "entry": t["entry"],
            "exit_price": t.get("exit_price", float("nan")),
            "exit": t["exit"],
            "r": t["r"],
            "pnl_pct": pnl_pct,
            "pnl_usd": pnl_usd,
            "balance": balance,
        })
    return chart, rows, balance


def draw(rows, balance) -> None:
    eq = [START] + [r["balance"] for r in rows]
    xs = list(range(len(eq)))

    fig, ax = plt.subplots(figsize=(12.8, 6.0), dpi=100, facecolor=card.BG)
    ax.set_facecolor(card.BG)

    ax.plot(xs, eq, color=card.GOLD, lw=2.2, zorder=3)
    ax.fill_between(xs, START, eq, color=card.GOLD, alpha=0.12, zorder=2)
    ax.axhline(START, color=card.SUB, lw=1, ls=(0, (4, 4)), zorder=1)

    ax.set_title(f"Симуляция по истории · {bot.SYMBOL} {feeds.tf_label(bot.TF)} · "
                 f"{len(rows)} сделок · риск {RISK:.1f}% на сделку",
                 color=card.TEXT, fontsize=13, pad=14, loc="left")
    ax.set_xlabel("номер сделки", color=card.SUB, fontsize=9)
    ax.set_ylabel("баланс, USD", color=card.SUB, fontsize=9)
    ax.tick_params(colors=card.SUB, labelsize=9)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(card.GRID)
    ax.grid(color=card.GRID, lw=0.7, alpha=0.9)
    ax.set_axisbelow(True)

    delta = balance - START
    color = card.UP if delta >= 0 else card.DOWN
    ax.annotate(f"{balance:,.0f} USD  ({delta:+,.0f} · {delta / START * 100:+.2f}%)".replace(",", " "),
                xy=(xs[-1], balance), xytext=(-8, 12), textcoords="offset points",
                color=color, fontsize=11, fontweight="bold", ha="right")

    fig.text(0.01, 0.015, "Не реальная торговля. Прошлые результаты не гарантируют будущих. "
                          "Без учёта спреда и комиссии.",
             color="#5A6068", fontsize=8, ha="left")

    fig.tight_layout(rect=(0, 0.04, 1, 1))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fig.savefig(OUT, facecolor=card.BG, dpi=100)
    plt.close(fig)


def main() -> None:
    chart, rows, balance = build()
    if not rows:
        print("сделок нет")
        return

    eq = [START] + [r["balance"] for r in rows]
    arr = np.array(eq)
    peak = np.maximum.accumulate(arr)
    dd = (arr - peak) / peak * 100.0
    wins = sum(1 for r in rows if r["r"] > 0)

    span = (chart.t[-1] - chart.t[0]) / 86400.0
    print(f"{bot.SYMBOL} {feeds.tf_label(bot.TF)} · {span:.1f} дн. · сделок {len(rows)}")
    print(f"старт {START:,.0f} → финиш {balance:,.2f} USD  "
          f"({balance - START:+,.2f} · {(balance - START) / START * 100:+.2f}%)".replace(",", " "))
    print(f"прибыльных {wins} из {len(rows)} ({wins / len(rows) * 100:.0f}%) · "
          f"макс. просадка {dd.min():.2f}%")
    print()
    print(f"{'дата':16} {'сторона':7} {'вход':>9} {'выход':>9} {'тип':5} {'R':>6} {'%':>7} {'баланс':>10}")
    for r in rows:
        print(f"{r['when']:%d.%m %H:%M}     {r['side']:7} {r['entry']:>9.2f} "
              f"{r['exit_price']:>9.2f} {r['exit']:5} {r['r']:>+6.2f} "
              f"{r['pnl_pct']:>+6.2f}% {r['balance']:>10.0f}")

    draw(rows, balance)
    print(f"\nграфик: {OUT}")

    if len(sys.argv) > 1 and sys.argv[1] == "send":
        import tg
        text = (f"📊 <b>Симуляция {bot.SYMBOL} {feeds.tf_label(bot.TF)}</b> · {span:.0f} дн.\n"
                f"Сделок: {len(rows)} · прибыльных {wins} ({wins / len(rows) * 100:.0f}%)\n"
                f"Баланс: {START:,.0f} → {balance:,.0f} USD ({(balance - START) / START * 100:+.2f}%)\n"
                f"Макс. просадка: {dd.min():.2f}%\n\n"
                f"<i>Бэктест по истории, не реальная торговля. Без спреда и комиссии.</i>").replace(",", " ")
        ok, info = tg.send_photo(OUT, text)
        print("отправка:", ok, info)


if __name__ == "__main__":
    main()
