"""Семантика RANGE_ER_MAX: 0 — фильтр выключен, а не «ER ≤ 0».

В бэктесте (range_v2.backtest) er_max=0 означает «ER не считаем». Живой бот
должен читать 0 так же, иначе EURUSD с er=0.0 не входит никогда: Kaufman ER
на реальном рынке всегда больше нуля.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
import range_v2  # noqa: E402
import strategy as st  # noqa: E402
import ta_core as ta  # noqa: E402

N = 40
BAR = 900


def frame(close: float = 100.0, low_last: float | None = None) -> st.Frame:
    c = np.full(N, close, dtype=float)
    l = c - 1.0
    if low_last is not None:
        l[-1] = low_last
    t = np.array([1_800_000_000 + i * BAR for i in range(N)], dtype="int64")
    return st.Frame(t=t, o=c.copy(), h=c + 1.0, l=l, c=c, v=np.ones(N))


@pytest.fixture()
def env(monkeypatch):
    """Глушим файловые и внешние эффекты: state не пишем, сигналы не публикуем."""
    monkeypatch.setattr(bot, "save_state", lambda _state: None)
    monkeypatch.setattr(bot, "post_signal", lambda *a, **k: True)
    monkeypatch.setattr(bot, "trade_id_for", lambda *a, **k: "T1")
    monkeypatch.setattr(bot, "risk_stops_today", lambda *_a, **_k: 0)
    monkeypatch.setattr(bot, "MAX_DAILY_STOPS", 3)

    monkeypatch.setattr(range_v2, "zones_for_chart", lambda *_a, **_k: [
        {"levels": [{"price": 99.5, "kind": "sup"}]}] * N)
    monkeypatch.setattr(ta, "atr", lambda *_a, **_k: np.full(N, 2.0))
    monkeypatch.setattr(range_v2, "side_ok", lambda *_a, **_k: True)
    monkeypatch.setattr(range_v2, "trend_ok", lambda *_a, **_k: True)
    monkeypatch.setattr(range_v2, "smt_ok", lambda *_a, **_k: True)
    monkeypatch.setattr(range_v2, "pick_target", lambda *_a, **_k: 105.0)
    return monkeypatch


def run_range(env, er: float, er_max: float) -> bool:
    env.setattr(ta, "efficiency_ratio", lambda _c: np.full(N, er))
    env.setattr(bot, "RANGE_ER_MAX", er_max)
    chart = frame(low_last=99.0)
    state: dict = {}
    info = bot.step_range(state, chart, {bot.RANGE_ZONE_TF: chart})
    return bool(info.get("posted"))


@pytest.mark.parametrize("er", [0.0, 0.1, 0.5, float("nan")])
def test_zero_threshold_disables_filter(er):
    assert bot.er_filter_ok(er, 0.0) is True
    assert bot.er_filter_ok(er, -1.0) is True


def test_positive_threshold_still_blocks_trending_market():
    assert bot.er_filter_ok(0.19, 0.20) is True
    assert bot.er_filter_ok(0.21, 0.20) is False
    assert bot.er_filter_ok(float("nan"), 0.20) is False


def test_step_range_trades_when_threshold_zero(env):
    # раньше здесь стояло «ER ≤ 0» и EURUSD не мог войти вообще
    assert run_range(env, er=0.5, er_max=0.0) is True


def test_step_range_blocks_trending_market_with_positive_threshold(env):
    assert run_range(env, er=0.5, er_max=0.20) is False
    assert run_range(env, er=0.1, er_max=0.20) is True
