"""Интеграционные тесты доставки: outbox → «Telegram» (заглушка)."""
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
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setattr(subbot, "USERS_PATH", str(tmp_path / "users.json"))
    monkeypatch.setattr(subbot, "alert_owner", lambda *a, **kw: None)
    store.init_db()
    yield


def _user(until: float | None = None) -> dict:
    return {"1": {"paid_until": until or time.time() + 86400}}


def test_entry_then_result_edits_caption(db, monkeypatch):
    users = _user()
    assert store.enqueue_delivery(uid="1", trade_id="t1", kind="entry", chat_id="1",
                                  caption="Вход LONG", symbol="XAUUSD", side="LONG")
    monkeypatch.setattr(subbot.tg, "send_message_result",
                        lambda *a, **kw: tg.ApiResult(True, result={"message_id": 100}))
    subbot.process_deliveries(users)
    assert store.entry_message_id("t1", "1") == 100

    assert store.enqueue_delivery(uid="1", trade_id="t1", kind="tp1", chat_id="1",
                                  caption="✅ TP1 взят · +1.0R")
    edited: dict = {}

    def fake_edit(chat_id, message_id, caption, **kw):
        edited.update(chat_id=chat_id, mid=message_id, caption=caption)
        return tg.ApiResult(True, result={"message_id": message_id})

    monkeypatch.setattr(subbot.tg, "edit_caption_result", fake_edit)
    subbot.process_deliveries(users)
    assert edited["mid"] == 100
    assert "TP1" in edited["caption"]
    stats = store.delivery_stats()
    assert stats["sent"]["n"] == 2


def test_result_waits_for_pending_entry(db, monkeypatch):
    users = _user()
    store.enqueue_delivery(uid="1", trade_id="t2", kind="entry", chat_id="1",
                           caption="Вход", symbol="XAUUSD", side="LONG")
    store.enqueue_delivery(uid="1", trade_id="t2", kind="sl", chat_id="1",
                           caption="🛑 Стоп")
    sent = []
    monkeypatch.setattr(subbot.tg, "send_message_result",
                        lambda *a, **kw: sent.append("msg") or tg.ApiResult(True, result={"message_id": 7}))
    monkeypatch.setattr(subbot.tg, "edit_caption_result",
                        lambda *a, **kw: sent.append("edit") or tg.ApiResult(True))
    subbot.process_deliveries(users)
    # entry ушёл первым, result отредактировал его, а не отправился отдельно
    assert sent == ["msg", "edit"]


def test_429_requeues_without_losing_message(db, monkeypatch):
    users = _user()
    store.enqueue_delivery(uid="1", trade_id="t3", kind="entry", chat_id="1",
                           caption="Вход", symbol="XAUUSD", side="LONG")
    monkeypatch.setattr(
        subbot.tg, "send_message_result",
        lambda *a, **kw: tg.ApiResult(False, error="Too Many Requests", retry_after=30))
    subbot.process_deliveries(users)
    stats = store.delivery_stats()
    assert stats["pending"]["n"] == 1
    assert store.claim_deliveries() == []


def test_blocked_user_is_excluded(db, monkeypatch):
    users = _user()
    store.enqueue_delivery(uid="1", trade_id="t4", kind="entry", chat_id="1",
                           caption="Вход", symbol="XAUUSD", side="LONG")
    monkeypatch.setattr(
        subbot.tg, "send_message_result",
        lambda *a, **kw: tg.ApiResult(False, error="Forbidden: bot was blocked by the user",
                                      status=403, blocked=True))
    subbot.process_deliveries(users)
    assert users["1"].get("blocked") == 1
    assert store.delivery_stats()["blocked"]["n"] == 1
