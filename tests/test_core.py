"""Тесты фундамента: outbox, ledger платежей, трек-рекорд, отсутствие секретов."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import store  # noqa: E402
import track_record  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "state.db"))
    store.init_db()
    yield
    try:
        os.remove(store.DB_PATH)
    except OSError:
        pass


def _delivery(**kw):
    data = dict(uid="1", trade_id="t1", kind="entry", chat_id="1",
                caption="test", image_path=None)
    data.update(kw)
    return store.enqueue_delivery(**data)


def test_enqueue_is_idempotent(db):
    assert _delivery() is True
    assert _delivery() is False
    assert len(store.claim_deliveries()) == 1


def test_delivery_sent_records_message_id(db):
    assert _delivery()
    row = store.claim_deliveries()[0]
    store.mark_delivery_sent(row["id"], 42)
    assert store.entry_message_id("t1", "1") == 42
    assert store.delivery_stats()["sent"]["n"] == 1


def test_retry_is_not_claimed_before_due(db):
    assert _delivery()
    row = store.claim_deliveries()[0]
    store.mark_delivery_retry(row["id"], "429", 60)
    assert store.claim_deliveries() == []
    stats = store.delivery_stats()
    assert stats["pending"]["n"] == 1


def test_blocked_user_removes_pending(db):
    assert _delivery()
    store.mark_user_blocked("1", "blocked")
    assert store.claim_deliveries() == []
    assert store.delivery_stats()["blocked"]["n"] == 1


def test_payment_ledger_is_idempotent(db):
    assert store.record_payment(provider="stars", external_id="charge-1",
                                uid="1", amount=500, currency="XTR", days=30)
    assert not store.record_payment(provider="stars", external_id="charge-1",
                                    uid="1", amount=500, currency="XTR", days=30)
    assert store.mark_payment_refunded(provider="stars", external_id="charge-1")
    assert not store.mark_payment_refunded(provider="stars", external_id="charge-1")
    totals = store.payment_totals(0)
    assert totals["stars:refunded:n"] == 1


def test_group_outbox_respects_due_time(db):
    now = time.time()
    store.enqueue_group(trade_id="t1", kind="entry", path="", caption="entry",
                        due_ts=now + 600, entry=100.0, side="LONG")
    store.enqueue_group(trade_id="t1", kind="sl", path="", caption="sl",
                        due_ts=now, entry=100.0, side="LONG")
    rows = store.claim_group()
    assert [r["kind"] for r in rows] == ["sl"]
    con = store.connect()
    try:
        con.execute("UPDATE group_outbox SET due_ts=?, next_try=?", (now - 1, now - 1))
    finally:
        con.close()
    rows = store.claim_group()
    assert [r["kind"] for r in rows] == ["entry"]


def test_track_record_metrics(tmp_path, monkeypatch):
    path = tmp_path / "trades.jsonl"
    now = time.time()
    rows = [
        {"ts": now - 300, "r": 2.0, "kind": "tp3", "symbol": "XAUUSD", "strategy": "classic"},
        {"ts": now - 200, "r": -1.0, "kind": "sl", "symbol": "XAUUSD", "strategy": "classic"},
        {"ts": now - 100, "r": 0.5, "kind": "tp", "symbol": "AUDUSD", "strategy": "range"},
        {"ts": now - 50, "r": 9.0, "kind": "tp1", "symbol": "XAUUSD", "strategy": "classic"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    monkeypatch.setattr(track_record, "TRADES_PATH", str(path))
    m = track_record.metrics(track_record.load_trades())
    assert m["n"] == 3
    assert m["sum_r"] == pytest.approx(1.5)
    assert m["pf"] == pytest.approx(2.5)
    assert m["max_dd_r"] == pytest.approx(1.0)
    assert m["by_symbol"]["XAUUSD"]["n"] == 2
    assert "PF" in track_record.short_line(30)


TOKEN_RE = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b")
SCAN_SUFFIXES = {".py", ".bat", ".ps1", ".md", ".json", ".yml", ".yaml", ".ini"}


def test_no_hardcoded_telegram_tokens():
    bad: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SCAN_SUFFIXES:
            continue
        if any(part in {".git", "__pycache__", "logs", "renders", "mt5_ea"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if TOKEN_RE.search(text):
            bad.append(str(path.relative_to(ROOT)))
    assert not bad, f"токены в исходниках: {bad}"
