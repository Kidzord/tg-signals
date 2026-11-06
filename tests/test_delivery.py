"""Интеграционные тесты доставки: outbox → «Telegram» (заглушка)."""
from __future__ import annotations

import json
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


def test_result_sends_notification_and_edits_caption(db, monkeypatch):
    users = _user()
    assert store.enqueue_delivery(uid="1", trade_id="t1", kind="entry", chat_id="1",
                                  caption="Вход LONG", symbol="XAUUSD", side="LONG")
    sent: list = []

    def fake_send(text, **kw):
        sent.append(kw.get("reply_to"))
        mid = 100 if kw.get("reply_to") is None else 101
        return tg.ApiResult(True, result={"message_id": mid})

    monkeypatch.setattr(subbot.tg, "send_message_result", fake_send)
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
    # вход без reply, тейк — отдельным сообщением-ответом на карточку
    assert sent == [None, 100]
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
    # entry, затем отдельное сообщение-результат и обновление подписи карточки
    assert sent == ["msg", "msg", "edit"]


def test_send_message_uses_reply_to(monkeypatch):
    captured: dict = {}

    def fake_api(method, data, **kw):
        captured.update(data)
        return tg.ApiResult(True, result={"message_id": 5})

    monkeypatch.setattr(tg, "api_call", fake_api)
    res = tg.send_message_result("Тейк", chat_id="1", reply_to=100)
    assert res.ok
    assert captured["reply_to_message_id"] == "100"
    assert captured["allow_sending_without_reply"] == "true"


def test_waiting_for_entry_does_not_consume_attempts(db):
    store.enqueue_delivery(uid="1", trade_id="t5", kind="tp", chat_id="1",
                           caption="Тейк")
    row = store.claim_deliveries()[0]
    store.mark_delivery_retry(row["id"], "ждём карточку входа", 60,
                              count_attempt=False)
    with store.connect() as con:
        attempts = con.execute("SELECT attempts FROM deliveries WHERE id=?",
                               (row["id"],)).fetchone()[0]
    assert attempts == 0


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


# ------------------------------------------------- склейка итога с входом в группе

import bot  # noqa: E402
import risk_guard  # noqa: E402


def _group_entry(trade_id: str, due: float) -> bool:
    return store.enqueue_group(
        trade_id=trade_id, kind="entry", path="",
        caption="На AUDUSD сняли хай — шорт.\n\nРаботаю до цели.\n\n"
                "M15 · 02.11 16:15 сервер · MT5\n<i>Не рекомендация.</i>\n\n"
                "⚡ Premium",
        due_ts=due, entry=0.70042, side="SHORT", symbol="AUDUSD")


def _capture_group_sends(monkeypatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(bot.tg, "send_message_result",
                        lambda caption, **kw: (sent.append(caption), tg.ApiResult(True))[1])
    return sent


def test_group_result_glues_into_pending_entry(db, monkeypatch):
    """Сделка закрылась внутри FREE_DELAY_SEC — одна карточка «вход + итог»."""
    monkeypatch.setattr(bot, "SYMBOL", "AUDUSD")
    assert _group_entry("g1", time.time() - 10)
    sent = _capture_group_sends(monkeypatch)
    bot.deliver_group_result(trade_id="g1", kind="sl", r=-1.0, note="",
                             text="🛑 отдельный итог", path="",
                             entry=0.70042, side="SHORT")
    bot.flush_pending()
    assert len(sent) == 1, "склеенный итог не должен уходить отдельным сообщением"
    cap = sent[0]
    assert "Итог:" in cap and "отдельный итог" not in cap
    assert cap.index("Итог:") < cap.index("M15 ·"), "итог перед подвалом карточки"
    assert "⚡ Premium" in cap, "CTA остаётся в конце"


def test_partial_tp_is_not_glued_as_final_result(db, monkeypatch):
    monkeypatch.setattr(bot, "SYMBOL", "AUDUSD")
    assert _group_entry("g3", time.time() - 10)
    sent = _capture_group_sends(monkeypatch)
    bot.deliver_group_result(trade_id="g3", kind="tp1", r=1.0, note="",
                             text="✅ TP1 отдельно", path="",
                             entry=0.70042, side="SHORT")
    bot.flush_pending()
    assert "Итог:" not in sent[0]          # карточка входа осталась входом
    assert any("TP1 отдельно" in m for m in sent)


def test_append_outcome_does_not_touch_sending_entry(db):
    assert _group_entry("g4", time.time() - 10)
    row = store.claim_group()[0]           # pending -> sending (уже в полёте)
    assert not store.append_group_outcome("g4", "Итог: x")
    con = store.connect()
    try:
        cap = con.execute("SELECT caption FROM group_outbox WHERE id=?",
                          (row["id"],)).fetchone()[0]
    finally:
        con.close()
    assert "Итог:" not in cap


def test_group_result_separate_after_entry_sent(db, monkeypatch):
    monkeypatch.setattr(bot, "SYMBOL", "AUDUSD")
    assert _group_entry("g2", time.time() - 10)
    sent = _capture_group_sends(monkeypatch)
    bot.flush_pending()                       # вход уже ушёл
    assert store.group_pending_entry("g2") is None
    bot.deliver_group_result(trade_id="g2", kind="tp", r=3.0, note="",
                             text="✅ отдельный итог", path="",
                             entry=0.70042, side="SHORT")
    bot.flush_pending()
    assert len(sent) == 2
    assert sent[1] == "✅ отдельный итог"


def test_group_result_delayed_until_entry_due(db, monkeypatch):
    """Вход ещё ждёт задержку, итог не должен выйти раньше карточки."""
    monkeypatch.setattr(bot, "SYMBOL", "AUDUSD")
    assert _group_entry("g3", time.time() + 1800)
    sent = _capture_group_sends(monkeypatch)
    bot.flush_pending()                       # ничего не должно уйти
    assert sent == []
    bot.deliver_group_result(trade_id="g3", kind="sl", r=-1.0, note="",
                             text="🛑 отдельный итог", path="",
                             entry=0.70042, side="SHORT")
    bot.flush_pending()
    assert sent == [], "итог приклеился — отдельных сообщений нет"
    assert store.group_pending_count() == 1, "ждёт только отложенный вход"


def test_group_late_note_skipped_for_merged(db, monkeypatch):
    """На склеенной карточке «цена ушла» неуместна — сделка уже закрыта."""
    monkeypatch.setattr(bot, "SYMBOL", "AUDUSD")
    monkeypatch.setattr(bot, "_late_note", lambda row, data: "\n⚠️ Цена ушла")
    assert _group_entry("g4", time.time() - 10)
    assert store.append_group_outcome("g4", "✅ Итог: Цель взята, +3.00R.")
    sent = _capture_group_sends(monkeypatch)
    bot.flush_pending()
    assert sent and "Цена ушла" not in sent[0]


def test_append_group_outcome_false_after_entry_sent(db):
    assert _group_entry("g5", time.time() - 10)
    rows = store.claim_group()
    store.mark_group_sent(rows[0]["id"])
    assert store.append_group_outcome("g5", "🛑 Итог: Минус, -1.00R.") is False


def test_entry_cancelled_when_trade_already_closed(db, monkeypatch, tmp_path):
    """Сделка закрылась до отправки входа — устаревшую карточку не публикуем."""
    monkeypatch.setattr(bot, "SYMBOL", "AUDUSD")
    path = tmp_path / "trades.jsonl"
    path.write_text(json.dumps({"trade_id": "g6", "kind": "sl", "r": -1.0,
                                "ts": time.time()}) + "\n", encoding="utf-8")
    monkeypatch.setattr(risk_guard, "TRADES_PATH", str(path))
    assert _group_entry("g6", time.time() - 10)
    sent = _capture_group_sends(monkeypatch)
    bot.flush_pending()
    assert sent == []
    assert store.group_pending_entry("g6") is None


def test_group_caption_returns_glued_text(db):
    assert _group_entry("g7", time.time() + 600)
    row = store.group_pending_entry("g7")
    assert store.append_group_outcome("g7", "✅ Итог: Цель взята, +3.00R.")
    cap = store.group_caption(row["id"])
    assert cap and "Итог:" in cap
