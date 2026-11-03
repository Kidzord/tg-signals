"""X: смешанный твит от первого лица и лимит 280 символов."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import x_post  # noqa: E402


def test_mix_text_is_short_and_first_person():
    trade = {"symbol": "AUDUSD", "side": "SHORT", "total_r": 3.0}
    stats = {"n": 4, "r": 4.0, "win": 50.0}
    text = x_post.mix_text(trade=trade, note="a portfolio kill-switch",
                           stats=stats)
    assert text.startswith("My post for today.")
    assert "Closed AUDUSD short, +3.0R" in text
    assert "Shipped: a portfolio kill-switch." in text
    assert "Last 7 days: 4 trades, +4.0R, 50% win." in text
    assert "Journal: t.me/" in text
    assert len(text) <= x_post.LIMIT


def test_mix_text_drops_stats_if_note_is_long():
    trade = {"symbol": "XAUUSD", "side": "LONG", "total_r": 2.0}
    stats = {"n": 9, "r": 12.5, "win": 55.0}
    text = x_post.mix_text(
        trade=trade,
        note="a very long shipped note " * 8,
        stats=stats,
    )
    assert len(text) <= x_post.LIMIT
    assert "My post for today." in text
