"""Тесты портфельного kill-switch: недельный лимит, пауза, ручное снятие."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config as cfg  # noqa: E402
import risk_guard  # noqa: E402

NOW = dt.datetime(2026, 11, 4, 12, 0,
                  tzinfo=ZoneInfo(cfg.DISPLAY_TZ)).timestamp()


@pytest.fixture()
def guard(tmp_path, monkeypatch):
    trades = tmp_path / "trades.jsonl"
    monkeypatch.setattr(risk_guard, "TRADES_PATH", str(trades))
    monkeypatch.setattr(risk_guard, "STATE_PATH", str(tmp_path / "risk_guard.json"))
    monkeypatch.setattr(risk_guard, "WEEK_LIMIT_R", -6.0)
    monkeypatch.setattr(risk_guard, "MIN_TRADES", 4)
    return trades


def write_trades(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


def trade(ts: float, r: float, kind: str = "sl", tid: str = "") -> dict:
    return {"ts": ts, "symbol": "AUDUSD", "kind": kind, "r": r,
            "trade_id": tid or f"t{ts}"}


def test_week_stats_ignores_partials_duplicates_and_old_week(guard):
    start, _end, _key = risk_guard.week_bounds(NOW)
    write_trades(guard, [
        trade(start + 3600, -2.0, tid="a"),
        trade(start + 3600, -2.0, tid="a"),          # дубль той же сделки
        trade(start + 7200, 5.0, kind="tp1", tid="b"),  # частичный выход
        trade(start - 86400, -10.0, tid="old"),      # прошлая неделя
    ])
    stats = risk_guard.weekly_stats(NOW)
    assert stats["n"] == 1
    assert stats["r"] == pytest.approx(-2.0)


def test_below_min_trades_does_not_pause(guard):
    start, _end, _key = risk_guard.week_bounds(NOW)
    write_trades(guard, [trade(start + 100 * i, -2.0, tid=str(i)) for i in range(3)])
    res = risk_guard.apply(NOW)
    assert res["breach"] is False
    assert res["paused"] is False
    assert res["newly_paused"] is False


def test_zero_limit_disables_rule(guard, monkeypatch):
    monkeypatch.setattr(risk_guard, "WEEK_LIMIT_R", 0.0)
    start, _end, _key = risk_guard.week_bounds(NOW)
    write_trades(guard, [trade(start + 100 * i, -2.0, tid=str(i)) for i in range(4)])
    res = risk_guard.apply(NOW)
    assert res["breach"] is False
    assert res["paused"] is False
    assert "выключен" in risk_guard.status_text(NOW)


def test_breach_pauses_until_next_monday(guard):
    start, end, key = risk_guard.week_bounds(NOW)
    write_trades(guard, [trade(start + 100 * i, -2.0, tid=str(i)) for i in range(4)])
    res = risk_guard.apply(NOW)
    assert res["breach"] is True
    assert res["newly_paused"] is True
    assert res["paused"] is True
    assert res["until"] == pytest.approx(end)
    state = json.loads(Path(risk_guard.STATE_PATH).read_text(encoding="utf-8"))
    assert state["week_key"] == key
    assert "-8.00R" in state["reason"]
    assert risk_guard.pause_state(NOW)["paused"] is True


def test_pause_expires_at_week_end(guard):
    start, end, _key = risk_guard.week_bounds(NOW)
    write_trades(guard, [trade(start + 100 * i, -2.0, tid=str(i)) for i in range(4)])
    risk_guard.apply(NOW)
    assert risk_guard.pause_state(end - 1)["paused"] is True
    assert risk_guard.pause_state(end + 1)["paused"] is False


def test_manual_resume_does_not_repause_same_week(guard):
    start, _end, key = risk_guard.week_bounds(NOW)
    write_trades(guard, [trade(start + 100 * i, -2.0, tid=str(i)) for i in range(4)])
    risk_guard.apply(NOW)
    assert risk_guard.resume(NOW) == key
    res = risk_guard.apply(NOW + 3600)
    assert res["breach"] is True
    assert res["paused"] is False
    assert res["newly_paused"] is False
    assert res["overridden"] is True
    assert risk_guard.pause_state(NOW + 3600)["paused"] is False


def test_expired_pause_is_cleared_when_week_is_clean(guard):
    start, _end, _key = risk_guard.week_bounds(NOW)
    write_trades(guard, [trade(start - 86400, -1.0, tid="old")])
    Path(risk_guard.STATE_PATH).write_text(
        json.dumps({"paused_until": start - 100}), encoding="utf-8")
    res = risk_guard.apply(NOW)
    assert res["paused"] is False
    assert json.loads(Path(risk_guard.STATE_PATH).read_text(encoding="utf-8")) == {}


def test_status_text_mentions_limit_and_week(guard):
    start, _end, key = risk_guard.week_bounds(NOW)
    write_trades(guard, [trade(start + 100 * i, -2.0, tid=str(i)) for i in range(4)])
    risk_guard.apply(NOW)
    text = risk_guard.status_text(NOW)
    assert key in text
    assert "Пауза" in text
    assert "лимит -6.0R" in text
