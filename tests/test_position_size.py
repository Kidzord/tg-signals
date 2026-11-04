"""Объём позиции: риск в долларах → лоты с округлением вниз по шагу."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import position_size as ps  # noqa: E402

XAU = {"loss_per_lot": 1300.0, "step": 0.01, "min": 0.01, "max": 100.0}


def test_size_floors_to_step():
    res = ps.size_for_risk(50.0, XAU)
    assert res["lots"] == 0.03
    assert res["risk"] == 39.0
    assert res["exceeds"] is False


def test_size_clamps_to_min_and_flags_excess():
    res = ps.size_for_risk(1.0, XAU)
    assert res["lots"] == 0.01
    assert res["exceeds"] is True


def test_size_respects_max_volume():
    res = ps.size_for_risk(10_000_000.0, XAU)
    assert res["lots"] == 100.0
    assert res["exceeds"] is False          # объём упёрся в максимум, риск ниже заданного


def test_size_none_without_specs():
    assert ps.size_for_risk(50.0, None) is None
    assert ps.size_for_risk(50.0, {"loss_per_lot": 0}) is None


def test_fmt_lots_uses_step_precision():
    assert ps.fmt_lots(0.05, 0.01) == "0.05"
    assert ps.fmt_lots(0.003, 0.001) == "0.003"


def test_queue_signal_keeps_size_for_premium(monkeypatch):
    import datetime as dt

    import bot
    import render as card

    captured: dict = {}
    monkeypatch.setattr(bot, "append_jsonl", lambda _path, rec: captured.update(rec))
    sig = card.Signal(side="SHORT", symbol="XAUUSD", tf="15",
                      entry=3960.0, sl=3973.0, tp1=3921.0,
                      when=dt.datetime(2026, 11, 4))
    bot.queue_signal("x.png", "caption", trade_id="T1", sig=sig,
                     size={"loss_per_lot": 1300.0, "step": 0.01})
    assert captured["size"]["loss_per_lot"] == 1300.0
    assert captured["trade_id"] == "T1"
