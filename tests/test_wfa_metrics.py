"""Метрика WFA: вердикт по активным фолдам, соседи сетки, сдвиг сессии."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import strategy as st  # noqa: E402
import wfa_verdict  # noqa: E402
from tools import walk_forward as wf  # noqa: E402


def test_verdict_passes_on_normalized_metric():
    assert wfa_verdict.verdict(18.5, 10, 7, 0.4) == wfa_verdict.PASS


def test_verdict_needs_eight_active_folds():
    assert wfa_verdict.verdict(18.5, 7, 7, 0.4) == wfa_verdict.FAIL


def test_verdict_needs_active_share_not_all_folds():
    # всего 20 фолдов, но активных 10 и в плюсе 6 (60%) — проходит
    assert wfa_verdict.verdict(9.0, 10, 6, 0.2) == wfa_verdict.PASS
    # те же 6 из 20 «всех» дали бы 30% — старая метрика ошибочно отклоняла
    assert wfa_verdict.active_share(10, 6) == 0.6


def test_verdict_needs_avg_and_net():
    assert wfa_verdict.verdict(4.9, 10, 9, 0.5) == wfa_verdict.FAIL
    assert wfa_verdict.verdict(18.5, 10, 9, 0.05) == wfa_verdict.FAIL


def test_verdict_fails_without_active_folds():
    assert wfa_verdict.verdict(18.5, 0, 0, 0.5) == wfa_verdict.FAIL


def test_reasons_explain_each_threshold():
    out = wfa_verdict.reasons(1.0, 3, 1, 0.0)
    text = " | ".join(out)
    assert "net" in text and "активных фолдов 3" in text and "средняя" in text


def test_neighbors_are_one_step_on_one_axis():
    grid = [{"tp_r": t, "er_max": e} for t in (2.0, 3.0, 4.0) for e in (0.0, 0.2)]
    base = {"tp_r": 3.0, "er_max": 0.2}
    out = wf.neighbors(grid, base)
    assert ("tp_r=2.0", {"tp_r": 2.0, "er_max": 0.2}) in out
    assert ("tp_r=4.0", {"tp_r": 4.0, "er_max": 0.2}) in out
    assert ("er_max=0.0", {"tp_r": 3.0, "er_max": 0.0}) in out
    assert all(label.startswith(("tp_r=", "er_max=")) for label, _ in out)


def test_neighbors_skip_missing_cell():
    grid = [{"tp_r": 2.0, "side": "both"}, {"tp_r": 3.0, "side": "both"}]
    # side строковый — не ось; tp_r=2 у базового 2 нет левого соседа
    assert wf.neighbors(grid, {"tp_r": 2.0, "side": "both"}) == [
        ("tp_r=3.0", {"tp_r": 3.0, "side": "both"})]


def _frame(n: int) -> st.Frame:
    c = np.full(n, 100.0)
    return st.Frame(t=np.arange(n, dtype="int64") * 900, o=c.copy(),
                    h=c + 1, l=c - 1, c=c, v=np.ones(n))


def test_plateau_flags_sign_flip(monkeypatch):
    chart = _frame(7300)
    cfg = {"grid": [{"tp_r": 2.0}, {"tp_r": 3.0}, {"tp_r": 4.0}]}

    def runner(chart_slice, htf, params, delay=0, spread=0.0):
        r = 1.0 if params["tp_r"] != 4.0 else -5.0
        return [{"r": r, "entry": 100.0, "stop": 99.0}]

    cfg["runner"] = runner
    out = wf.plateau(cfg, chart, {}, {"tp_r": 3.0}, spread=0.0, stress=1.0, delay=0)
    assert out["ok"] is False
    assert any("tp_r=4.0" in flip for flip in out["sign_flips"])


def test_plateau_ok_when_neighbors_keep_sign():
    chart = _frame(7300)
    cfg = {"grid": [{"tp_r": 2.0}, {"tp_r": 3.0}, {"tp_r": 4.0}],
           "runner": lambda chart_slice, htf, params, delay=0, spread=0.0: [
               {"r": 1.0, "entry": 100.0, "stop": 99.0}]}
    out = wf.plateau(cfg, chart, {}, {"tp_r": 3.0}, spread=0.0, stress=1.0, delay=0)
    assert out["ok"] is True


def test_shift_sessions_moves_chart_and_htf():
    chart = _frame(5)
    htf = {"240": _frame(3)}
    out, out_htf = wf.shift_sessions(chart, htf, 1)
    assert list(out.t[:2] - chart.t[:2]) == [3600, 3600]
    assert list(out_htf["240"].t - htf["240"].t) == [3600, 3600, 3600]


def test_cli_opts_parse_stress_delay_and_plateau_switch():
    opts = wf._cli_opts(["classic", "--stress", "2", "--delay", "1", "--shift-hours", "-1"])
    assert opts["stress"] == 2.0
    assert opts["delay"] == 1
    assert opts["shift"] == -1.0
    assert wf._cli_opts(["--no-plateau"])["plateau"] is False
