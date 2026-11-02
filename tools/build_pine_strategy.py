#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Собирает strategy()-двойник из индикатора KidzordFx.

Pine не умеет импортировать код, поэтому единственный способ не разъезжаться
двум файлам — генерировать второй из первого. Логика (вход, стоп, TP, ведение)
остаётся в одном месте; здесь меняется только заголовок скрипта, убираются
alertcondition() и добавляется драйвер ордеров.

    python tools/build_pine_strategy.py

Читает  pine/KidzordFx_v1_6.pine
Пишет   pine/KidzordFx_v1_6_strategy.pine
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "pine" / "KidzordFx_v1_6.pine"
DST = ROOT / "pine" / "KidzordFx_v1_6_strategy.pine"

STRATEGY_HEADER = (
    'strategy("KidzordFx V1.6 STRAT", "KidzordFxS", overlay = true, '
    "max_labels_count = 500, max_lines_count = 500, max_boxes_count = 500, "
    "default_qty_type = strategy.percent_of_equity, default_qty_value = 100, "
    "initial_capital = 10000, commission_type = strategy.commission.percent, "
    "commission_value = 0.0, slippage = 1, calc_on_every_tick = false, "
    "process_orders_on_close = true)"
)

DRIVER = """
// ═══════════════════ STRATEGY-ДРАЙВЕР ═══════════════════
// Сгенерировано tools/build_pine_strategy.py из KidzordFx_v1_3.pine — руками
// этот файл не правят. Правила те же, что у индикатора: вход, стоп, TP и
// ведение берутся из его состояния (stDir/stStop/stTp1-3). Отличие одно:
// исполняет движок TradingView, поэтому в отчёте учитываются комиссия и
// проскальзывание из заголовка strategy() (Settings → Properties).
if goLong
    strategy.entry("L", strategy.long)
if goShort
    strategy.entry("S", strategy.short)
// Выходы держим выставленными каждый бар: strategy.exit с тем же id заменяет
// предыдущую заявку, поэтому трейлинг подхватывается автоматически.
if strategy.position_size > 0 and stDir == 1
    if p1PctSafe > 0
        strategy.exit("L1", "L", qty_percent = p1PctSafe, stop = stStop, limit = stTp1)
    if p2PctSafe > 0
        strategy.exit("L2", "L", qty_percent = p2PctSafe, stop = stStop, limit = stTp2)
    if p3PctSafe > 0
        strategy.exit("L3", "L", qty_percent = p3PctSafe, stop = stStop, limit = stTp3)
if strategy.position_size < 0 and stDir == -1
    if p1PctSafe > 0
        strategy.exit("S1", "S", qty_percent = p1PctSafe, stop = stStop, limit = stTp1)
    if p2PctSafe > 0
        strategy.exit("S2", "S", qty_percent = p2PctSafe, stop = stStop, limit = stTp2)
    if p3PctSafe > 0
        strategy.exit("S3", "S", qty_percent = p3PctSafe, stop = stStop, limit = stTp3)
// Сделка закрыта по стопу, трейлингу или времени — снимаем остаток.
if strategy.position_size != 0 and stDir == 0
    strategy.close_all(comment = "выход по стопу/времени")
"""


def main() -> int:
    if not SRC.exists():
        print(f"нет исходника: {SRC}")
        return 1

    lines = SRC.read_text(encoding="utf-8").split("\n")
    out: list[str] = []
    header_done = False
    dropped = 0

    for line in lines:
        stripped = line.lstrip()
        if not header_done and stripped.startswith("indicator("):
            out.append(STRATEGY_HEADER)
            header_done = True
            continue
        if stripped.startswith("alertcondition("):
            dropped += 1
            continue
        out.append(line)

    if not header_done:
        print("не нашёл строку indicator(...) — файл изменился")
        return 1

    text = "\n".join(out).rstrip("\n") + "\n" + DRIVER
    DST.write_text(text, encoding="utf-8", newline="\n")

    print(f"источник : {SRC.name} ({len(lines)} строк)")
    print(f"результат: {DST.name} ({len(text.splitlines())} строк)")
    print(f"alertcondition удалено: {dropped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
