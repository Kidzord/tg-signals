"""Nurture триала и двусторонняя рефералка."""
from __future__ import annotations

import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import store  # noqa: E402
import subbot  # noqa: E402
import tg  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(subbot, "USERS_PATH", str(tmp_path / "users.json"))
    monkeypatch.setattr(subbot.tg, "send_message", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(subbot, "alert_owner", lambda *a, **kw: None)
    store.init_db()
    yield


def test_grant_trial_schedules_two_steps(env):
    users: dict = {}
    subbot.grant_trial(users, "1")
    assert users["1"]["trial_used"] is True
    con = store.connect()
    try:
        steps = {r["step"] for r in con.execute("SELECT step FROM nurture")}
    finally:
        con.close()
    assert steps == {"trial_day1", "trial_day3"}


def test_nurture_day1_is_sent(env, monkeypatch):
    store.enqueue_nurture("1", "trial_day1", time.time() - 1)
    monkeypatch.setattr(subbot.tg, "send_message_result",
                        lambda *a, **kw: tg.ApiResult(True, result={"message_id": 1}))
    users = {"1": {"trial_used": 1, "paid_until": time.time() + 86400}}
    subbot.process_nurture(users)
    con = store.connect()
    try:
        row = con.execute("SELECT status FROM nurture WHERE uid='1'").fetchone()
    finally:
        con.close()
    assert row["status"] == "sent"


def test_referral_rewards_both_sides(env):
    now = time.time()
    users = {
        "1": {"paid_until": now + 86400},
        "2": {"ref_by": "1", "paid_until": now + 86400},
    }
    subbot.reward_referrer(users, "2")
    assert users["1"]["paid_until"] == pytest.approx(now + 86400 + 7 * 86400, abs=5)
    assert users["2"]["paid_until"] == pytest.approx(now + 86400 + 3 * 86400, abs=5)
    assert users["1"]["refs"] == 1
    totals = store.payment_totals(0)
    assert totals["ref:paid:n"] == 1
    assert totals["ref_friend:paid:n"] == 1

    # повторный вызов ничего не добавляет
    before = (users["1"]["paid_until"], users["2"]["paid_until"])
    subbot.reward_referrer(users, "2")
    assert (users["1"]["paid_until"], users["2"]["paid_until"]) == before
