"""Публикация статистики за месяц в группу.

    python post_month.py           — собрать картинки, ничего не отправлять
    python post_month.py send      — отправить в Telegram
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
import month
import render as card
import tg

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "month_post")
DAYS = 30


def build():
    data = bot.MT5Data()
    try:
        all_trades = month.collect(data)
        frames = {}
        for sym in {t["symbol"] for t in all_trades}:
            frames[sym] = bot.load_frames(data, sym, "15", int(os.getenv("BOT_BARS", "20000")))
    finally:
        data.close()

    last = max(t["t_out"] for t in all_trades)
    sel = [t for t in all_trades if t["t_in"] >= last - DAYS * 86400]

    # та же симуляция, что в month.py: с дневным риск-гардом, иначе цифры в группе
    # будут расходиться с тем, что реально торгует бот
    pairs, _ = month.balance_curve(sel)
    rows = [{"t": t, "pnl": pnl, "bal": b} for t, pnl, b in pairs]
    final = rows[-1]["bal"] if rows else month.START
    return rows, frames, final


def summary_png(rows, final) -> str:
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "summary.png")

    eq = [month.START] + [r["bal"] for r in rows]
    rs = [r["t"]["r"] for r in rows]
    wins = sum(1 for r in rs if r > 0)

    fig = plt.figure(figsize=(12.8, 7.2), dpi=100, facecolor=card.BG)
    ax = fig.add_axes([0.06, 0.30, 0.55, 0.55])
    ax.set_facecolor(card.BG)
    ax.plot(range(len(eq)), eq, color=card.GOLD, lw=2.2)
    ax.fill_between(range(len(eq)), month.START, eq, color=card.GOLD, alpha=0.12)
    ax.axhline(month.START, color=card.SUB, lw=1, ls=(0, (4, 4)))
    ax.tick_params(colors=card.SUB, labelsize=8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(card.GRID)
    ax.grid(color=card.GRID, lw=0.6, alpha=0.8)
    ax.set_xlabel("номер сделки", color=card.SUB, fontsize=8)

    fig.text(0.06, 0.93, "СТАТИСТИКА · 30 ДНЕЙ", color=card.GOLD, fontsize=17, fontweight="bold")
    fig.text(0.06, 0.885, f"бэктест по истории · {len({r['t']['symbol'] for r in rows})} инструментов · "
                          f"риск {month.RISK_PCT:g}% на сделку · "
                          f"дневной лимит {bot.MAX_DAILY_STOPS} стопов",
             color=card.SUB, fontsize=9)

    fig.text(0.66, 0.90, f"{final:,.0f} $".replace(",", " "), color=card.UP,
             fontsize=26, fontweight="bold")
    fig.text(0.66, 0.855, f"{month.START:,.0f} → {(final / month.START - 1) * 100:+.1f}%".replace(",", " "),
             color=card.SUB, fontsize=11)

    lines = [f"сделок        {len(rows)}",
             f"прибыльных    {wins} ({wins / len(rs) * 100:.0f}%)",
             f"сумма         {sum(rs):+.1f} R"]
    by: dict[str, float] = {}
    for r in rows:
        by[r["t"]["symbol"]] = by.get(r["t"]["symbol"], 0) + r["t"]["r"]
    for s, v in sorted(by.items(), key=lambda x: -x[1]):
        lines.append(f"{s:<12} {v:+.1f} R")
    fig.text(0.66, 0.78, "\n".join(lines), color=card.TEXT, fontsize=9.5, va="top", linespacing=1.7)

    fig.text(0.06, 0.04, "Симуляция по историческим данным, не реальная торговля. "
                         "Прошлые результаты не гарантируют будущих.",
             color="#5A6068", fontsize=8)
    fig.savefig(path, facecolor=card.BG, dpi=100)
    plt.close(fig)
    return path


def trade_png(row, frames, idx: int) -> str:
    t = row["t"]
    sym = t["symbol"]
    chart, htf = frames[sym]
    # найдём индекс входа в этих данных
    i = int(np.searchsorted(chart.t, t["t_in"], side="left"))
    i = min(max(i, 5), len(chart.c) - 1)
    sgn = 1 if t["side"] == "LONG" else -1
    when = dt.datetime.fromtimestamp(t["t_in"], dt.timezone.utc)
    sig = card.Signal(side=t["side"], symbol=sym, tf="15",
                      entry=float(t["entry"]), sl=float(t["stop"]),
                      tp1=float(t["target"]), when=when)
    lo = max(0, i - 119)
    bars = [feeds.Bar(dt.datetime.fromtimestamp(float(chart.t[j]), dt.timezone.utc),
                      float(chart.o[j]), float(chart.h[j]), float(chart.l[j]), float(chart.c[j]))
            for j in range(lo, i + 1)]
    path = os.path.join(OUT, f"{idx:02d}_{sym}_{t['side']}.png")
    card.render_card(sig, bars, "MT5", False, path)
    return path


def main() -> None:
    rows, frames, final = build()
    rs = [r["t"]["r"] for r in rows]
    wins = sum(1 for r in rs if r > 0)
    print(f"{len(rows)} сделок · прибыльных {wins} · {sum(rs):+.1f}R · "
          f"{month.START:.0f} → {final:.0f}$")

    os.makedirs(OUT, exist_ok=True)
    summary = summary_png(rows, final)

    # подбираем примеры: 3 прибыльных, 2 убыточных, из разных инструментов
    picked, seen = [], set()
    for want_win, need in ((True, 3), (False, 2)):
        got = 0
        for r in rows:
            key = r["t"]["symbol"]
            if (r["t"]["r"] > 0) == want_win and key not in seen and got < need:
                picked.append(r)
                seen.add(key)
                got += 1
    cards = [(trade_png(r, frames, k), r) for k, r in enumerate(picked, 1)]
    print("карточки:", len(cards))

    if len(sys.argv) > 1 and sys.argv[1] == "send":
        caption = (f"📊 <b>Статистика за 30 дней</b>\n"
                   f"{len({r['t']['symbol'] for r in rows})} инструментов · {len(rows)} сделок · {wins} прибыльных "
                   f"({wins / len(rs) * 100:.0f}%)\n"
                   f"Баланс: {month.START:,.0f} → {final:,.0f} $ "
                   f"({(final / month.START - 1) * 100:+.1f}%) при риске 0.5%\n\n"
                   f"<i>Бэктест по истории, не реальная торговля.</i>").replace(",", " ")
        ok, info = tg.send_photo(summary, caption)
        print("сводка:", ok, info)
        for path, r in cards:
            t_ = r["t"]
            cap = (f"<i>{t_['symbol']} · {'LONG' if t_['side'] == 'LONG' else 'SHORT'}</i>\n"
                   f"Результат: <b>{t_['r']:+.2f}R</b>")
            ok, info = tg.send_photo(path, cap)
            print("карточка:", os.path.basename(path), ok, info)


if __name__ == "__main__":
    main()
