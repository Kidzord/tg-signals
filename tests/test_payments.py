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
