"""Диагностика classic: пересборка старших ТФ из M15 и сдвиг сервера."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import strategy as st  # noqa: E402
from tools import classic_feed_diag as d  # noqa: E402


def _frame(times):
    n = len(times)
    c = np.arange(n, dtype=float) + 100
    return st.Frame(t=np.array(times, dtype="int64"), o=c.copy(), h=c + 2,
                    l=c - 2, c=c, v=np.ones(n))


def test_aggregate_h1_keeps_ohlc():
    # четыре M15-бара одного часа: 10:00–10:45
    t0 = 1_800_000_000 - (1_800_000_000 % 3600)
    chart = _frame([t0, t0 + 900, t0 + 1800, t0 + 2700])
    h1 = d.aggregate(chart, 3600)
    assert len(h1.t) == 1
    assert h1.t[0] == t0
    assert h1.o[0] == 100.0 and h1.c[0] == 103.0
    assert h1.h[0] == 105.0 and h1.l[0] == 98.0
    assert h1.v[0] == 4.0


def test_shift_moves_timestamps_only():
    f = _frame([100, 200])
    out = d.shift(f, 2)
    assert list(out.t) == [100 + 7200, 200 + 7200]
    assert list(out.c) == list(f.c)
