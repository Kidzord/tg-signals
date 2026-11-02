"""Смоук-тесты исследовательского модуля PRO MB (pmb.py)."""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pmb  # noqa: E402


def test_fvg_list_detects_both_gaps():
    h = np.array([10, 11, 12, 13, 14], dtype=float)
    l = np.array([9, 10, 11.5, 12.5, 13.5], dtype=float)
    out = pmb.fvg_list(h, l)
    assert (2, 10.0, 11.5, 1) in out


def test_result_counts_r():
    def tr(side, res):
        return pmb.Trade(side=side, setup_i=1, trigger_i=2, entry_i=3, exit_i=4,
                         entry=1.0, sl=0.9, tp=1.2, result=res,
                         ts_setup=0, ts_trigger=0, ts_entry=0, ts_exit=0, tp_r=2.0)
    stats = pmb.result([tr(1, "win"), tr(-1, "win"), tr(1, "loss"), tr(1, "open")], 3)
    assert stats["entries"] == 3
    assert stats["wins"] == 2 and stats["losses"] == 1
    assert stats["net_r"] == 3.0
    assert stats["cancelled"] == 3


def test_engine_runs_on_synthetic_data():
    rng = np.random.default_rng(7)
    n = 600
    c = 1.10 + np.cumsum(rng.normal(0, 0.0015, n))
    o = np.concatenate(([c[0]], c[:-1]))
    h = np.maximum(o, c) + rng.uniform(0.0002, 0.0015, n)
    l = np.minimum(o, c) - rng.uniform(0.0002, 0.0015, n)
    t4 = np.arange(n, dtype=np.int64) * 4 * 3600 + 1_700_000_000
    d = 260
    dc = 1.10 + np.cumsum(rng.normal(0, 0.003, d))
    do = np.concatenate(([dc[0]], dc[:-1]))
    dh = np.maximum(do, dc) + rng.uniform(0.001, 0.004, d)
    dl = np.minimum(do, dc) - rng.uniform(0.001, 0.004, d)
    td = np.arange(d, dtype=np.int64) * 86400 + 1_700_000_000
    h4 = pmb.Frame(t=t4, o=o, h=h, l=l, c=c)
    d1 = pmb.Frame(t=td, o=do, h=dh, l=dl, c=dc)
    trades = pmb.Engine(h4, d1, pmb.Params()).run()
    assert isinstance(trades, list)
    for t in trades:
        assert t.sl != t.entry and t.tp != t.entry
