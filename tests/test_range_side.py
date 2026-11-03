"""Тесты фильтра стороны для range-стратегии."""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import range_v2  # noqa: E402
import strategy as st  # noqa: E402

T0 = 1_700_000_000
TF = 900


def side_ok_cases():
    assert range_v2.side_ok("SHORT", "short")
    assert not range_v2.side_ok("LONG", "short")
    assert range_v2.side_ok("LONG", "long")
    assert not range_v2.side_ok("SHORT", "long")
    assert range_v2.side_ok("LONG", "both") and range_v2.side_ok("SHORT", "both")
    assert range_v2.side_ok("LONG", "") and range_v2.side_ok("SHORT", "")


def make_case():
    """60 плоских баров; на 30-м цена касается сопротивления 104 и закрывается ниже."""
    n = 60
    o = np.full(n, 100.0)
    h = np.full(n, 100.5)
    l = np.full(n, 99.5)
    c = np.full(n, 100.0)
    o[30], h[30], l[30], c[30] = 100.0, 105.0, 99.0, 99.5
    frame = st.Frame(
        t=np.array([T0 + i * TF for i in range(n)], dtype=np.int64),
        o=o, h=h, l=l, c=c, v=np.ones(n),
    )
    zones = [{"bar": i, "levels": [{"kind": "res", "price": 104.0, "touches": 2}]}
             for i in range(n)]
    return frame, zones


def test_side_ok():
    side_ok_cases()


def test_only_short_allowed_keeps_shorts_and_drops_longs():
    frame, zones = make_case()
    both = range_v2.backtest(frame, zones, frame, stop_atr=1.0, tp_r=2.0,
                             max_r=4.0, side_allow="both")
    assert both and all(t["side"] == "SHORT" for t in both)

    short = range_v2.backtest(frame, zones, frame, stop_atr=1.0, tp_r=2.0,
                              max_r=4.0, side_allow="short")
    assert len(short) == len(both)

    long_only = range_v2.backtest(frame, zones, frame, stop_atr=1.0, tp_r=2.0,
                                  max_r=4.0, side_allow="long")
    assert long_only == []
