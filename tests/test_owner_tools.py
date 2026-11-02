"""Инструменты владельца и воронки: /feedback, /refund, growth-отчёт, месяц."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import autoposts  # noqa: E402
import store  # noqa: E402
import subbot  # noqa: E402
import tg  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "state.db"))
    store.init_db()
    yield


def _users(monkeypatch, users: dict):
    users_path = os.path.join(autoposts.HERE, "users.json")
    real_load = autoposts.load_json

    def fake_load(path: str) -> dict:
        if path == users_path:
            return users
        return real_load(path)

    monkeypatch.setattr(autoposts, "load_json", fake_load)


def test_payment_by_ref_finds_by_id_and_external(db):
    store.record_payment(provider="stars", external_id="CHG-1", uid="77",
                         amount=500, currency="XTR", days=30, source="src_t")
    row = store.payment_by_ref("1")
    assert row and row["external_id"] == "CHG-1"
    assert store.payment_by_ref("CHG-1")["uid"] == "77"
    assert store.payment_by_ref("нет такого") is None


def test_refund_stars_calls_api_and_marks_refunded(db, monkeypatch):
    store.record_payment(provider="stars", external_id="CHG-2", uid="77",
                         amount=500, currency="XTR", days=30, source="")
    sent: list[tuple] = []
    monkeypatch.setattr(subbot, "reply",
                        lambda uid, text: sent.append(("reply", uid, text)))
    monkeypatch.setattr(subbot.tg, "api_call",
                        lambda method, data, **kw: sent.append(("api", method, data))
                        or tg.ApiResult(True))
    subbot._refund_stars("42", "1")
    assert sent[0][:2] == ("api", "refundStarPayment")
    assert sent[0][2]["telegram_payment_charge_id"] == "CHG-2"
    assert store.payment_by_ref("1")["status"] == "refunded"
    assert sent[1][0] == "reply"


def test_refund_rejects_already_refunded(db, monkeypatch):
    store.record_payment(provider="stars", external_id="CHG-3", uid="77",
                         amount=500, currency="XTR", days=30, source="")
    store.mark_payment_refunded(provider="stars", external_id="CHG-3")
    calls: list[str] = []
    monkeypatch.setattr(subbot, "reply", lambda uid, text: calls.append(text))
    monkeypatch.setattr(subbot.tg, "api_call",
                        lambda *a, **kw: calls.append("api") or tg.ApiResult(True))
    subbot._refund_stars("42", "CHG-3")
    assert "api" not in calls, "повторного вызова провайдера быть не должно"
    assert "refunded" in calls[0] or "статусе" in calls[0]


def test_feedback_routes_to_owner_with_source(monkeypatch):
    users = {"55": {"trial_used": True, "source": "src_threads"}}
    sent: list[tuple] = []
    monkeypatch.setattr(subbot, "reply",
                        lambda uid, text: sent.append(("reply", text)))
    monkeypatch.setattr(subbot.tg, "send_message",
                        lambda text, **kw: sent.append(("owner", text, kw)))
    subbot._send_feedback(users, "55", "/feedback сломался /maxday, почини")
    owner = next(x for x in sent if x[0] == "owner")
    assert "src_threads" in owner[1] and "триал" in owner[1]
    assert "сломался" in owner[1]
    assert "<code>55</code>" in owner[1]
    assert owner[2]["chat_id"] == subbot.cfg.OWNER_CHAT_ID
    assert any(x[0] == "reply" for x in sent), "тестеру подтверждают приём"


def test_feedback_escapes_html(monkeypatch):
    users: dict = {}
    captured: dict = {}
    monkeypatch.setattr(subbot, "reply", lambda uid, text: None)
    monkeypatch.setattr(subbot.tg, "send_message",
                        lambda text, **kw: captured.update(text=text))
    subbot._send_feedback(users, "55", "/feedback <b>инжект</b> & >")
    assert "<b>инжект</b>" not in captured["text"]
    assert "&lt;b&gt;" in captured["text"]


def test_growth_report_counts_sources_and_payments(db, tmp_path, monkeypatch):
    now = dt.datetime.now(autoposts.TZ)
    users = {
        "1": {"paid_until": now.timestamp() + 86400, "source": "src_threads"},
        "2": {"paid_until": 0, "source": "src_tiktok", "trial_used": True},
        "3": {"source": "src_threads"},
    }
    _users(monkeypatch, users)
    store.record_payment(provider="stars", external_id="G1", uid="1",
                         amount=500, currency="XTR", days=30, source="src_threads")
    sent: dict = {}
    monkeypatch.setattr(autoposts, "owner_chat", lambda: "42")
    monkeypatch.setattr(autoposts.tg, "send_message",
                        lambda text, **kw: sent.update(text=text, kw=kw) or (True, "ok"))
    assert autoposts.post_growth(now) is True
    text = sent["text"]
    assert "Контакты: 3" in text and "платящих: 1" in text
    assert "src_threads 2" in text
    assert "Оплаты за 7 дней: 1 на 500 $" in text
    assert sent["kw"]["chat_id"] == "42"


def test_post_month_sums_previous_calendar_month(db, monkeypatch):
    now = dt.datetime(2026, 12, 1, 12, 0, tzinfo=autoposts.TZ)
    trades = [
        {"ts": now.timestamp() - 86400 * 40, "r": 3.0, "kind": "tp", "trade_id": "a"},
        {"ts": now.timestamp() - 86400 * 20, "r": -1.0, "kind": "sl", "trade_id": "b"},
        {"ts": now.timestamp() - 86400 * 5, "r": 2.0, "kind": "tp", "trade_id": "c"},
    ]
    monkeypatch.setattr(autoposts, "load_trades", lambda: trades)
    sent: dict = {}
    monkeypatch.setattr(autoposts.tg, "send_photo",
                        lambda path, caption, **kw: sent.update(caption=caption)
                        or (True, "ok"))
    monkeypatch.setattr(autoposts.tg, "send_message",
                        lambda text, **kw: sent.update(text=text) or (True, "ok"))
    monkeypatch.setattr(autoposts, "_mirror", lambda *a, **kw: None)
    assert autoposts.post_month(now) is True
    caption = sent.get("caption") or sent.get("text") or ""
    assert "Месяц в цифрах" in caption
    assert "1–30 ноября" in caption, "берётся предыдущий календарный месяц"
    assert "С начала счёта" in caption


def test_month_not_in_catchup():
    """«Месяц в цифрах» гоняет только 1-го числа — догон не должен его трогать."""
    src = open(os.path.join(autoposts.HERE, "autoposts.py"), encoding="utf-8").read()
    assert '"backup", "cleanup", "month"' in src
