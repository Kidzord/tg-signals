"""Редактор Threads: текст без HTML, два сообщения, кнопки и /post."""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import subbot  # noqa: E402
import tg  # noqa: E402
import threads_editor  # noqa: E402
import threads_post  # noqa: E402


def test_finish_strips_html_and_fits_limit():
    text = threads_editor._finish("<b>Заголовок</b>\n" + "строка " * 200)
    assert "<b>" not in text
    assert len(text.encode("utf-8")) <= 500
    assert "t.me/" in text


def test_send_next_sends_photo_and_plain_text(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(threads_post, "next_unused", lambda: 1)
    monkeypatch.setattr(threads_post, "mark_used", lambda n: calls.append(("used", n)))
    monkeypatch.setattr(threads_editor.tg, "send_photo_result",
                        lambda *a, **kw: calls.append(("photo", a[0])) or tg.ApiResult(True))
    monkeypatch.setattr(threads_editor.tg, "send_plain_result",
                        lambda *a, **kw: calls.append(("text", a[0])) or tg.ApiResult(True))
    assert threads_editor.send_next("42", "token") is True
    assert [c[0] for c in calls] == ["photo", "text", "used"]
    assert calls[1][1].startswith(threads_post.POSTS[0]["text"][:20])


def test_send_signal_reads_queue(tmp_path, monkeypatch):
    path = tmp_path / "signals.jsonl"
    path.write_text(json.dumps({
        "ts": 1, "kind": "entry", "trade_id": "t1", "symbol": "XAUUSD",
        "side": "LONG", "path": "", "caption": "<b>LONG XAUUSD</b> вход 4000",
    }), encoding="utf-8")
    monkeypatch.setattr(threads_editor, "SIGNALS_PATH", str(path))
    sent: list[str] = []
    monkeypatch.setattr(threads_editor.tg, "send_plain_result",
                        lambda text, **kw: sent.append(text) or tg.ApiResult(True))
    assert threads_editor.send_signal("42", "token") is True
    assert "XAUUSD" in sent[0]
    assert "<b>" not in sent[0]


def test_send_result_starts_with_day_number(tmp_path, monkeypatch):
    trade_img = tmp_path / "card.png"
    trade_img.write_bytes(b"\x89PNG fake card")
    path = tmp_path / "trades.jsonl"
    path.write_text(json.dumps({
        "ts": 1, "kind": "sl", "symbol": "AUDUSD", "side": "SHORT",
        "total_r": -1.0, "image": str(trade_img),
    }), encoding="utf-8")
    monkeypatch.setattr(threads_editor, "TRADES_PATH", str(path))
    sent: list[str] = []
    monkeypatch.setattr(threads_editor.tg, "send_photo_result",
                        lambda *a, **kw: tg.ApiResult(True))
    monkeypatch.setattr(threads_editor.tg, "send_plain_result",
                        lambda text, **kw: sent.append(text) or tg.ApiResult(True))
    assert threads_editor.send_result("42", "token") is True
    assert sent and sent[0].startswith("День "), "пост сделки открывает счётчик дня"
    assert "AUDUSD" in sent[0] and "-1.0R" in sent[0]


def test_owner_post_command_calls_editor(monkeypatch):
    monkeypatch.setattr(subbot.cfg, "OWNER_CHAT_ID", "42")
    called: list[str] = []
    monkeypatch.setattr(subbot.threads_editor, "send_next",
                        lambda uid, token, number=None: called.append(f"next:{number}") or True)
    subbot.handle({}, "42", "/post")
    assert called == ["next:None"]
