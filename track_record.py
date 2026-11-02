"""Публичный трек-рекорд: считается по trades.jsonl, который пишет bot.py.

Публичные каналы обычно показывают «винрейт» и сумму R. Для доверия нужны
метрики, которые нельзя подкрутить: profit factor, максимальная просадка,
серия убытков, разбивка по инструментам и стратегиям. Здесь они считаются
по одному журналу и рисуются в одну equity-картинку.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import defaultdict
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
TRADES_PATH = os.path.join(HERE, "trades.jsonl")
OUT_DIR = os.path.join(HERE, "renders")
DISCLAIMER = os.getenv("DISCLAIMER", "Не является инвестиционной рекомендацией.").strip()


def load_trades(days: int | None = None) -> list[dict[str, Any]]:
    """Закрытые сделки из журнала. Частичные tp1/tp2 пропускаем."""
    cutoff = 0.0 if days is None else time.time() - days * 86400
    rows: list[dict[str, Any]] = []
    if not os.path.exists(TRADES_PATH):
        return rows
    try:
        with open(TRADES_PATH, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    ts = float(d["ts"])
                    r = float(d.get("total_r", d.get("r", 0)) or 0)
                except (ValueError, KeyError, TypeError):
                    continue
                if ts < cutoff or str(d.get("kind", "")) in ("tp1", "tp2"):
                    continue
                rows.append({
                    "ts": ts, "r": r, "symbol": str(d.get("symbol", "")),
                    "side": str(d.get("side", "")), "kind": str(d.get("kind", "")),
                    "strategy": str(d.get("strategy", "")),
                })
    except OSError:
        pass
    rows.sort(key=lambda x: x["ts"])
    return rows


def metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    rs = [float(t["r"]) for t in trades]
    n = len(rs)
    if n == 0:
        return {"n": 0, "wins": 0, "winrate": 0.0, "sum_r": 0.0, "avg_r": 0.0,
                "pf": 0.0, "max_dd_r": 0.0, "best_r": 0.0, "worst_r": 0.0,
                "losing_streak": 0, "by_symbol": {}, "by_strategy": {},
                "equity": [], "max_dd_pct": 0.0}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0)

    equity: list[float] = []
    acc = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = 0
    max_streak = 0
    for r in rs:
        acc += r
        equity.append(acc)
        peak = max(peak, acc)
        max_dd = max(max_dd, peak - acc)
        if r <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    by_symbol: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "r": 0.0, "wins": 0})
    by_strategy: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "r": 0.0, "wins": 0})
    for t in trades:
        for key, bucket in (("symbol", by_symbol), ("strategy", by_strategy)):
            name = t.get(key) or "—"
            bucket[name]["n"] += 1
            bucket[name]["r"] += t["r"]
            if t["r"] > 0:
                bucket[name]["wins"] += 1

    return {
        "n": n, "wins": len(wins), "winrate": len(wins) / n * 100.0,
        "sum_r": acc, "avg_r": acc / n, "pf": pf, "max_dd_r": max_dd,
        "best_r": max(rs), "worst_r": min(rs), "losing_streak": max_streak,
        "by_symbol": dict(by_symbol), "by_strategy": dict(by_strategy),
        "equity": equity, "max_dd_pct": (max_dd / max(peak, 1e-9) * 100.0) if peak > 0 else 0.0,
    }


def short_line(days: int = 30, label: str = "") -> str:
    """Компактная строка для /stats в premium-боте."""
    m = metrics(load_trades(days))
    name = label or f"{days} дней"
    if m["n"] == 0:
        return f"<b>{name}</b>: сделок не было."
    pf = "∞" if math.isinf(m["pf"]) else f"{m['pf']:.2f}"
    return (f"<b>{name}</b>: {m['n']} сд. · {m['winrate']:.0f}% · "
            f"{m['sum_r']:+.1f}R · PF {pf} · DD {m['max_dd_r']:.1f}R")


def text_report(days: int = 30, title: str | None = None) -> str:
    trades = load_trades(days)
    m = metrics(trades)
    head = title or f"📈 <b>Трек-рекорд · {days} дней</b>"
    if m["n"] == 0:
        return head + "\n\nЗакрытых сделок за период не было."
    pf = "∞" if math.isinf(m["pf"]) else f"{m['pf']:.2f}"
    lines = [
        head,
        "",
        f"Сделок: <b>{m['n']}</b> · винрейт <b>{m['winrate']:.0f}%</b> · "
        f"profit factor <b>{pf}</b>",
        f"Итог: <b>{m['sum_r']:+.1f}R</b> · средняя <b>{m['avg_r']:+.2f}R</b> · "
        f"макс. просадка <b>{m['max_dd_r']:.1f}R</b>",
        f"Лучшая {m['best_r']:+.1f}R · худшая {m['worst_r']:+.1f}R · "
        f"серия убытков до {m['losing_streak']}",
    ]
    sym = sorted(m["by_symbol"].items(), key=lambda kv: -kv[1]["r"])[:6]
    if sym:
        lines.append("")
        lines.append("<b>По инструментам</b>")
        for name, v in sym:
            lines.append(f"  {name}: {v['r']:+.1f}R · {int(v['n'])} сд. · "
                         f"{v['wins'] / max(v['n'], 1) * 100:.0f}%")
    stg = sorted(m["by_strategy"].items(), key=lambda kv: -kv[1]["r"])[:4]
    if stg and any(k not in ("", "—") for k, _ in stg):
        lines.append("")
        lines.append("<b>По стратегиям</b>")
        for name, v in stg:
            if name in ("", "—"):
                continue
            lines.append(f"  {name}: {v['r']:+.1f}R · {int(v['n'])} сд.")
    lines += ["", f"<i>{DISCLAIMER}</i>"]
    return "\n".join(lines)


def render_equity(days: int | None = 90, out_path: str | None = None) -> str:
    """Нарисовать equity curve и просадку. Возвращает путь к PNG."""
    import datetime as dt

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import dates as mdates

    trades = load_trades(days)
    m = metrics(trades)
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = out_path or os.path.join(OUT_DIR, "track_record.png")
    if not trades:
        return ""

    xs = [dt.datetime.fromtimestamp(t["ts"]) for t in trades]
    eq = m["equity"]
    peak = 0.0
    dd = []
    for v in eq:
        peak = max(peak, v)
        dd.append(v - peak)
    pf_str = "∞" if math.isinf(m["pf"]) else f"{m['pf']:.2f}"

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 6.6), dpi=110, sharex=True,
        gridspec_kw={"height_ratios": [2.4, 1.0], "hspace": 0.08},
    )
    fig.patch.set_facecolor("#12161f")
    for ax in (ax1, ax2):
        ax.set_facecolor("#12161f")
        ax.tick_params(colors="#9aa4b2", labelsize=9)
        for spine in ax.spines.values():
            spine.set_color("#2a3140")
        ax.grid(color="#242b39", linewidth=0.8, alpha=0.7)

    ax1.plot(xs, eq, color="#4cc9f0", linewidth=2.2, zorder=3)
    ax1.fill_between(xs, 0, eq, color="#4cc9f0", alpha=0.12)
    ax1.axhline(0, color="#6b7280", linewidth=1)
    ax1.set_ylabel("R", color="#9aa4b2")
    ax1.set_title(
        f"KidzordFX track record · {len(trades)} trades · "
        f"{m['sum_r']:+.1f}R · PF {pf_str}",
        color="#e5e7eb", fontsize=12, loc="left", pad=12,
    )
    ax1.scatter(xs[-1], eq[-1], color="#4cc9f0", s=30, zorder=4)
    ax1.annotate(f"{eq[-1]:+.1f}R", (xs[-1], eq[-1]), textcoords="offset points",
                 xytext=(8, 0), color="#e5e7eb", fontsize=10, va="center")

    ax2.fill_between(xs, dd, 0, color="#f87171", alpha=0.35)
    ax2.plot(xs, dd, color="#f87171", linewidth=1.4)
    ax2.set_ylabel("DD, R", color="#9aa4b2")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    fig.autofmt_xdate(rotation=0, ha="center")

    fig.text(0.01, 0.01, DISCLAIMER, color="#6b7280", fontsize=8)
    fig.savefig(out_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return out_path


def post(days: int = 30, token: str | None = None, chat_id: str | None = None) -> bool:
    """Отправить текстовый отчёт (и equity-картинку, если получилась)."""
    import tg

    text = text_report(days)
    img = render_equity(days)
    if img:
        ok, info = tg.send_photo(img, text, token=token, chat_id=chat_id)
    else:
        ok, info = tg.send_message(text, token=token, chat_id=chat_id)
    print("трек-рекорд:", ok, info)
    return ok
