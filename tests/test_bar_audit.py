"""Аудит баров: агрегация M1→M15 и сравнение двух фидов (без MT5)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import bar_audit as ba  # noqa: E402


def _m1(start: int, closes: list[float]) -> list[ba.Bar]:
    out = []
    for i, c in enumerate(closes):
        t = start + i * 60
        out.append(ba.Bar(t, c, c + 1, c - 1, c, 1.0))
    return out


def test_aggregate_keeps_first_open_last_close_and_extremes():
    bars = _m1(0, [10, 12, 9, 11])
    tf = ba.aggregate(bars, 15)
    assert len(tf) == 1
    b = tf[0]
    assert (b.t, b.o, b.h, b.l, b.c, b.v) == (0, 10, 13, 8, 11, 4.0)


def test_best_shift_finds_utc_offset():
    native = _m1(0, [10, 11, 12])
    other = [ba.Bar(b.t + 3 * 3600, b.o, b.h, b.l, b.c) for b in native]
    assert ba.best_shift(native, other) == -3


def test_compare_flags_only_native_and_close_mismatch():
    native = _m1(0, [10, 11, 12, 13])
    other = _m1(0, [10, 11, 99])          # последний бар за общим окном
    res = ba.compare(native, other, point=0.01, tf_min=1)
    assert len(res["edge_native"]) == 1
    assert len(res["only_native"]) == 0
    assert res["close_bad"]


def test_compare_separates_inner_gap_from_edge():
    native = _m1(0, [10, 11, 12, 13])
    other = [native[0], native[1], native[3]]   # пропущен средний бар
    res = ba.compare(native, other, point=0.01, tf_min=1, shift_hours=0)
    assert res["only_native"] == [120]
    assert len(res["edge_native"]) == 0


def test_compare_detects_gap_and_zero_range():
    native = _m1(0, [10, 11]) + _m1(15 * 60, [12])
    native[0].h, native[0].l = 10.0, 10.0
    res = ba.compare(native, native, point=0.01, tf_min=1, shift_hours=0)
    assert res["max_gap_min"] == 14
    assert len(res["unexpected_gaps"]) == 1
    assert res["zero_range"] == 1 and res["zero_times"] == [0]


def test_repeated_gap_at_same_hour_is_regular():
    # брокер закрывает золото на час каждую ночь: паузы повторяются в 00:00 и 01:00
    bars = [ba.Bar(t, 10, 11, 9, 10)
            for t in (0, 3600, 86400, 90000, 172800)]
    res = ba.compare(bars, bars, point=0.01, tf_min=1, shift_hours=0)
    assert len(res["regular_gaps"]) == 4
    assert len(res["unexpected_gaps"]) == 0


def test_read_csv_accepts_epoch_and_iso(tmp_path):
    path = tmp_path / "bars.csv"
    path.write_text(
        "time,open,high,low,close,volume\n"
        "1700000000,1.1,1.2,1.0,1.15,5\n"
        "2023-11-14T22:15:00+00:00,1.15,1.3,1.1,1.25,6\n",
        encoding="utf-8")
    bars = ba.read_csv(str(path))
    assert len(bars) == 2 and bars[0].c == 1.15 and bars[1].c == 1.25


def test_td_symbol_mapping():
    assert ba.td_symbol("EURUSD") == "EUR/USD"
    assert ba.td_symbol("XAUUSD") == "XAU/USD"
    assert ba.td_symbol("US500") == "US500"
