"""Mean-reversion ядро: вход на следующем баре, цель/стоп/time-exit, издержки."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mr_probe import (  # noqa: E402
    Bar, _month_slices, backtest, rolling_mean_std,
)

STEP = 900
T0 = int(dt.datetime(2026, 1, 1, tzinfo=dt.UTC).timestamp())


def make_bars(closes: list[float], t0: int = T0) -> list[Bar]:
    out: list[Bar] = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append(Bar(t0 + i * STEP, o, max(o, c) + 0.1, min(o, c) - 0.1, c))
        prev = c
    return out


OSC = [100.0, 100.6] * 10
N, K = 5, 1.5


def first_signal_index(closes: list[float], n: int, k: float) -> int:
    mean, std = rolling_mean_std(closes, n)
    for i in range(len(closes)):
        if mean[i] is not None and std[i] and std[i] > 0:
            z = (closes[i] - mean[i]) / std[i]
            if z <= -k:
                return i
    raise AssertionError("нет сигнала в синтетике")


def test_entry_is_next_bar_and_target_is_mean():
    closes = OSC + [99.0, 98.0, 99.0, 100.0, 101.0]
    bars = make_bars(closes)
    trades = backtest(bars, N, K, 2.0, 10, 0.0).trades
    assert trades, "сделка не открылась"
    tr = trades[0]
    i = first_signal_index(closes, N, K)
    assert tr.side == 1
    assert tr.ts_entry == bars[i + 1].t
    assert tr.entry == bars[i + 1].o
    assert tr.reason == "target"
    assert tr.gross_r > 0


def test_stop_caps_loss_at_one_r():
    closes = OSC + [99.0, 97.0, 95.0, 93.0, 91.0, 89.0]
    bars = make_bars(closes)
    trades = backtest(bars, N, K, 2.0, 10, 0.0).trades
    long = [t for t in trades if t.side == 1]
    assert long
    tr = long[0]
    assert tr.reason in ("stop", "stop_gap")
    assert tr.gross_r <= -1.0 + 1e-9


def test_time_exit_closes_at_bar_close():
    closes = OSC + [99.0, 98.5, 98.5, 98.5, 98.5, 98.5]
    bars = make_bars(closes)
    trades = backtest(bars, N, K, 2.0, 1, 0.0).trades
    long = [t for t in trades if t.side == 1]
    assert long
    tr = long[0]
    assert tr.reason == "time"
    assert tr.exit == 98.5  # close бара, на котором истёк max_bars
    assert abs(tr.gross_r - (98.5 - 99.0) / tr.risk) < 1e-9


def test_costs_subtract_from_gross_r():
    closes = OSC + [99.0, 98.0, 99.0, 100.0, 101.0]
    bars = make_bars(closes)
    free = [t for t in backtest(bars, N, K, 2.0, 10, 0.0).trades if t.side == 1][0]
    paid = [t for t in backtest(bars, N, K, 2.0, 10, 0.5).trades if t.side == 1][0]
    expected = max(0.05, 0.5 / paid.risk)
    assert abs((paid.gross_r - paid.net_r) - expected) < 1e-9
    assert paid.net_r < free.net_r


def test_er_filter_can_block_all_trades():
    closes = OSC + [99.0, 98.0, 99.0, 100.0, 101.0]
    bars = make_bars(closes)
    blocked = backtest(bars, N, K, 2.0, 10, 0.0, er_max=0.0).trades
    allowed = backtest(bars, N, K, 2.0, 10, 0.0, er_max=1.0).trades
    assert blocked == []
    assert allowed


def test_month_slices_are_contiguous():
    t_jan = int(dt.datetime(2026, 1, 20, tzinfo=dt.UTC).timestamp())
    t_feb = int(dt.datetime(2026, 2, 2, tzinfo=dt.UTC).timestamp())
    bars = [Bar(t_jan + i * 60, 1, 1, 1, 1) for i in range(3)]
    bars += [Bar(t_feb + i * 60, 1, 1, 1, 1) for i in range(2)]
    assert _month_slices(bars) == [("2026-01", 0, 3), ("2026-02", 3, 5)]
