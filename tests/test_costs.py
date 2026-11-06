"""Единая модель издержек: пол 0.05R, спред/риск, stress, env-переопределение."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import costs  # noqa: E402


def trade(r: float, entry: float = 100.0, stop: float = 99.0) -> dict:
    return {"r": r, "entry": entry, "stop": stop}


def test_cost_has_floor_when_spread_is_zero():
    assert costs.cost_r(0.0, 1.0) == costs.MIN_COST_R


def test_cost_is_spread_over_risk_above_floor():
    # спред 0.2 при риске 1.0 = 0.2R, это выше пола
    assert costs.cost_r(0.2, 1.0) == 0.2


def test_cost_uses_floor_when_spread_is_tiny():
    # спред 0.01 при риске 1.0 = 0.01R < 0.05R
    assert costs.cost_r(0.01, 1.0) == costs.MIN_COST_R


def test_cost_stress_multiplies_spread_only():
    assert costs.cost_r(0.2, 1.0, stress=2.0) == 0.4
    assert costs.cost_r(0.0, 1.0, stress=2.0) == costs.MIN_COST_R


def test_cost_never_divides_by_zero():
    assert costs.cost_r(0.2, 0.0) == costs.MIN_COST_R


def test_net_r_subtracts_cost_per_trade():
    t = trade(1.0, entry=100.0, stop=99.0)      # риск 1.0, спред 0.2 -> 0.2R
    assert costs.net_r(t, 0.2) == 0.8
    assert costs.net_rs([t, t], 0.2) == [0.8, 0.8]


def test_trade_risk_falls_back_to_entry_stop():
    t = {"r": 1.0, "entry": 100.0, "stop": 99.5}
    assert costs.trade_risk(t) == 0.5


def test_summary_reports_gross_cost_and_net():
    s = costs.summary([trade(1.0), trade(-1.0)], 0.2)
    assert s["trades"] == 2
    assert s["gross_r"] == 0.0
    assert s["cost_r"] == 0.4
    assert s["net_r"] == -0.4
    assert s["avg_r"] == -0.2


def test_env_override_wins_over_terminal(monkeypatch):
    class FakeInfo:
        spread = 25
        point = 0.01
        bid = ask = 0.0

    class FakeMT5:
        def symbol_select(self, symbol, enable):
            return True

        def symbol_info(self, symbol):
            return FakeInfo()

    monkeypatch.setenv("WF_SPREAD_XAUUSD", "0.33")
    assert costs.symbol_spread("XAUUSD", FakeMT5()) == 0.33


def test_symbol_spread_uses_terminal_when_no_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("WF_SPREAD"):
            monkeypatch.delenv(name, raising=False)

    class FakeInfo:
        spread = 25
        point = 0.01
        bid = ask = 0.0

    class FakeMT5:
        def symbol_select(self, symbol, enable):
            return True

        def symbol_info(self, symbol):
            return FakeInfo()

    assert costs.symbol_spread("XAUUSD", FakeMT5()) == 0.25
