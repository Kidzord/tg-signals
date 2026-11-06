"""Level-fade движок: свип, стоп, частичный фикс 2R и цель 3R."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.level_fade_probe import Bar, _levels, backtest  # noqa: E402


def flat(i: int, price: float = 100.0) -> Bar:
    return Bar(i * 300, price, price + 0.1, price - 0.1, price)


def test_donchian_levels_use_previous_bars():
    bars = [flat(i) for i in range(30)]
    res, sup = _levels(bars, "don20")
    assert res[25] == 100.1 and sup[25] == 99.9
    assert res[10] is None and sup[10] is None


def test_sweep_returns_partial_and_target():
    bars = [flat(i) for i in range(60)]
    bars.append(Bar(60 * 300, 100.5, 100.6, 99.0, 100.5))  # свип низа, возврат
    bars.append(Bar(61 * 300, 100.5, 106.0, 100.4, 105.5))
    tr = backtest(bars, level="don20", tp_r=3.0, trail_atr=0.0, spread=0.0)
    assert len(tr) == 1
    assert tr[0]["side"] == "LONG"
    # 50% на 2R + 50% на 3R = 2.5R, минус пол издержек 0.05R
    assert abs(tr[0]["r"] - 2.45) < 1e-9
    assert tr[0]["reason"] == "target"


def test_stop_caps_loss():
    bars = [flat(i) for i in range(60)]
    bars.append(Bar(60 * 300, 100.5, 100.6, 99.0, 100.5))
    bars.append(Bar(61 * 300, 100.5, 100.6, 98.0, 98.5))
    tr = backtest(bars, level="don20", tp_r=3.0, trail_atr=0.0, spread=0.0)
    assert len(tr) == 1
    assert tr[0]["reason"] == "stop"
    assert abs(tr[0]["r"] + 1.05) < 1e-9
