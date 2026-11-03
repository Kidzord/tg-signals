"""Смешанный пост: положительная сделка + разработка + цифры, от первого лица."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import social_mix  # noqa: E402


@pytest.fixture()
def trades(tmp_path, monkeypatch):
    path = tmp_path / "trades.jsonl"
    monkeypatch.setattr(social_mix, "TRADES_PATH", str(path))
    now = time.time()
    rows = [
        {"ts": now - 3 * 86400, "symbol": "XAUUSD", "side": "LONG",
         "kind": "sl", "r": -1.0, "total_r": -1.0, "trade_id": "loss"},
        {"ts": now - 2 * 86400, "symbol": "AUDUSD", "side": "SHORT",
         "kind": "tp", "r": 3.0, "total_r": 3.0, "trade_id": "win"},
        {"ts": now - 86400, "symbol": "AUDUSD", "side": "LONG",
         "kind": "tp1", "r": 1.0, "total_r": 1.0, "trade_id": "partial"},
        # дубль той же закрытой сделки не должен считаться дважды
        {"ts": now - 2 * 86400, "symbol": "AUDUSD", "side": "SHORT",
         "kind": "tp", "r": 3.0, "total_r": 3.0, "trade_id": "win"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                    encoding="utf-8")
    return path


def test_latest_positive_skips_losses_and_partials(trades):
    pos = social_mix.latest_positive()
    assert pos is not None
    assert pos["trade_id"] == "win"
    assert social_mix.trade_line(pos) == "AUDUSD шорт, +3.0R"


def test_week_stats_dedupes_and_counts(trades):
    s = social_mix.week_stats()
    assert s["n"] == 2
    assert s["r"] == pytest.approx(2.0)
    assert s["win"] == pytest.approx(50.0)


def test_ru_text_is_first_person_and_mixes(trades):
    text = social_mix.ru_text(note="добавил фильтр новостей", with_cta=False)
    assert text.startswith("Вот мой пост на сегодня.")
    assert "AUDUSD шорт, +3.0R" in text
    assert "добавил фильтр новостей" in text
    assert "За 7 дней: 2 сделки" in text


def test_image_for_returns_existing_trade_file(trades, tmp_path):
    image = tmp_path / "trade.png"
    image.write_bytes(b"png")
    trade = {"trade_id": "win", "image": str(image)}
    assert social_mix.image_for(trade) == str(image)


def test_english_trade_line(trades):
    pos = social_mix.latest_positive()
    assert social_mix.trade_line(pos, "en") == "AUDUSD short, +3.0R"
