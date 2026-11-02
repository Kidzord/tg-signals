"""Платежи: идемпотентность и выдача подписки."""
from __future__ import annotations

import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import store  # noqa: E402
import subbot  # noqa: E402
import revenue  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(subbot, "USERS_PATH", str(tmp_path / "users.json"))
    store.init_db()
    yield


def test_apply_payment_is_idempotent(env):
    users: dict = {}
    assert subbot.apply_payment(users, provider="stars", external_id="charge-1",
                                uid="1", amount=500, currency="XTR", days=30)
    until = users["1"]["paid_until"]
    assert until > time.time()
    assert not subbot.apply_payment(users, provider="stars", external_id="charge-1",
                                    uid="1", amount=500, currency="XTR", days=30)
    assert users["1"]["paid_until"] == until


def test_crypto_poll_grants_once(env, monkeypatch):
    monkeypatch.setattr(subbot.crypto_pay, "enabled", lambda: True)
    invoice = {"invoice_id": 777, "payload": "sub:42:30", "amount": "9",
               "asset": "USDT"}
    monkeypatch.setattr(subbot.crypto_pay, "paid_invoices", lambda: (True, [invoice]))
    monkeypatch.setattr(subbot.tg, "send_message", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(subbot, "reward_referrer", lambda *a, **kw: None)
    users: dict = {}
    assert subbot.poll_crypto(users) is True
    until = users["42"]["paid_until"]
    assert until > time.time() + 29 * 86400
    assert subbot.poll_crypto(users) is True
    assert users["42"]["paid_until"] == until
    assert store.payment_totals(0)["crypto:paid:n"] == 1


def test_revenue_by_source(env, monkeypatch, tmp_path):
    users_path = tmp_path / "users.json"
    users_path.write_text(
        '{"1": {"paid_until": 9999999999, "source": "threads"}}',
        encoding="utf-8")
    monkeypatch.setattr(revenue, "USERS_PATH", str(users_path))
    store.record_payment(provider="stars", external_id="ch-1", uid="1",
                         amount=500, currency="XTR", days=30, source="threads")
    m = revenue.metrics(30)
    assert m["by_source"]["threads"]["paid"] == 1
    assert m["by_source"]["threads"]["revenue"] == 500
    assert "threads" in revenue.text_report(30)


def test_extension_adds_to_remaining_days(env):
    """Оплата при активной подписке не сжигает уже оплаченный остаток."""
    before = time.time() + 20 * 86400
    users = {"1": {"paid_until": before}}
    assert subbot.apply_payment(users, provider="crypto", external_id="c-1",
                                uid="1", days=30)
    assert users["1"]["paid_until"] == pytest.approx(before + 30 * 86400, abs=5)


def test_expired_subscription_starts_from_now(env):
    users = {"1": {"paid_until": time.time() - 10 * 86400}}
    assert subbot.apply_payment(users, provider="crypto", external_id="c-2",
                                uid="1", days=30)
    assert users["1"]["paid_until"] == pytest.approx(time.time() + 30 * 86400, abs=5)


def test_absolute_stars_date_does_not_stack(env):
    """Срок подписки Telegram — абсолютный: max(текущий, дата Telegram)."""
    exp = time.time() + 30 * 86400
    users = {"1": {"paid_until": time.time() + 20 * 86400}}
    assert subbot.apply_payment(users, provider="stars", external_id="s-1",
                                uid="1", days=30, until_ts=exp)
    assert users["1"]["paid_until"] == pytest.approx(exp, abs=1)


def test_payment_marks_granted(env):
    users: dict = {}
    subbot.apply_payment(users, provider="crypto", external_id="mark-1",
                         uid="1", days=30)
    row = store.payment_row("crypto", "mark-1")
    assert row and row["granted_until"] > time.time() + 29 * 86400


def test_reconcile_restores_ungranted_payment(env, monkeypatch):
    monkeypatch.setattr(subbot, "alert_owner", lambda *a, **kw: None)
    users = {"1": {"paid_until": 0}}
    store.record_payment(provider="crypto", external_id="lost", uid="1", days=30)
    subbot.reconcile_payments(users)
    assert users["1"]["paid_until"] > time.time() + 29 * 86400
    assert store.payment_row("crypto", "lost")["granted_until"] > 0


def test_reconcile_skips_granted_payment(env, monkeypatch):
    monkeypatch.setattr(subbot, "alert_owner", lambda *a, **kw: None)
    users = {"1": {"paid_until": 0}}
    store.record_payment(provider="crypto", external_id="ok", uid="1", days=30)
    store.mark_payment_granted("crypto", "ok", 123.0)
    subbot.reconcile_payments(users)
    assert users["1"]["paid_until"] == 0


def test_refund_of_expired_payment_keeps_current_subscription(env, monkeypatch):
    monkeypatch.setattr(subbot, "reply", lambda *a, **kw: None)
    monkeypatch.setattr(subbot, "alert_owner", lambda *a, **kw: None)
    now = time.time()
    users = {"1": {"paid_until": now + 30 * 86400}}
    store.record_payment(provider="stars", external_id="old", uid="1", days=365)
    con = store.connect()
    con.execute("UPDATE payments SET created_at=? WHERE external_id='old'",
                (now - 400 * 86400,))
    con.close()
    up = {"message": {"chat": {"id": 1},
                      "refunded_payment": {"telegram_payment_charge_id": "old"}}}
    subbot.handle_update(users, {}, up)
    assert users["1"]["paid_until"] > now + 29 * 86400


def test_trial_not_for_ex_payer(env):
    store.record_payment(provider="stars", external_id="paid-1", uid="1", days=30)
    assert subbot.trial_available({"1": {}}, "1") is False
