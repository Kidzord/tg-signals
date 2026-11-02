"""Зеркало контентных постов: копия владельцу в личку premium-ботом."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import autoposts  # noqa: E402


def test_mirror_sends_photo_and_text_to_owner(tmp_path, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(autoposts, "owner_chat", lambda: "42")
    monkeypatch.setattr(autoposts.tg, "send_photo",
                        lambda p, t, **kw: calls.append(("photo", p, t, kw)) or (True, "ok"))
    monkeypatch.setattr(autoposts.tg, "send_message",
                        lambda t, **kw: calls.append(("msg", t, kw)) or (True, "ok"))

    img = tmp_path / "card.png"
    img.write_bytes(b"png")
    autoposts._mirror(str(img), "план")
    autoposts._mirror(None, "текст")
    autoposts._mirror(str(tmp_path / "нет.png"), "файла нет")

    assert calls[0][0] == "photo" and calls[0][1] == str(img)
    assert calls[0][3]["chat_id"] == "42"
    assert calls[0][3]["token"], "копия уходит от premium-бота"
    assert calls[1][0] == "msg"
    assert calls[2][0] == "msg", "несуществующий файл уходит текстом"


def test_mirror_silently_skips_without_owner(monkeypatch):
    monkeypatch.setattr(autoposts, "owner_chat", lambda: "")
    sent: list[str] = []
    monkeypatch.setattr(autoposts.tg, "send_message",
                        lambda t, **kw: sent.append(t) or (True, "ok"))
    autoposts._mirror(None, "некому")
    assert sent == []
