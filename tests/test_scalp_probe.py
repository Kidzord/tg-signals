"""Скальпинг-ядро: вход на следующем баре, TP/SL/time-exit, издержки, сигналы."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.scalp_probe import (  # noqa: E402
    Bar, _donchian, _fade, atr_series, backtest,
)

STEP = 300
T0 = int(dt.datetime(2026, 1, 1, tzinfo=dt.UTC).timestamp())
CFG = {"n": 5, "tp_atr": 1.5, "sl_atr": 1.0, "max_bars": 10}


def make_bars(closes: list[float], t0: int = T0) -> list[Bar]:
    out: list[Bar] = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append(Bar(t0 + i * STEP, o, max(o, c) + 0.1, min(o, c) - 0.1, c))
        prev = c
    return out


def test_donchian_signal_on_breakout():
    bars = make_bars([100.0] * 10 + [101.5])
    sig, _ = _donchian(bars, {"n": 5}, None)
    assert sig(5) == 0
    assert sig(10) == 1


def test_fade_signal_on_impulse_candle():
    bars = make_bars([100.0] * 20 + [104.9])
    sig, _ = _fade(bars, {"k": 1.5}, atr_series(bars, 14))
    assert sig(20) == -1


def test_entry_next_bar_and_target():
    closes = [100.0] * 20 + [101.5, 103.2]
    bars = make_bars(closes)
    tr = backtest(bars, "donchian", CFG, 0.0).trades[0]
    assert tr.side == 1
    assert tr.ts_entry == bars[21].t
    assert tr.entry == bars[21].o
    assert tr.reason == "target"
    assert tr.gross_r > 0


def test_stop_caps_loss():
    closes = [100.0] * 20 + [101.5, 99.0]
    bars = make_bars(closes)
    tr = backtest(bars, "donchian", CFG, 0.0).trades[0]
    assert tr.reason in ("stop", "stop_gap")
    assert tr.gross_r <= -1.0 + 1e-9


def test_time_exit_at_bar_close():
    closes = [100.0] * 20 + [101.5, 101.6, 101.7]
    bars = make_bars(closes)
    cfg = {**CFG, "max_bars": 1}
    tr = backtest(bars, "donchian", cfg, 0.0).trades[0]
    assert tr.reason == "time"
    assert tr.exit == 101.7


def test_costs_subtract_from_gross():
    closes = [100.0] * 20 + [101.5, 103.2]
    bars = make_bars(closes)
    free = backtest(bars, "donchian", CFG, 0.0).trades[0]
    paid = backtest(bars, "donchian", CFG, 0.5).trades[0]
    expected = max(0.05, 0.5 / paid.risk)
    assert abs((paid.gross_r - paid.net_r) - expected) < 1e-9
    assert paid.net_r < free.net_r
