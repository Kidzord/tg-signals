"""MT5Data.frame: повтор при пустой истории (было у EURUSD на старте)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def _rates(n: int = 60):
    dtype = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"),
                      ("low", "f8"), ("close", "f8"), ("tick_volume", "i8")])
    r = np.zeros(n, dtype=dtype)
    r["time"] = np.arange(n) * 900
    r["open"] = r["high"] = r["low"] = r["close"] = 100.0
    return r


class FakeMT5:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def symbol_select(self, symbol, enable):
        return True

    def copy_rates_from_pos(self, symbol, tf, start, count):
        res = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return res


def _data(results) -> bot.MT5Data:
    obj = bot.MT5Data.__new__(bot.MT5Data)
    obj.mt5 = FakeMT5(results)
    obj.offset = lambda symbol: 0
    return obj


def test_frame_retries_empty_history(monkeypatch):
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    monkeypatch.setattr(bot.feeds, "_mt5_timeframe", lambda tf: tf)
    data = _data([None, _rates()])
    frame = data.frame("EURUSD", "15", 60)
    assert len(frame.c) == 60
    assert data.mt5.calls == 2


def test_frame_raises_after_three_empty_attempts(monkeypatch):
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    monkeypatch.setattr(bot.feeds, "_mt5_timeframe", lambda tf: tf)
    data = _data([None, None, None])
    with pytest.raises(RuntimeError, match="баров 0"):
        data.frame("EURUSD", "15", 60)
    assert data.mt5.calls == 3
