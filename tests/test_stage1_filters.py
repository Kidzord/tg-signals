"""Этап 1: фильтры-оси не меняют live, но работают в research-режиме."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
import ict2022  # noqa: E402
import strategy as st  # noqa: E402
from tools import axis_sweep  # noqa: E402


def _res(n: int = 3, er: bool = True, adx: bool = True, filters: bool = True) -> dict:
    one = np.ones(n, dtype=bool)
    r = [one.copy() for _ in range(12)]
    return {
        "is_long": one.copy(),
        "r": r,
        "er_ok": np.full(n, er, dtype=bool),
        "adx_ok": np.full(n, adx, dtype=bool),
        "filters_ok": np.full(n, filters, dtype=bool),
        "since_sweep_l": np.full(n, 5.0),
        "since_sweep_s": np.full(n, 5.0),
    }


def _params(**kw) -> st.Params:
    base = dict(re_entry_bars=10, sweep_delay=1)
    base.update(kw)
    return st.Params(**base)


def _gate(res: dict, p: st.Params, i: int = 0) -> bool:
    m = {"state": 1, "side": 1, "last_exit": None, "in_trade": False}
    entry, _ = bot.cl_gate(res, i, m, p)
    return entry


def test_live_gate_ignores_research_masks_by_default():
    assert _gate(_res(er=False, adx=False, filters=False), _params()) is True


def test_er_hard_blocks_entry():
    p = _params(er_hard=True)
    assert _gate(_res(er=False), p) is False
    assert _gate(_res(er=True), p) is True


def test_adx_min_blocks_entry():
    p = _params(adx_min=20.0)
    assert _gate(_res(adx=False), p) is False
    assert _gate(_res(adx=True), p) is True


def test_filters_hard_blocks_entry():
    p = _params(filters_hard=True)
    assert _gate(_res(filters=False), p) is False
    assert _gate(_res(filters=True), p) is True


def test_session_none_is_all_hours():
    t = np.arange(0, 86400 * 3, 900, dtype="int64")
    ok, block = st.session_mask(t, "none")
    assert ok.all() and block.all()


def test_session_both_covers_london_and_ny():
    t = np.arange(0, 86400 * 5, 900, dtype="int64")
    ldn, _ = st.session_mask(t, "london")
    ny, _ = st.session_mask(t, "ny")
    both, _ = st.session_mask(t, "both")
    assert set(np.where(ldn)[0]).issubset(set(np.where(both)[0]))
    assert set(np.where(ny)[0]).issubset(set(np.where(both)[0]))
    assert both.sum() >= max(ldn.sum(), ny.sum())


def test_ict_er_filter_reduces_or_keeps_trades():
    rng = np.random.default_rng(7)
    n = 400
    c = 100 + np.cumsum(rng.normal(0, 0.3, n))
    frame = st.Frame(t=np.arange(n, dtype="int64") * 900,
                     o=c.copy(), h=c + 0.5, l=c - 0.5, c=c, v=np.ones(n))
    loose = ict2022.backtest(frame, swing_len=10, fvg_window=10, retest_look=10,
                             tp_r=2.0, max_bars=60)
    tight = ict2022.backtest(frame, swing_len=10, fvg_window=10, retest_look=10,
                             tp_r=2.0, max_bars=60, er_min=0.99)
    assert len(tight) <= len(loose)


def test_axis_coerce_keeps_types():
    assert axis_sweep.coerce("0.2", 0.2) == 0.2
    assert axis_sweep.coerce("0", None) == 0
    assert axis_sweep.coerce("true", None) is True
    assert axis_sweep.coerce("За свингом", "За манипуляцией") == "За свингом"


def test_axis_parse_overrides():
    got = axis_sweep.parse_overrides("mode_soft=0,stop_atr=1.2,sess_mode=london")
    assert got == {"mode_soft": 0, "stop_atr": 1.2, "sess_mode": "london"}


def test_range_adx_max_is_accepted():
    import range_v2
    n = 200
    c = np.full(n, 100.0)
    frame = st.Frame(t=np.arange(n, dtype="int64") * 900,
                     o=c.copy(), h=c + 1, l=c - 1, c=c, v=np.ones(n))
    zones = [{"bar": i, "levels": []} for i in range(n)]
    out = range_v2.backtest(frame, zones, frame, stop_atr=1.0, tp_r=2.0,
                            er_max=0.0, adx_max=25.0)
    assert isinstance(out, list)
