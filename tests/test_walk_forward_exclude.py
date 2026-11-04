"""WFA: исключение аномального окна из всех таймфреймов."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import strategy as st  # noqa: E402
from tools import walk_forward as wf  # noqa: E402


def _frame(times: list[int]) -> st.Frame:
    n = len(times)
    c = np.arange(n, dtype=float) + 100.0
    return st.Frame(t=np.array(times, dtype="int64"), o=c.copy(), h=c + 1,
                    l=c - 1, c=c, v=np.ones(n))


def _ts(y, m, d) -> int:
    return int(dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp())


def test_exclude_window_removes_bars_from_chart_and_htf():
    t = [_ts(2026, 10, 19), _ts(2026, 10, 20), _ts(2026, 10, 21), _ts(2026, 10, 26)]
    chart = _frame(t)
    htf = {"240": _frame([_ts(2026, 10, 20), _ts(2026, 10, 26)])}
    out, out_htf = wf.exclude_window(chart, htf, "2026-10-20:2026-10-26")
    assert list(out.t) == [_ts(2026, 10, 19), _ts(2026, 10, 26)]
    assert list(out_htf["240"].t) == [_ts(2026, 10, 26)]


def test_exclude_window_without_spec_is_noop():
    chart = _frame([_ts(2026, 10, 20)])
    out, out_htf = wf.exclude_window(chart, {}, None)
    assert out is chart and out_htf == {}


def test_cli_parses_exclude_and_json():
    only, json_path, exclude = wf._cli_args(
        ["classic", "--exclude", "2026-10-20:2026-10-26", "--json", "out.json"])
    assert only == "classic"
    assert json_path == "out.json"
    assert exclude == "2026-10-20:2026-10-26"
