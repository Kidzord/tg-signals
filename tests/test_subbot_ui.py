"""Первый экран и воронка premium-бота: триал должен быть виден до оплаты."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import subbot  # noqa: E402


def test_buy_options_offers_trial_first_for_new_user():
    rows = subbot.buy_options({}, "42")
    assert rows[0][0]["callback_data"] == "trial"
    assert any(b[0]["callback_data"] == "pay:stars" for b in rows)


def test_buy_options_hides_trial_after_it_was_used():
    users = {"42": {"trial_used": True}}
    rows = subbot.buy_options(users, "42")
    assert all(b[0]["callback_data"] != "trial" for b in rows)


def test_menu_offers_trial_before_payment():
    rows = subbot.menu_markup({}, "42")["inline_keyboard"]
    callbacks = [btn.get("callback_data") for row in rows for btn in row]
    assert callbacks.index("trial") < callbacks.index("buy")


def test_card_status_shows_days_left_for_paid_user():
    until = time.time() + 10 * 86400 + 3600
    line = subbot.card_status_line({"42": {"paid_until": until}}, "42")
    assert "Доступ до" in line and "осталось 11 дн." in line


def test_card_status_points_to_trial_for_new_user():
    line = subbot.card_status_line({}, "42")
    assert "Пробный доступ" in line


def test_card_status_does_not_promise_trial_twice():
    line = subbot.card_status_line({"42": {"trial_used": True}}, "42")
    assert "Пробный доступ" not in line and "не активна" in line


def test_welcome_mentions_trial_before_payment():
    text = subbot.welcome_text({}, "42")
    assert "бесплатно" in text and "кнопка под сообщением" in text
    assert "Жми «⚡ Premium»" not in text


def test_welcome_for_paid_user_shows_until_date():
    until = time.time() + 5 * 86400
    text = subbot.welcome_text({"42": {"paid_until": until}}, "42")
    assert "Подписка активна до" in text


def test_size_line_for_user_deposit():
    rec = {"size": {"loss_per_lot": 1300.0, "step": 0.01,
                    "min": 0.01, "max": 100.0}}
    line = subbot.size_line({"42": {"deposit": 10000, "risk": 0.5}}, "42", rec)
    assert "50 $" in line and "0.03 лота" in line


def test_size_line_warns_when_min_lot_exceeds_risk():
    rec = {"size": {"loss_per_lot": 1300.0, "step": 0.01,
                    "min": 0.01, "max": 100.0}}
    line = subbot.size_line({"42": {"deposit": 100, "risk": 1}}, "42", rec)
    assert "Мин. лот" in line and "больше" in line


def test_size_line_empty_without_size_specs():
    assert subbot.size_line({}, "42", {"caption": "x"}) == ""
