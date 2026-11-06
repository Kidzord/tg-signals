"""Paper-трекинг: записи кандидатов, агрегаты и хранилище."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import shadow  # noqa: E402
import strategy as st  # noqa: E402

DAY = 86400
START = 1_700_000_000


def _chart(n: int = 6) -> st.Frame:
    c = np.array([100.0 + i for i in range(n)])
    return st.Frame(t=np.array([START + i * 900 for i in range(n)], dtype="int64"),
                    o=c, h=c + 1, l=c - 1, c=c, v=np.ones(n))


def test_range_records_marks_open_on_last_bar():
    chart = _chart()
    trades = [
        {"entry_i": 1, "exit_i": 3, "entry": 101.0, "target": 103.0,
         "r": 1.0, "side": "LONG", "exit": "TP"},
        {"entry_i": 4, "exit_i": 5, "entry": 104.0, "target": 106.0,
         "r": 0.4, "side": "SHORT", "exit": "TIME"},
    ]
    rows = shadow.range_records(chart, trades, last_i=5)
    assert len(rows) == 2
    assert rows[0]["status"] == "closed"
    assert rows[1]["status"] == "open"


def test_tp_on_last_bar_is_closed_not_open():
    chart = _chart()
    trade = {"entry_i": 1, "exit_i": 5, "entry": 100.0, "target": 103.0,
             "risk": 1.0, "r": 2.0, "side": "LONG", "exit": "TP"}
    row = shadow.range_records(chart, [trade], last_i=5)[0]
    assert row["status"] == "closed"


def test_range_records_are_net_and_have_real_exit_price():
    chart = _chart()
    trade = {"entry_i": 1, "exit_i": 3, "entry": 100.0, "target": 103.0,
             "risk": 1.0, "r": 2.0, "side": "LONG", "exit": "TP"}
    row = shadow.range_records(chart, [trade], last_i=5, spread=0.1)[0]
    assert row["exit_price"] == 102.0          # entry + r * risk
    assert row["r"] == 1.9                     # 2.0 - spread/risk
    assert row["exit_reason"] == "TP"


def _real_series(entry_ts: float, step: float = 0.01) -> dict[str, float]:
    day = dt.datetime.fromtimestamp(entry_ts, dt.timezone.utc).date()
    return {(day - dt.timedelta(days=i)).isoformat(): -1.0 + (25 - i) * step
            for i in range(25)}


def test_classic_records_blocks_long_when_real_yields_rise():
    chart = _chart()
    real = _real_series(float(chart.t[1]))
    long_trade = {"i": 1, "ei": 3, "dir": 1, "r": 1.0, "risk": 1.0,
                  "entry": 101.0, "exit_price": 102.0, "exit": "TP"}
    short_trade = dict(long_trade, dir=-1)
    rows = shadow.classic_records(chart, [long_trade, short_trade], real, last_i=5)
    assert rows[0]["status"] == "blocked" and rows[0]["r"] == 0.0
    assert rows[1]["status"] == "closed" and rows[1]["r"] > 0


def test_classic_m5_candidate_skips_real_yield_filter():
    chart = _chart()
    long_trade = {"i": 1, "ei": 3, "dir": 1, "r": 1.0, "risk": 1.0,
                  "entry": 101.0, "exit_price": 102.0, "exit": "TP"}
    rows = shadow.classic_records(chart, [long_trade], {}, last_i=5,
                                  spread=0.19, candidate="classic_xau_m5",
                                  use_real=False)
    assert rows[0]["candidate"] == "classic_xau_m5"
    assert rows[0]["status"] == "closed" and rows[0]["r"] > 0


def test_agg_trades_net_win_and_drawdown():
    recs = [
        {"status": "closed", "r": 1.0, "entry_ts": 1},
        {"status": "closed", "r": 2.0, "entry_ts": 2},
        {"status": "closed", "r": -3.0, "entry_ts": 3},
        {"status": "open", "r": 0.0, "entry_ts": 4},
        {"status": "blocked", "r": 0.0, "entry_ts": 5},
    ]
    a = shadow.agg_trades(recs)
    assert a["closed"] == 3 and a["open"] == 1 and a["blocked"] == 1
    assert a["net_r"] == 0.0 and a["win_share"] == 0.667
    assert a["max_dd_r"] == 3.0          # 3.0 -> 0.0 после убытка


def test_agg_months_compounds_and_maxdd():
    recs = [
        {"status": "closed", "month": "2026-01", "net": 0.10},
        {"status": "closed", "month": "2026-02", "net": -0.05},
    ]
    a = shadow.agg_months(recs)
    assert a["months"] == 2
    assert a["return_pct"] == 4.5
    assert a["max_dd_pct"] == 5.0


def test_monthly_records_marks_current_month_open():
    rows = [{"month": "2026-10", "net": 0.01}, {"month": "2026-11", "net": 0.02}]
    recs = shadow.monthly_records(rows, "cand", now_month="2026-11")
    assert recs[0]["status"] == "closed"
    assert recs[1]["status"] == "open"


def test_sqlite_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    rec = {"candidate": "cand", "id": "1", "kind": "trade", "r": 1.0,
           "status": "closed", "updated": "2026-11-05"}
    shadow._save({("cand", "1"): rec})
    loaded = shadow._load()
    assert loaded[("cand", "1")]["r"] == 1.0
    shadow._save({})
    assert shadow._load() == {}


def test_current_positions_uses_last_closed_month():
    n = 400
    up = st.Frame(t=np.array([START + i * DAY for i in range(n)], dtype="int64"),
                  o=np.full(n, 100.0), h=np.full(n, 101.0), l=np.full(n, 99.0),
                  c=np.linspace(100.0, 200.0, n), v=np.ones(n))
    down = st.Frame(t=up.t, o=up.o, h=up.h, l=up.l,
                    c=np.linspace(200.0, 100.0, n), v=up.v)
    dual, tsmom_text = shadow.current_positions({"UP": up, "DOWN": down})
    assert dual == ["UP"]
    assert tsmom_text == "long 1/2"
