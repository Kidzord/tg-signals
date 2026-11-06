"""Контракт step_*: одинаковые проверки и одинаковый trade-стейт у стратегий.

Тесты фиксируют поведение ДО выноса общего каркаса, чтобы рефакторинг не
изменил ни порядок проверок, ни поля state["trade"].
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
import crt  # noqa: E402
import ict2022  # noqa: E402
import sonar  # noqa: E402
import strategy as st  # noqa: E402
import ta_core as ta  # noqa: E402

N = 40
BAR = 900


def frame(close: float = 100.0, low_last: float | None = None,
          high_last: float | None = None) -> st.Frame:
    c = np.full(N, close, dtype=float)
    l = c - 1.0
    h = c + 1.0
    if low_last is not None:
        l[-1] = low_last
    if high_last is not None:
        h[-1] = high_last
    t = np.array([1_800_000_000 + i * BAR for i in range(N)], dtype="int64")
    return st.Frame(t=t, o=c.copy(), h=h, l=l, c=c, v=np.ones(N))


@pytest.fixture()
def env(monkeypatch):
    """Глушим файловые и внешние эффекты: state не пишем, сигналы не публикуем."""
    monkeypatch.setattr(bot, "save_state", lambda _state: None)
    monkeypatch.setattr(bot, "post_signal", lambda *a, **k: True)
    monkeypatch.setattr(bot, "trade_id_for", lambda *a, **k: "T1")
    monkeypatch.setattr(bot, "risk_stops_today", lambda *_a, **_k: 0)
    monkeypatch.setattr(bot, "MAX_DAILY_STOPS", 3)
    return monkeypatch


def test_sonar_signal_state(env):
    chart = frame()
    env.setattr(sonar, "build_atr", lambda _c: np.zeros(N))
    env.setattr(sonar, "find_levels", lambda *_a, **_k: [{"levels": [1, 2]}] * N)
    env.setattr(sonar, "detect_at", lambda *_a, **_k: ("LONG", 95.0))
    env.setattr(st, "_htf", lambda t, _H, _v, _shift: np.full(len(t), 50.0))
    htf = {bot.PARAMS.htf_tf: chart}

    state: dict = {}
    info = bot.step_sonar(state, chart, htf)

    assert info["posted"] is True and info["trade"] is True
    trade = state["trade"]
    assert trade["entry"] == 100.0 and trade["stop"] == 95.0
    assert trade["tp1"] == 110.0                       # risk 5 × 2R
    assert trade["single"] is True and trade["p1"] == 1.0
    assert trade["r1"] == bot.SONAR_TARGET_R
    assert state["last_signal_bar"] == float(chart.t[-1])


def test_ict_signal_state(env):
    chart = frame()
    env.setattr(sonar, "build_atr", lambda _c: np.zeros(N))
    env.setattr(ict2022, "signal_at", lambda *_a, **_k: ("LONG", 95.0))

    state: dict = {}
    info = bot.step_ict(state, chart)

    assert info["posted"] is True
    assert state["trade"]["tp1"] == pytest.approx(120.0)   # risk 5 × 4R
    assert state["trade"]["r1"] == bot.ICT_TP_R


def test_range_signal_state(env):
    import range_v2 as rv

    chart = frame(low_last=99.0)
    htf = {bot.RANGE_ZONE_TF: chart}
    env.setattr(rv, "zones_for_chart", lambda *_a, **_k: [
        {"levels": [{"price": 99.5, "kind": "sup"}]}] * N)
    env.setattr(ta, "efficiency_ratio", lambda _c: np.zeros(N))
    env.setattr(ta, "atr", lambda *_a, **_k: np.full(N, 2.0))
    env.setattr(rv, "side_ok", lambda *_a, **_k: True)
    env.setattr(rv, "trend_ok", lambda *_a, **_k: True)
    env.setattr(rv, "smt_ok", lambda *_a, **_k: True)
    env.setattr(rv, "pick_target", lambda *_a, **_k: 105.0)

    state: dict = {}
    info = bot.step_range(state, chart, htf)

    assert info["posted"] is True
    trade = state["trade"]
    assert trade["stop"] == pytest.approx(98.0)         # ATR 2 × 1
    assert trade["level"] == 99.5
    assert trade["r1"] == pytest.approx(2.5)            # (105 − 100) / 2


def test_crt_signal_state(env):
    chart = frame()
    htf = {bot.CRT_RANGE_TF: chart}
    env.setattr(crt, "scan", lambda *_a, **_k: [])
    env.setattr(crt, "signal_at", lambda *_a, **_k: {
        "side": "LONG", "stop": 95.0, "tp1": 105.0, "tp2": 110.0})
    env.setattr(ta, "atr", lambda *_a, **_k: np.full(N, 1.0))

    state: dict = {}
    info = bot.step_crt(None, state, chart, htf)

    assert info["posted"] is True
    trade = state["trade"]
    assert trade["tp1"] == 105.0 and trade["tp2"] == 110.0
    assert trade["single"] is False and trade["p1"] == 0.5
    assert trade["r1"] == pytest.approx(1.0)
    assert trade["r2"] == pytest.approx(2.0)
    assert trade["p3"] == 0.0


def test_daily_limit_blocks_all_strategies(env):
    chart = frame()
    env.setattr(bot, "risk_stops_today", lambda *_a, **_k: 3)
    env.setattr(sonar, "build_atr", lambda _c: np.zeros(N))
    env.setattr(sonar, "find_levels", lambda *_a, **_k: [{"levels": []}] * N)
    info = bot.step_sonar({}, chart, {})
    assert info["posted"] is False
    assert "дневной лимит" in info["marks"]


def test_last_signal_bar_blocks_repeat(env):
    chart = frame()
    env.setattr(sonar, "build_atr", lambda _c: np.zeros(N))
    env.setattr(sonar, "find_levels", lambda *_a, **_k: [{"levels": [1]}] * N)
    env.setattr(sonar, "detect_at", lambda *_a, **_k: ("LONG", 95.0))
    state = {"last_signal_bar": float(chart.t[-1])}
    info = bot.step_sonar(state, chart, {})
    assert info["posted"] is False
    assert "trade" not in state


def test_open_trade_is_managed_not_replaced(env):
    chart = frame()
    calls = []
    env.setattr(bot, "manage_open_trade", lambda s, c, i: calls.append(i))
    state = {"trade": {"bar_ts": float(chart.t[-1]) - BAR}}
    info = bot.step_sonar(state, chart, {})
    assert calls == [N - 1]
    assert info["trade"] is True and info["posted"] is False
