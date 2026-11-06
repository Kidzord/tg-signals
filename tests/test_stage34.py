"""Этапы 3–4: TSMOM, календарные эффекты, портфельная симуляция, режимы."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import calendar_effects as ce  # noqa: E402
import strategy as st  # noqa: E402
import tsmom  # noqa: E402
from tools import portfolio_backtest as pf  # noqa: E402
from tools import regime_probe as rp  # noqa: E402

DAY = 86400


def _daily(closes: list[float], start: int = 1_600_000_000) -> st.Frame:
    c = np.asarray(closes, dtype=float)
    n = len(c)
    return st.Frame(t=np.array([start + i * DAY for i in range(n)], dtype="int64"),
                    o=c.copy(), h=c * 1.001, l=c * 0.999, c=c, v=np.ones(n))


def _trend(n: int, step: float = 0.001, noise: float = 0.0, seed: int = 1) -> list[float]:
    rng = np.random.default_rng(seed)
    out = [100.0]
    for _ in range(1, n):
        out.append(out[-1] * (1.0 + step + (rng.normal(0.0, noise) if noise else 0.0)))
    return out


def test_tsmom_long_in_uptrend():
    frame = _daily(_trend(400, noise=0.004))
    rows = tsmom.monthly_series(frame, lookback=20, vol_len=10)
    assert rows and sum(1 for r in rows if r["sign"] == 1) >= len(rows) * 0.7
    assert np.mean([r["gross"] for r in rows]) > 0
    assert tsmom.stats(rows)["sharpe"] > 0


def test_tsmom_long_only_never_shorts():
    frame = _daily([100.0 * (1.0 - 0.001) ** i for i in range(400)])
    rows = tsmom.monthly_series(frame, lookback=20, vol_len=10, long_only=True)
    assert rows and all(r["sign"] == 0 for r in rows)
    assert all(r["gross"] == 0 for r in rows)


def test_tsmom_cost_reduces_net():
    frame = _daily(_trend(400, noise=0.004))
    rows = tsmom.monthly_series(frame, lookback=20, vol_len=10, spread_pct=0.01)
    assert rows and all(abs(r["gross"] - r["net"] - r["cost"]) < 2e-6 for r in rows)


def test_turn_of_month_trades_once_per_month():
    frame = _daily(_trend(500))
    rows = ce.turn_of_month(frame, sma_len=20, cost_pct=0.0)
    assert rows and 12 <= len(rows) <= 17
    assert all(r["gross"] > 0 for r in rows)


def test_turn_of_month_filter_blocks_downtrend():
    frame = _daily([100.0 * (1.0 - 0.001) ** i for i in range(500)])
    assert ce.turn_of_month(frame, sma_len=20) == []


def test_overnight_charges_one_spread():
    frame = _daily(_trend(400, noise=0.004))
    rows = ce.overnight(frame, sma_len=20, vol_len=10, pct_len=60,
                        pct_max=1.0, cost_pct=0.0001)
    assert rows and all(abs(r["gross"] - r["net"] - 0.0001) < 1e-9 for r in rows)


def test_dxy_regime_true_when_above_sma_and_rising():
    closes = _trend(300)
    dxy = {"t": [1_600_000_000 + i * DAY for i in range(300)], "c": closes}
    date = ce._month(1_600_000_000 + 299 * DAY) if hasattr(ce, "_month") else None
    # дата последнего бара
    import datetime as dt
    date = dt.datetime.fromtimestamp(dxy["t"][-1], dt.timezone.utc).strftime("%Y-%m-%d")
    assert rp._regime_dxy(dxy, date) is True


def _trade(sym: str, entry: int, exit_: int, r: float) -> dict:
    return {"sym": sym, "side": "LONG", "entry_ts": entry, "exit_ts": exit_,
            "r": r, "risk": 1.0}


def test_portfolio_group_lock_blocks_second_fx_trade():
    t0 = 1_700_000_000
    trades = [_trade("AUDUSD", t0, t0 + 3600, 1.0),
              _trade("EURUSD", t0 + 60, t0 + 3600, 1.0)]
    no_lock = pf.simulate(trades, max_risk_pct=0, group_limit=0)
    locked = pf.simulate(trades, max_risk_pct=0, group_limit=1)
    assert no_lock["accepted"] == 2
    assert locked["accepted"] == 1 and locked["blocked_group"] == 1


def test_portfolio_kill_switch_blocks_rest_of_week():
    t0 = 1_700_000_000
    trades = [_trade("XAUUSD", t0, t0 + 60, -7.0),
              _trade("AUDUSD", t0 + 120, t0 + 3600, 1.0)]
    on = pf.simulate(trades, max_risk_pct=0, group_limit=0, kill_r=-6.0, use_kill=True)
    off = pf.simulate(trades, max_risk_pct=0, group_limit=0, kill_r=-6.0, use_kill=False)
    assert off["accepted"] == 2
    assert on["accepted"] == 1 and on["blocked_kill"] == 1


def test_portfolio_risk_cap_blocks_concurrent_exposure():
    t0 = 1_700_000_000
    trades = [_trade("XAUUSD", t0, t0 + 3600, 1.0),
              _trade("US500", t0 + 60, t0 + 3600, 1.0)]
    capped = pf.simulate(trades, risk_pct=0.5, max_risk_pct=0.5, group_limit=0)
    assert capped["accepted"] == 1 and capped["blocked_cap"] == 1
