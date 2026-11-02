"""TikTok: карусель, JPEG 1080p, media group и команда /tt."""
from __future__ import annotations

import json
import os
import sys

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import subbot  # noqa: E402
import tg  # noqa: E402
import tiktok_editor  # noqa: E402


def test_caption_has_cta_and_hashtags():
    text = tiktok_editor.caption()
    assert "t.me/" in text
    assert "#трейдинг" in text
    assert len(text) <= 2200


def test_fit_converts_to_jpeg_1080(tmp_path, monkeypatch):
    src = tmp_path / "wide.png"
    Image.new("RGB", (1600, 900), (14, 17, 22)).save(src)
    monkeypatch.setattr(tiktok_editor, "OUT_DIR", str(tmp_path / "out"))
    dst = tiktok_editor._fit(str(src), "test")
    assert dst and dst.endswith(".jpg")
    with Image.open(dst) as im:
        assert im.format == "JPEG"
        assert max(im.size) <= 1080


def test_media_group_builds_multipart(tmp_path, monkeypatch):
    paths = []
    for i, color in enumerate(((10, 20, 30), (40, 50, 60))):
        p = tmp_path / f"{i}.jpg"
        Image.new("RGB", (20, 20), color).save(p, "JPEG")
        paths.append(str(p))
    captured: dict = {}

    def fake_api(method, data, files=None, token=None, timeout=30):
        captured.update(method=method, data=data, files=files)
        return tg.ApiResult(True, result=[])

    monkeypatch.setattr(tg, "api_call", fake_api)
    res = tg.send_media_group(paths, caption="<b>Тест</b>", chat_id="1", token="t")
    assert res.ok
    media = json.loads(captured["data"]["media"])
    assert len(media) == 2
    assert media[0]["media"] == "attach://photo0"
    assert media[0]["parse_mode"] == "HTML"
    assert set(captured["files"]) == {"photo0", "photo1"}


def test_owner_tt_command(monkeypatch):
    monkeypatch.setattr(subbot.cfg, "OWNER_CHAT_ID", "42")
    called: list[str] = []
    monkeypatch.setattr(subbot.tiktok_editor, "send",
                        lambda uid, token: called.append(uid) or True)
    subbot.handle({}, "42", "/tt")
    assert called == ["42"]

def test_story_slides_carry_journal_hook():
    slides = tiktok_editor._story_slides(7, "KidzordFX")
    titles = [s[1] for s in slides]
    assert titles[0] == "ДЕНЬ 7"
    bodies = " ".join(s[2] for s in slides)
    assert "написал не я" in bodies
    assert "ИЩУ ТЕСТЕРОВ" in titles
    assert "Telegram: @KidzordFX" in bodies
    # каждый слайд влезает в шаблон карточки: 2 строки заголовка, 7 строк текста
    for _, title, body, _ in slides:
        assert len(title) <= 32
        assert len(body.splitlines()) <= 5
