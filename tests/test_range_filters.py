"""Тесты доп. фильтров range: безубыток после N R и подтверждение входа."""
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


def make_frame():
    """Флэт, на 30-м баре прокол поддержки 100 и возврат (сигнал LONG)."""
    n = 60
    o = np.full(n, 101.0)
    h = np.full(n, 102.0)
    l = np.full(n, 101.0)
    c = np.full(n, 101.5)
    o[30], h[30], l[30], c[30] = 101.0, 102.0, 99.5, 101.5
    # вход на 31-м, рост до +1R на 32-м, откат ниже исходного стопа на 33-м
    o[31], h[31], l[31], c[31] = 101.0, 101.5, 100.5, 101.2
    o[32], h[32], l[32], c[32] = 101.2, 104.0, 101.5, 103.0
    o[33], h[33], l[33], c[33] = 103.0, 103.2, 98.0, 99.0
    frame = st.Frame(
        t=np.array([T0 + i * TF for i in range(n)], dtype=np.int64),
        o=o, h=h, l=l, c=c, v=np.ones(n),
    )
    zones = [{"bar": i, "levels": [{"kind": "sup", "price": 100.0, "touches": 2}]}
             for i in range(n)]
    return frame, zones


def run(frame, zones, **kw):
    return range_v2.backtest(frame, zones, frame, stop_atr=1.0, tp_r=10.0,
                             max_r=20.0, **kw)


def test_breakeven_after_one_r_cuts_loss():
    frame, zones = make_frame()
    no_be = run(frame, zones)
    with_be = run(frame, zones, be_after_r=1.0)
    assert no_be and with_be
    assert no_be[0]["r"] < -0.9           # без БУ полный стоп
    assert with_be[0]["r"] == 0.0          # с БУ выход в ноль
    assert with_be[0]["risk"] > 0          # исходный риск сохранён для отчётов


def test_confirm_requires_close_beyond_level():
    frame, zones = make_frame()
    # ATR на сигнале ~1.1; порог 2 ATR = ~2.2 — закрытие 101.5 его не проходит
    assert run(frame, zones)
    assert run(frame, zones, confirm_atr=2.0) == []


def test_trailing_after_one_r_locks_profit():
    frame, zones = make_frame()
    base = run(frame, zones)
    trailed = run(frame, zones, trail_after_r=1.0, trail_k=1.0)
    assert base and trailed
    assert base[0]["r"] < -0.9              # без трейлинга откат выбивает полный стоп
    assert trailed[0]["r"] > 0              # с трейлингом стоп подтянут выше входа
    assert trailed[0]["risk"] > 0


def test_trend_ok_uses_last_closed_h4():
    n = 260
    c = np.linspace(100.0, 150.0, n)
    h4 = st.Frame(t=np.array([T0 + i * 14400 for i in range(n)], dtype=np.int64),
                  o=c, h=c + 0.5, l=c - 0.5, c=c, v=np.ones(n))
    ts = float(h4.t[-1] + 14400)          # после последнего закрытого H4
    assert range_v2.trend_ok(h4, ts, "LONG", "with") is True
    assert range_v2.trend_ok(h4, ts, "SHORT", "with") is False
    assert range_v2.trend_ok(h4, ts, "SHORT", "against") is True
    assert range_v2.trend_ok(h4, ts, "LONG", "off") is True
