"""Тесты новостного фильтра: валюты, окно до/после, кэш и fail-open."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import news_calendar as nc  # noqa: E402


def event(ts: float, country: str = "USD", impact: str = "high",
          title: str = "CPI m/m") -> dict:
    return {"ts": ts, "country": country, "impact": impact,
            "title": title, "date_raw": "2026-11-04T12:00:00-05:00"}


@pytest.fixture()
def news(tmp_path, monkeypatch):
    monkeypatch.setattr(nc, "ENABLED", True)
    monkeypatch.setattr(nc, "BEFORE_SEC", 30 * 60)
    monkeypatch.setattr(nc, "AFTER_SEC", 15 * 60)
    monkeypatch.setattr(nc, "CACHE_PATH", str(tmp_path / "news_cache.json"))
    return tmp_path


def test_currencies_for_metals_fx_and_indices():
    assert nc.currencies_for("XAUUSD") == {"USD"}
    assert nc.currencies_for("AUDUSD") == {"AUD", "USD"}
    assert nc.currencies_for("USDCAD") == {"USD", "CAD"}
    assert nc.currencies_for("US500") == {"USD"}
    assert nc.currencies_for("DE40") == {"EUR"}
    assert nc.currencies_for("FOO") == set()


def test_blocks_before_and_after_high_impact():
    now = 1_800_000_000.0
    ev = event(now + 10 * 60)          # через 10 минут
    blocked, found = nc.blackout("XAUUSD", now, [ev])
    assert blocked is True and found is ev
    ev_after = event(now - 10 * 60)    # 10 минут назад
    blocked, found = nc.blackout("XAUUSD", now, [ev_after])
    assert blocked is True and found is ev_after


def test_outside_window_is_free():
    now = 1_800_000_000.0
    far_before = event(now + 31 * 60)
    far_after = event(now - 16 * 60)
    blocked, found = nc.blackout("XAUUSD", now, [far_before, far_after])
    assert blocked is False and found is None


def test_low_impact_and_other_currency_do_not_block():
    now = 1_800_000_000.0
    low = event(now, impact="low")
    eur = event(now, country="EUR")
    blocked, _ = nc.blackout("XAUUSD", now, [low, eur])
    assert blocked is False


def test_fx_symbol_matches_either_currency():
    now = 1_800_000_000.0
    aud = event(now, country="AUD")
    assert nc.blackout("AUDUSD", now, [aud])[0] is True
    assert nc.blackout("XAUUSD", now, [aud])[0] is False


def test_stale_cache_is_used_when_network_fails(news, monkeypatch):
    now = time.time()
    cache = {"fetched_at": now - 99999, "events": [event(now + 600)]}
    Path(nc.CACHE_PATH).write_text(json.dumps(cache), encoding="utf-8")

    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(nc, "fetch_events", boom)
    events = nc.load_events(now)
    assert len(events) == 1


def test_allow_fetch_false_returns_cache_without_network(news, monkeypatch):
    now = time.time()
    Path(nc.CACHE_PATH).write_text(
        json.dumps({"fetched_at": now, "events": [event(now + 600)]}),
        encoding="utf-8")

    def boom():
        raise AssertionError("сеть не должна вызываться")

    monkeypatch.setattr(nc, "fetch_events", boom)
    events = nc.load_events(now, allow_fetch=False)
    assert len(events) == 1
    blocked, _ = nc.blackout("XAUUSD", now, allow_fetch=False)
    assert blocked is True


def test_disabled_filter_never_blocks(monkeypatch):
    monkeypatch.setattr(nc, "ENABLED", False)
    now = 1_800_000_000.0
    assert nc.blackout("XAUUSD", now, [event(now)]) == (False, None)
