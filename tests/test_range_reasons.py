"""Диагностика range-бота: в лог попадает причина, а не заглушка «не хватает 0»."""
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
    monkeypatch.setattr(bot, "save_state", lambda _state: None)
    monkeypatch.setattr(bot, "post_signal", lambda *a, **k: True)
    monkeypatch.setattr(bot, "trade_id_for", lambda *a, **k: "T1")
    monkeypatch.setattr(bot, "risk_stops_today", lambda *_a, **_k: 0)
    monkeypatch.setattr(bot, "MAX_DAILY_STOPS", 3)
    monkeypatch.setattr(ta, "atr", lambda *_a, **_k: np.full(N, 2.0))
    monkeypatch.setattr(ta, "efficiency_ratio", lambda _c: np.full(N, 0.01))
    monkeypatch.setattr(range_v2, "side_ok", lambda *_a, **_k: True)
    monkeypatch.setattr(range_v2, "trend_ok", lambda *_a, **_k: True)
    monkeypatch.setattr(range_v2, "smt_ok", lambda *_a, **_k: True)
    monkeypatch.setattr(range_v2, "pick_target", lambda *_a, **_k: 105.0)
    return monkeypatch


def set_levels(env, price: float, kind: str = "sup") -> None:
    env.setattr(range_v2, "zones_for_chart", lambda *_a, **_k: [
        {"levels": [{"price": price, "kind": kind}]}] * N)


def run(env) -> dict:
    chart = frame(low_last=99.0)
    return bot.step_range({}, chart, {bot.RANGE_ZONE_TF: chart})


def test_no_crossing_reports_nearest_zone(env):
    set_levels(env, 200.0, "res")
    env.setattr(bot, "RANGE_ER_MAX", 0.20)
    info = run(env)
    assert info["posted"] is False
    assert "нет пересечения" in info["reason"]
    assert "200" in info["reason"]


def test_er_block_reports_values(env):
    set_levels(env, 99.5)
    env.setattr(ta, "efficiency_ratio", lambda _c: np.full(N, 0.50))
    env.setattr(bot, "RANGE_ER_MAX", 0.20)
    info = run(env)
    assert info["reason"] == "ER 0.50 > 0.20"


def test_trend_block_is_explicit(env):
    set_levels(env, 99.5)
    env.setattr(bot, "RANGE_ER_MAX", 0.20)
    env.setattr(range_v2, "trend_ok", lambda *_a, **_k: False)
    assert run(env)["reason"] == "против тренда H4"


def test_daily_limit_is_explicit(env):
    set_levels(env, 99.5)
    env.setattr(bot, "RANGE_ER_MAX", 0.20)
    env.setattr(bot, "risk_stops_today", lambda *_a, **_k: 3)
    info = run(env)
    assert info["reason"] == "дневной лимит стопов"
    assert info["marks"].count("дневной лимит") == 1


def test_signal_marks_reason(env):
    set_levels(env, 99.5)
    env.setattr(bot, "RANGE_ER_MAX", 0.20)
    info = run(env)
    assert info["posted"] is True
    assert info["reason"] == "сигнал отправлен"


def test_info_tail_prefers_reason_over_stub():
    assert "не хватает" not in bot._info_tail(
        {"marks": "6 зон", "missing": 0, "reason": "нет пересечения"})
    assert "не хватает 3" in bot._info_tail(
        {"marks": "✓✓·", "missing": 3, "reason": ""})
    tail = bot._info_tail(
        {"marks": "6 зон · дневной лимит стопов", "missing": 0,
         "reason": "дневной лимит стопов"})
    assert tail.count("дневной лимит") == 1
