"""Тесты реалистичной лимитки range: ожидание ретеста и отмена."""
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


def make_frame(retest_bar: int | None = 35, n: int = 60):
    """Флэт выше поддержки 100; на 30-м баре прокол и возврат (сигнал LONG).

    retest_bar — бар, который снова касается 100 (заполнение лимитки),
    None — цена больше не возвращается к уровню.
    """
    o = np.full(n, 101.0)
    h = np.full(n, 102.0)
    l = np.full(n, 101.0)
    c = np.full(n, 101.5)
    o[30], h[30], l[30], c[30] = 101.0, 102.0, 99.5, 101.5
    if retest_bar is not None:
        o[retest_bar], h[retest_bar], l[retest_bar], c[retest_bar] = 101.0, 101.5, 100.0, 101.0
        for j in range(retest_bar + 1, n):
            o[j], h[j], l[j], c[j] = 101.0, 120.0, 100.5, 110.0
    frame = st.Frame(
        t=np.array([T0 + i * TF for i in range(n)], dtype=np.int64),
        o=o, h=h, l=l, c=c, v=np.ones(n),
    )
    zones = [{"bar": i, "levels": [{"kind": "sup", "price": 100.0, "touches": 2}]}
             for i in range(n)]
    return frame, zones


def run(frame, zones, **kw):
    return range_v2.backtest(frame, zones, frame, stop_atr=1.0, tp_r=2.0,
                             max_r=4.0, **kw)


def test_limit_retest_fills_on_return_to_level():
    frame, zones = make_frame(retest_bar=35)
    stats: dict = {}
    trades = run(frame, zones, entry_model="limit_retest", limit_bars=8, stats=stats)
    assert stats["signals"] >= 1
    assert stats.get("fills") == 1 and stats.get("cancelled", 0) == 0
    assert len(trades) == 1
    assert trades[0]["limit"] is True
    assert trades[0]["entry"] == 100.0
    assert trades[0]["entry_i"] == 35


def test_limit_retest_cancels_without_retest():
    frame, zones = make_frame(retest_bar=None)
    stats: dict = {}
    trades = run(frame, zones, entry_model="limit_retest", limit_bars=8, stats=stats)
    assert trades == []
    assert stats.get("fills", 0) == 0
    assert stats.get("cancelled", 0) >= 1
    assert stats["signals"] >= 1


def test_signal_bar_touch_alone_does_not_fill():
    """Прокол на баре сигнала не должен считаться заполнением лимитки."""
    frame, zones = make_frame(retest_bar=None)
    stats: dict = {}
    run(frame, zones, entry_model="limit_retest", limit_bars=1, stats=stats)
    # бар 31 не касается 100, лимитка живёт всего 1 бар — заполнения нет
    assert stats.get("fills", 0) == 0
    assert stats.get("cancelled", 0) >= 1


def test_market_entry_still_works():
    frame, zones = make_frame(retest_bar=35)
    trades = run(frame, zones, entry_model="next_open")
    assert trades and trades[0]["entry_i"] == 30
