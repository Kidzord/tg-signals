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
    trades_all = read_jsonl(TRADES)
    # частичные выходы не пишутся, но на всякий случай фильтруем как bot.py
    closed = [t for t in trades_all if t.get("kind") not in ("tp1", "tp2")]
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
    if verdicts:
        lines.append("⚠️ Кандидаты на отключение: " + ", ".join(verdicts))
        lines.append(f"(сумма R ≤ {DOWNSIDE_R} при ≥ {MIN_TRADES} сделках; "
                     "решение — за тобой: убрать символ из start_all.ps1)")
    else:
        lines.append("✅ Явных балластных символов нет "
                     f"(порог: ≤ {DOWNSIDE_R}R при ≥ {MIN_TRADES} сделках).")
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
