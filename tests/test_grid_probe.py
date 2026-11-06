"""OCO-сетка из ролика: TP, отсутствие SL, усреднение лота, оба ордера, stop-out."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.grid_probe import Bar, Params, simulate  # noqa: E402


def bar(t: int, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(t, o, h, l, c)


def flat(t: int, price: float = 100.0) -> Bar:
    return bar(t, price, price + 0.1, price - 0.1, price)


def params(**kw) -> Params:
    base = dict(dist=0.8, tp=1.2, spread=0.0, slippage=0.0,
                exit_slippage=0.0, leverage=1e9, start_balance=1060.0)
    base.update(kw)
    return Params(**base)


def test_buy_stop_fills_and_takes_profit():
    bars = [flat(0), flat(300), flat(600),
            bar(900, 100.0, 101.0, 99.9, 100.5),
            bar(1200, 100.5, 102.5, 100.4, 102.0)]
    res = simulate(bars, params())
    tp = [t for t in res.trades if t.reason == "tp"]
    assert len(tp) == 1
    assert tp[0].side == 1
    assert tp[0].entry == 100.8
    assert abs(tp[0].pnl - 1.2) < 1e-9
    assert abs(res.balance - 1061.2) < 1e-9


def test_no_stop_loss_keeps_losing_position_open():
    bars = [flat(0), flat(300), flat(600),
            bar(900, 100.0, 101.0, 99.9, 100.5),
            bar(1200, 100.5, 100.6, 95.0, 95.5)]
    res = simulate(bars, params())
    assert res.open_at_end >= 1
    assert all(t.reason != "sl" for t in res.trades)
    assert res.max_floating_loss < -5.0


def test_lot_grows_with_open_positions_on_same_side():
    bars = [flat(0), flat(300), flat(600),
            bar(900, 100.0, 101.0, 100.0, 100.9),
            bar(1200, 100.9, 101.0, 100.9, 100.95),
            bar(1800, 101.0, 102.2, 101.0, 102.0)]
    res = simulate(bars, params(dist=0.5, tp=50.0))
    assert res.max_lots == 0.02
    assert res.open_at_end == 2


def test_grid_stops_at_three_levels():
    bars = [flat(0), flat(300), flat(600),
            bar(900, 100.0, 101.0, 100.0, 100.9),
            bar(1800, 100.9, 101.9, 100.9, 101.8),
            bar(2700, 101.8, 102.8, 101.8, 102.7),
            bar(3600, 102.7, 103.7, 102.7, 103.6)]
    res = simulate(bars, params(dist=0.5, tp=50.0))
    assert res.open_at_end == 3
    assert res.max_lots == 0.03


def test_both_oco_sides_fill_in_one_bar():
    bars = [flat(0), flat(300), flat(600),
            bar(900, 100.0, 101.0, 99.0, 100.0)]
    res = simulate(bars, params(dist=0.5, tp=5.0))
    assert res.max_open == 2
    assert res.open_at_end == 2


def test_sell_tp_pays_one_tp_after_spread():
    bars = [flat(0), flat(300), flat(600),
            bar(900, 100.0, 100.2, 99.0, 99.2),
            bar(1200, 99.2, 99.3, 98.2, 98.5)]
    res = simulate(bars, params(dist=0.5, tp=1.0, spread=0.2))
    tp = [t for t in res.trades if t.reason == "tp"]
    assert len(tp) == 1
    assert tp[0].side == -1
    assert abs(tp[0].pnl - 1.0) < 1e-9


def test_gap_fills_stop_at_open_plus_slippage():
    bars = [flat(0), flat(300), flat(600),
            bar(900, 102.0, 102.5, 101.8, 102.3)]
    res = simulate(bars, params(tp=0.1, slippage=0.05))
    assert len(res.trades) == 1
    assert abs(res.trades[0].entry - 102.05) < 1e-9


def test_per_position_stop_caps_loss_exactly():
    # dist=200 уводит встречный sell stop далеко: buy @300, падение до 200,
    # стоп 50 срабатывает на 250 и фиксирует ровно -50
    bars = [flat(0), flat(300), flat(600),
            bar(900, 300.0, 300.2, 200.0, 200.0)]
    res = simulate(bars, params(dist=200.0, tp=1.0, stop_loss=50.0))
    sl = [t for t in res.trades if t.reason == "sl"]
    assert len(sl) == 1
    assert sl[0].side == 1
    assert abs(sl[0].pnl + 50.0) < 1e-9
    assert res.open_at_end == 0


def test_stop_gap_fills_worse_than_level():
    bars = [flat(0), flat(300), flat(600),
            bar(900, 300.0, 300.2, 300.0, 300.0),
            bar(1200, 200.0, 200.2, 199.8, 200.0)]
    res = simulate(bars, params(dist=200.0, tp=1.0, stop_loss=50.0))
    sl = [t for t in res.trades if t.reason == "sl"]
    assert len(sl) == 1
    assert abs(sl[0].pnl + 100.0) < 1e-9


def test_basket_stop_closes_everything():
    bars = [flat(0), flat(300), flat(600),
            bar(900, 300.0, 300.2, 150.0, 150.0)]
    res = simulate(bars, params(dist=200.0, tp=1.0, basket_stop=50.0))
    basket = [t for t in res.trades if t.reason == "basket"]
    assert len(basket) == 1
    assert res.basket_stops == 1
    assert res.open_at_end == 0


def test_equity_stop_triggers_on_drawdown_from_peak():
    bars = [flat(0), flat(300), flat(600),
            bar(900, 300.0, 300.2, 150.0, 150.0)]
    res = simulate(bars, params(dist=200.0, tp=1.0, equity_stop_pct=10.0,
                                start_balance=1000.0))
    assert res.equity_stops == 1
    assert res.open_at_end == 0
    assert res.balance < 1000.0


def test_stopout_closes_at_equity_threshold_not_at_bar_low():
    # гэп вверх заполняет только buy stop (sell stop на 50 не тронут),
    # затем падение до 79: stop-out должен сработать на 131.15, а не на 79.5
    bars = [flat(0), flat(300), flat(600),
            bar(900, 151.0, 151.2, 79.0, 79.5)]
    res = simulate(bars, params(dist=50.0, tp=100.0, leverage=500.0,
                                start_balance=20.0))
    assert res.blowup_reason == "stopout"
    assert res.blowup_ts == 900
    assert res.open_at_end == 0
    assert 0.0 <= res.balance < 0.5
