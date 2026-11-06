"""Внешние ряды: выравнивание по дате и функции фильтров."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import calendar_effects as ce  # noqa: E402
import external_data as ed  # noqa: E402
import strategy as st  # noqa: E402
from tools import regime_probe as rp  # noqa: E402

DAY = 86400
START = 1_600_000_000


def _frame(n: int = 10, step: float = 1.0) -> st.Frame:
    c = np.array([100.0 + i * step for i in range(n)])
    return st.Frame(t=np.array([START + i * DAY for i in range(n)], dtype="int64"),
                    o=c.copy(), h=c, l=c, c=c, v=np.ones(n))


def test_value_on_or_before_skips_gaps():
    data = {"2026-01-05": 10.0}
    assert ed.value_on_or_before(data, dt.date(2026, 1, 7)) == 10.0
    assert ed.value_on_or_before({"2026-01-01": 1.0}, dt.date(2026, 2, 1)) is None


def test_fomc_drift_takes_previous_close_to_event_close():
    frame = _frame(10, step=1.0)          # +1% на бар
    dates = [dt.datetime.fromtimestamp(int(frame.t[5]), dt.timezone.utc).date()]
    rows = ce.fomc_drift(frame, dates, mode="day", cost_pct=0.001)
    assert len(rows) == 1
    expected = 105.0 / 104.0 - 1.0
    assert abs(rows[0]["gross"] - expected) < 2e-6
    assert abs(rows[0]["net"] - (expected - 0.001)) < 2e-6


def test_fomc_drift_overnight_uses_open():
    frame = _frame(10, step=1.0)
    dates = [dt.datetime.fromtimestamp(int(frame.t[5]), dt.timezone.utc).date()]
    rows = ce.fomc_drift(frame, dates, mode="overnight", cost_pct=0.0)
    assert rows and abs(rows[0]["gross"] - (105.0 / 104.0 - 1.0)) < 2e-6


def _vix(values: list[float]) -> dict[str, float]:
    base = dt.date(2020, 1, 1)
    return {(base + dt.timedelta(days=i)).isoformat(): v
            for i, v in enumerate(values)}


def test_fomc_intraday_entry_prev_close_exit_before_announcement():
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    ts = [dt.datetime(2024, 1, 30, 15, 30, tzinfo=et),
          dt.datetime(2024, 1, 31, 13, 0, tzinfo=et),
          dt.datetime(2024, 1, 31, 14, 0, tzinfo=et)]
    epochs = np.array([int(x.timestamp()) for x in ts], dtype="int64")
    o = np.array([100.0, 101.0, 102.0])
    c = np.array([100.5, 101.5, 102.5])
    frame = st.Frame(t=epochs, o=o, h=np.maximum(o, c), l=np.minimum(o, c),
                     c=c, v=np.ones(3))
    rows = ce.fomc_intraday(frame, [dt.date(2024, 1, 31)], exit_hm=(13, 0),
                            cost_pct=0.001)
    assert rows and abs(rows[0]["gross"] - (101.0 / 100.5 - 1.0)) < 2e-6


def test_vix_high_detects_percentile_and_spike():
    # Фильтр смотрит на закрытие ПРЕДЫДУЩЕГО дня, поэтому запрос — на день
    # после последнего наблюдения.
    low = [10.0] * 300
    assert rp._vix_high(_vix(low + [40.0]), "2020-10-28") is True
    assert rp._vix_high(_vix(low + [5.0]), "2020-10-28") is False


def test_real_yield_rising():
    up = [(-1.0 + i * 0.01) for i in range(30)]
    down = [(0.5 - i * 0.01) for i in range(30)]
    date = (dt.date(2020, 1, 1) + dt.timedelta(days=30)).isoformat()
    assert rp._real_rising(_vix(up), date) is True
    assert rp._real_rising(_vix(down), date) is False


def test_broad_dollar_strong_requires_trend():
    rising = [100.0 * (1.0 + 0.001) ** i for i in range(300)]
    falling = list(reversed(rising))
    date = (dt.date(2020, 1, 1) + dt.timedelta(days=299)).isoformat()
    assert rp._broad_dollar_strong(_vix(rising), date) is True
    assert rp._broad_dollar_strong(_vix(falling), date) is False
