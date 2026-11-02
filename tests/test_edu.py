"""Методички: содержание банка, подписи и навигация без Telegram."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import edu_posts  # noqa: E402


def test_lessons_have_title_and_slides():
    assert len(edu_posts.LESSONS) >= 10
    for lesson in edu_posts.LESSONS:
        assert lesson["title"].strip()
        assert 3 <= len(lesson["slides"]) <= 6
        for slide in lesson["slides"]:
            assert slide["title"].strip()
            assert slide["text"].strip()


def test_caption_fits_telegram_limit():
    for n in range(1, edu_posts.total() + 1):
        assert 0 < len(edu_posts.user_caption(n)) <= 1024


def test_index_lists_every_lesson():
    text = edu_posts.index_text()
    assert "/lesson" in text and "/lessons" in text
    for n, lesson in enumerate(edu_posts.LESSONS, 1):
        assert f"{n}. " in text
        assert lesson["title"] in text


def test_markup_opens_each_lesson():
    rows = edu_posts.lessons_markup()["inline_keyboard"]
    callbacks = [b["callback_data"] for row in rows for b in row]
    for n in range(1, edu_posts.total() + 1):
        assert f"edu:lesson:{n}" in callbacks
    assert "edu:next:0" in callbacks
    assert "main_menu" in callbacks


def test_next_after_wraps():
    assert edu_posts.next_after(1) == 2
    assert edu_posts.next_after(edu_posts.total()) == 1


class _Result:
    ok = True
    error = ""


def test_send_to_user_flow(monkeypatch, tmp_path):
    """Альбом уходит с подписью, а под ним — кнопка следующей методички."""
    images = []
    for i in range(5):
        path = tmp_path / f"{i}.jpg"
        path.write_bytes(b"jpg")
        images.append(str(path))

    sent: dict = {}
    monkeypatch.setattr(edu_posts.edu_cards, "render_lesson",
                        lambda lesson, index, out_dir: images)
    monkeypatch.setattr(
        edu_posts.tg, "send_media_group",
        lambda paths, caption="", token=None, chat_id=None:
        sent.update(paths=paths, caption=caption, chat_id=chat_id) or _Result())
    monkeypatch.setattr(
        edu_posts.tg, "send_plain",
        lambda text, token=None, chat_id=None, markup=None:
        sent.update(plain=text, markup=markup) or (True, "ok"))

    assert edu_posts.send_to_user("42", "token", 3)
    assert sent["chat_id"] == "42"
    assert len(sent["paths"]) == 5
    assert "Методичка 3" in sent["caption"]
    assert sent["markup"]["inline_keyboard"][0][0]["callback_data"] == "edu:lesson:4"


def test_send_to_user_rejects_bad_number(monkeypatch):
    plain: dict = {}
    monkeypatch.setattr(
        edu_posts.tg, "send_plain",
        lambda text, token=None, chat_id=None, markup=None:
        plain.update(text=text) or (True, "ok"))
    assert not edu_posts.send_to_user("42", "token", 999)
    assert "Нет методички" in plain["text"]
