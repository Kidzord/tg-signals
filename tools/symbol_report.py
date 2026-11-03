#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Разбор живых сделок ботов по символам: кто тянет канал вниз.

Читает trades.jsonl (закрытые сделки, пишут bot.py) и signals_queue.jsonl
(попытки постов) из корня проекта. Периоды: последние 7 дней и всё время.
Вердикт: символ с отрицательной суммой R при >= MIN_TRADES сделок —
кандидат на отключение (убрать BOT_SYMBOL из start_all.ps1).

Запуск:
    python tools/symbol_report.py           # отчёт в консоль
    python tools/symbol_report.py --send    # + отправка владельцу в Telegram
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRADES = ROOT / "trades.jsonl"
QUEUE = ROOT / "signals_queue.jsonl"
MIN_TRADES = 20          # меньше сделок — вердикт «мало данных», не «отключать»
DOWNSIDE_R = -3.0        # сумма R ниже этого при достатке сделок = кандидат

sys.path.insert(0, str(ROOT))

import config as cfg  # noqa: E402

# Форвард-контроль: последние N сделок символа против порога.
FORWARD_WINDOW = int(getattr(cfg, "FORWARD_WINDOW", 20))
FORWARD_DEGRADE_R = float(getattr(cfg, "FORWARD_DEGRADE_R", -3.0))


def read_jsonl(path: Path, since: float = 0.0) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if float(rec.get("ts", 0)) >= since:
                out.append(rec)
    return out


def agg(trades: list[dict]) -> dict:
    n = len(trades)
    r = sum(float(t.get("r", 0)) for t in trades)
    wins = sum(1 for t in trades if float(t.get("r", 0)) > 0)
    return {"n": n, "r": r, "wr": 100.0 * wins / n if n else 0.0}


def load_closed(path: Path = TRADES) -> list[dict]:
    """Закрытые сделки: без частичных выходов и дублей, по времени."""
    rows = [t for t in read_jsonl(path)
            if t.get("kind") not in ("tp1", "tp2")]
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for rec in rows:
        tid = str(rec.get("trade_id") or "")
        key = (tid, str(rec.get("kind") or "")) if tid else None
        if key is not None and key in seen:
            continue
        if key is not None:
            seen.add(key)
        out.append(rec)
    out.sort(key=lambda r: float(r.get("ts", 0)))
    return out


def recent_by_symbol(closed: list[dict],
                     window: int | None = None) -> dict[str, dict]:
    """По каждому символу — агрегат последних window сделок."""
    window = FORWARD_WINDOW if window is None else window
    by: dict[str, list[dict]] = {}
    for rec in closed:
        by.setdefault(str(rec.get("symbol") or "?"), []).append(rec)
    out: dict[str, dict] = {}
    for sym, rows in by.items():
        a = agg(rows[-window:])
        a["window"] = window
        a["total"] = len(rows)
        out[sym] = a
    return out


def degraded(closed: list[dict], window: int | None = None,
             limit: float | None = None) -> list[dict]:
    """Символы, у которых последние window сделок дали <= limit R."""
    window = FORWARD_WINDOW if window is None else window
    limit = FORWARD_DEGRADE_R if limit is None else limit
    out: list[dict] = []
    for sym, a in recent_by_symbol(closed, window).items():
        if a["n"] >= window and a["r"] <= limit:
            out.append({"symbol": sym, "n": a["n"], "r": a["r"],
                        "window": window})
    return sorted(out, key=lambda d: d["r"])


def fmt_row(sym: str, a: dict) -> str:
    mark = ""
    if a["n"] >= MIN_TRADES:
        mark = " ← кандидат на отключение" if a["r"] <= DOWNSIDE_R else ""
    elif a["n"] > 0:
        mark = " (мало данных)"
    wr = f"{a['wr']:.0f}%"
    return f"  {sym:<8} {a['n']:>4} сд · {a['r']:+7.2f}R · win {wr}{mark}"


def build_report(now: float | None = None) -> str:
    now = now or time.time()
    week_ago = now - 7 * 86400
    closed = load_closed()
    trades_week = [t for t in closed if float(t.get("ts", 0)) >= week_ago]
    sigs_all = read_jsonl(QUEUE)
    sigs_week = [s for s in sigs_all if float(s.get("ts", 0)) >= week_ago]

    if not closed:
        return ("Разбор ботов: живых сделок пока нет — trades.jsonl пуст. "
                "Отчёт станет содержательным, когда накопятся сделки.")

    symbols = sorted({t.get("symbol", "?") for t in closed})
    lines = ["📊 Разбор ботов по символам", ""]

    lines.append("Последние 7 дней:")
    if not trades_week:
        lines.append("  сделок не было")
    by_week: dict[str, list] = {}
    for t in trades_week:
        by_week.setdefault(t.get("symbol", "?"), []).append(t)
    total_w = agg(trades_week)
    for sym in sorted(by_week):
        lines.append(fmt_row(sym, agg(by_week[sym])))
    if trades_week:
        lines.append(f"  ИТОГО     {total_w['n']:>4} сд · {total_w['r']:+7.2f}R · win {total_w['wr']:.0f}%")
    lines.append(f"  сигналов за 7 дней: {len(sigs_week)}")
    lines.append("")

    lines.append("За всё время:")
    by_all: dict[str, list] = {}
    for t in closed:
        by_all.setdefault(t.get("symbol", "?"), []).append(t)
    verdicts: list[str] = []
    for sym in symbols:
        a = agg(by_all[sym])
        lines.append(fmt_row(sym, a))
        if a["n"] >= MIN_TRADES and a["r"] <= DOWNSIDE_R:
            verdicts.append(sym)
    total = agg(closed)
    lines.append(f"  ИТОГО     {total['n']:>4} сд · {total['r']:+7.2f}R · win {total['wr']:.0f}%")

    lines.append("")
    lines.append(f"Форвард (последние {FORWARD_WINDOW} сделок по символу):")
    recent = recent_by_symbol(closed)
    degraded_syms: list[str] = []
    for sym in symbols:
        a = recent.get(sym)
        if not a:
            continue
        bad = a["n"] >= FORWARD_WINDOW and a["r"] <= FORWARD_DEGRADE_R
        if bad:
            degraded_syms.append(sym)
        mark = (" ← деградация" if bad
                else " (мало данных)" if a["n"] < FORWARD_WINDOW else "")
        lines.append(f"  {sym:<8} {a['n']:>3}/{FORWARD_WINDOW} сд · "
                     f"{a['r']:+6.2f}R{mark}")

    lines.append("")
    actions = sorted(set(verdicts) | set(degraded_syms))
    if actions:
        lines.append("⚠️ Кандидаты на отключение: " + ", ".join(actions))
        if degraded_syms:
            lines.append(f"Деградация: {', '.join(degraded_syms)} — последние "
                         f"{FORWARD_WINDOW} сделок дали ≤ {FORWARD_DEGRADE_R}R.")
        if verdicts:
            lines.append(f"Исторически: {', '.join(verdicts)} — сумма R ≤ "
                         f"{DOWNSIDE_R} при ≥ {MIN_TRADES} сделках.")
        lines.append("Решение — за тобой: убрать символ из start_all.ps1.")
    else:
        lines.append("✅ Явных балластных символов нет "
                     f"(пороги: ≤ {DOWNSIDE_R}R при ≥ {MIN_TRADES} сделках "
                     f"или ≤ {FORWARD_DEGRADE_R}R за {FORWARD_WINDOW} сделок).")
    return "\n".join(lines)


def main() -> int:
    report = build_report()
    print(report)
    if "--send" in sys.argv:
        import config as cfg
        import tg
        if not cfg.OWNER_CHAT_ID:
            print("OWNER_CHAT_ID пуст — не отправляю")
            return 1
        ok, err = tg.send_message(report, chat_id=cfg.OWNER_CHAT_ID)
        print("отправлено" if ok else f"ошибка отправки: {err}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
