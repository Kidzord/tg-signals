"""Discord-приём: текст, OCR скриншота, очередь проверки, дубли."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config as cfg  # noqa: E402
import consensus  # noqa: E402
import discord_intake as di  # noqa: E402

NOW = 1_800_000_000.0


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONSENSUS_PATH", str(tmp_path / "consensus.jsonl"))
    monkeypatch.setattr(di, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(di, "REVIEW_PATH", str(tmp_path / "review.jsonl"))
    monkeypatch.setattr(di, "ARCHIVE_PATH", str(tmp_path / "trades.jsonl"))
    monkeypatch.setattr(di, "INBOX", str(tmp_path / "inbox"))
    return tmp_path


def image(tmp_path: Path, name: str = "shot.png") -> dict:
    path = tmp_path / name
    path.write_bytes(b"fake-png")
    return {"path": str(path), "url": "https://cdn.example/shot.png"}


def test_text_message_stored(env):
    res = di.process_message("discord:g#ideas", "XAUUSD BUY 4020 SL 4005",
                             [], message_id="1", now_ts=NOW)
    assert res["status"] == "stored"
    assert res["via"] == "text"
    rows = consensus._load()
    assert rows[0]["source"] == "discord:g#ideas"
    assert rows[0]["symbol"] == "XAUUSD"


def test_screenshot_parsed_by_ocr(env, tmp_path):
    seen = []

    def fake_ocr(path):
        seen.append(path)
        return "XAUUSD  Long Position\nEntry 4020 SL 4005"

    res = di.process_message("discord:g#ideas", "", [image(tmp_path)],
                             message_id="2", now_ts=NOW, ocr=fake_ocr)
    assert res["status"] == "stored"
    assert res["via"] == "ocr"
    assert seen
    assert consensus._load()[0]["side"] == "LONG"


def test_unparsed_screenshot_goes_to_review(env, tmp_path):
    res = di.process_message("discord:g#chat", "просто болтовня", [image(tmp_path)],
                             message_id="3", url="https://discord/3",
                             now_ts=NOW, ocr=lambda _p: "ничего полезного")
    assert res["status"] == "review"
    assert res["ocr"] is True
    review = [json.loads(x) for x in Path(di.REVIEW_PATH).read_text(
        encoding="utf-8").splitlines()]
    assert review[0]["source"] == "discord:g#chat"
    assert review[0]["message_id"] == "3"
    assert review[0]["images"]
    assert consensus._load() == []


def test_review_without_ocr_backend(env, tmp_path):
    res = di.process_message("discord:g#chat", "", [image(tmp_path)],
                             message_id="4", now_ts=NOW, ocr=lambda _p: "")
    assert res["status"] == "review"
    assert res["ocr"] is False


def test_ocr_exception_does_not_break(env, tmp_path):
    def boom(_path):
        raise RuntimeError("модель не загрузилась")

    res = di.process_message("discord:g#chat", "", [image(tmp_path)],
                             message_id="5", now_ts=NOW, ocr=boom)
    assert res["status"] == "review"


def test_duplicate_same_source_not_stored_twice(env):
    first = di.process_message("discord:g#a", "XAUUSD BUY 4020", [], now_ts=NOW)
    second = di.process_message("discord:g#a", "XAUUSD BUY 4020", [], now_ts=NOW + 30)
    assert first["status"] == "stored"
    assert second["status"] == "duplicate"
    assert len(consensus._load()) == 1


def test_is_image_by_type_or_extension():
    class Att:
        def __init__(self, filename="", content_type=""):
            self.filename = filename
            self.content_type = content_type

    assert di.is_image(Att(content_type="image/png"))
    assert di.is_image(Att(filename="chart.PNG"))
    assert not di.is_image(Att(filename="report.pdf", content_type="application/pdf"))


def test_channel_ids_parsing(env, monkeypatch):
    monkeypatch.setattr(cfg, "DISCORD_CHANNELS", ("123", " 456 ", "мусор"))
    assert di._channel_ids() == {123, 456}


class _Fake:
    """Минимальная заглушка discord-объекта для проверки склейки."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _msg(msg_id: int = 1, channel_id: int = 123, content: str = "XAUUSD BUY 4020"):
    return _Fake(
        id=msg_id,
        author=_Fake(bot=False),
        channel=_Fake(id=channel_id, name="ideas"),
        guild=_Fake(name="server"),
        content=content,
        embeds=[],
        attachments=[],
        jump_url="https://discord/1",
    )


def test_handle_ignores_other_channels(env, monkeypatch):
    import asyncio

    monkeypatch.setattr(cfg, "DISCORD_CHANNELS", ("999",))
    called = []
    monkeypatch.setattr(di, "process_message", lambda *a, **k: called.append(a))
    asyncio.run(di._handle(_msg(), {"seen": []}))
    assert called == []


def test_handle_ignores_channels_when_list_empty(env, monkeypatch):
    import asyncio

    monkeypatch.setattr(cfg, "DISCORD_CHANNELS", ())
    called = []
    monkeypatch.setattr(di, "process_message", lambda *a, **k: called.append(a))
    asyncio.run(di._handle(_msg(), {"seen": []}))
    assert called == []


def test_handle_dispatches_and_remembers(env, monkeypatch):
    import asyncio

    monkeypatch.setattr(cfg, "DISCORD_CHANNELS", ("123",))
    calls = []

    def fake_process(source, text, images, **kw):
        calls.append((source, text, kw.get("message_id")))
        return {"status": "stored", "symbol": "XAUUSD", "side": "LONG", "via": "text"}

    monkeypatch.setattr(di, "process_message", fake_process)
    seen = {"seen": []}
    asyncio.run(di._handle(_msg(), seen))
    assert calls == [("discord:server#ideas", "XAUUSD BUY 4020", "1")]
    assert "1" in seen["seen"]

    asyncio.run(di._handle(_msg(), seen))     # повторный апдейт — пропуск
    assert len(calls) == 1


def test_review_row_keeps_symbol(env, tmp_path):
    res = di.process_message("discord:g#ideas", "", [image(tmp_path)],
                             message_id="7", now_ts=NOW,
                             ocr=lambda _p: "XAUUSD H4 resistance")
    assert res["status"] == "review"
    assert res["row"]["symbol"] == "XAUUSD"
    assert di.review_count() == 1


def test_resolve_review_with_side_stores_vote(env, tmp_path):
    di.process_message("discord:g#ideas", "", [image(tmp_path)],
                       message_id="8", now_ts=NOW,
                       ocr=lambda _p: "XAUUSD H4 resistance")
    res = di.resolve_review("8", "LONG")
    assert res["ok"] is True
    rows = consensus._load()
    assert rows[0]["symbol"] == "XAUUSD"
    assert rows[0]["side"] == "LONG"
    assert di.review_count() == 0


def test_resolve_review_skip_removes_without_vote(env, tmp_path):
    di.process_message("discord:g#chat", "", [image(tmp_path)],
                       message_id="9", now_ts=NOW, ocr=lambda _p: "?" * 5)
    res = di.resolve_review("9", None)
    assert res["ok"] is True
    assert consensus._load() == []
    assert di.review_count() == 0


def test_resolve_review_unknown_id(env):
    res = di.resolve_review("нет-такого", "LONG")
    assert res["ok"] is False


def test_resolve_review_without_symbol_keeps_item(env):
    # строка без тикера: подтвердить сторону нельзя, элемент остаётся в очереди
    di.process_message("discord:g#chat", "просто текст", [],
                       message_id="10", now_ts=NOW)
    res = di.resolve_review("10", "LONG")
    assert res["ok"] is False
    assert "тикер" in res["error"]
    assert di.review_count() == 1


def test_notify_review_sends_photo_with_buttons(env, tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(cfg, "OWNER_CHAT_ID", "42")
    monkeypatch.setattr(cfg, "SUB_BOT_TOKEN", "tok")
    import tg

    monkeypatch.setattr(tg, "send_photo", lambda path, caption, **kw: (
        sent.append(("photo", path, caption, kw)) or (True, "ok")))
    res = di.process_message("discord:g#i", "", [image(tmp_path)],
                             message_id="11", now_ts=NOW,
                             ocr=lambda _p: "XAUUSD H4")
    di.notify_review(res, "discord:g#i", "")
    assert sent and sent[0][0] == "photo"
    assert sent[0][3]["chat_id"] == "42"
    assert sent[0][3]["token"] == "tok"
    buttons = sent[0][3]["markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == [
        "drev:11:LONG", "drev:11:SHORT", "drev:11:SKIP"]


def test_owner_callback_stores_and_clears_buttons(env, monkeypatch):
    import subbot

    monkeypatch.setattr(cfg, "OWNER_CHAT_ID", "42")
    monkeypatch.setattr(di, "resolve_review", lambda mid, side: {
        "ok": True, "symbol": "XAUUSD", "stored": {"symbol": "XAUUSD"},
        "row": {"source": "discord:g#i", "symbol": "XAUUSD", "message_id": mid}})
    edits = []
    monkeypatch.setattr(subbot.tg, "edit_caption", lambda *a, **kw: (
        edits.append((a[2], kw.get("markup"))) or (True, "ok")))
    subbot.handle_callback({}, "42", "42", 555, "drev:8:LONG")
    assert edits and "записано" in edits[0][0]
    assert edits[0][1] == {"inline_keyboard": []}


def test_non_owner_cannot_resolve(env, monkeypatch):
    import subbot

    monkeypatch.setattr(cfg, "OWNER_CHAT_ID", "42")
    called = []
    monkeypatch.setattr(di, "resolve_review", lambda *a: called.append(a))
    subbot.handle_callback({}, "99", "99", 555, "drev:8:LONG")
    assert called == []


# --- архив разборов сделок ------------------------------------------------

@pytest.mark.parametrize("title,text,expected", [
    ("Разбор XAUUSD", "итог +2.5R", "win"),
    ("Разбор XAUUSD", "итог -1R", "loss"),
    ("Профитный вход", "", "win"),
    ("Убыточная сделка", "", "loss"),
    ("", "закрыл по стопу", "loss"),
    ("", "обычный текст", ""),
])
def test_detect_outcome(title, text, expected):
    assert di.detect_outcome(title, text) == expected


def test_detect_r():
    assert di.detect_r("результат +3R") == 3.0
    assert di.detect_r("минус 1,2R") == -1.2
    assert di.detect_r("без цифр") is None


def test_parse_link():
    assert di.parse_link(
        "https://discord.com/channels/111/222/333") == ("111", "222", "333")
    assert di.parse_link("https://discordapp.com/channels/1/2/3") == ("1", "2", "3")
    assert di.parse_link("https://t.me/x") is None


def test_build_archive_row_derives_fields():
    row = di.build_archive_row(
        source="discord:g#xau", message_id="55", url="u", author="v",
        text="Закрыл лонг по золоту +1.8R", title="Разбор: профит", ts=NOW)
    assert row["symbol"] == "XAUUSD"
    assert row["side"] == "LONG"
    assert row["outcome"] == "win"
    assert row["r"] == 1.8


def test_archive_rows_dedupes_by_message_id(env):
    row = di.build_archive_row(source="s", message_id="1", url="", author="a",
                               text="+1R", ts=NOW)
    assert di.archive_rows([row]) == 1
    assert di.archive_rows([row, dict(row, text="+2R")]) == 0
    assert len(di._load_archive()) == 1


def test_archive_stats_shape(env):
    di.archive_rows([
        di.build_archive_row(source="s", message_id="1", url="", author="a",
                             text="XAUUSD +2R", ts=NOW),
        di.build_archive_row(source="s", message_id="2", url="", author="a",
                             text="XAUUSD -1R", ts=NOW),
        di.build_archive_row(source="s", message_id="3", url="", author="a",
                             text="EURUSD без итога", ts=NOW),
    ])
    text = di.archive_stats()
    assert "профит 1" in text
    assert "убыток 1" in text
    assert "без метки 1" in text


def test_thread_inherits_parent_channel(env, monkeypatch):
    monkeypatch.setattr(cfg, "DISCORD_CHANNELS", ("100",))
    monkeypatch.setattr(cfg, "DISCORD_ARCHIVE_CHANNELS", ("200",))
    thread_signal = _Fake(id=101, parent_id=100, name="th")
    thread_archive = _Fake(id=201, parent_id=200, name="breakdown")
    plain = _Fake(id=999, parent_id=None, name="other")

    assert di._allowed_channel(thread_signal) is True
    assert di._in_archive_channel(thread_signal) is False
    assert di._allowed_channel(thread_archive) is True
    assert di._in_archive_channel(thread_archive) is True
    assert di._allowed_channel(plain) is False


def test_handle_routes_breakdowns_to_archive(env, monkeypatch):
    import asyncio

    monkeypatch.setattr(cfg, "DISCORD_ARCHIVE_CHANNELS", ("200",))
    monkeypatch.setattr(cfg, "DISCORD_CHANNELS", ())
    votes = []
    monkeypatch.setattr(di, "process_message", lambda *a, **k: votes.append(a))

    async def fake_archive(message, with_ocr=True):
        return {"source": "discord:g#breakdown", "symbol": "XAUUSD",
                "outcome": "win", "message_id": "21"}

    monkeypatch.setattr(di, "archive_discord_message", fake_archive)
    msg = _msg(msg_id=21, channel_id=201, content="разбор")
    msg.channel.parent_id = 200
    asyncio.run(di._handle(msg, {"seen": []}))
    assert votes == []
    rows = di._load_archive()
    assert rows and rows[0]["message_id"] == "21"


# --- скриншоты, пересланные в Telegram ------------------------------------

def test_has_media_and_caption_source():
    import subbot

    assert subbot._has_media({"photo": [{"file_id": "x"}]})
    assert subbot._has_media({"document": {"mime_type": "image/png"}})
    assert not subbot._has_media({"document": {"mime_type": "application/pdf"}})
    assert not subbot._has_media({})
    assert subbot._caption_source({"caption": "смотри #trades"}) == "#trades"
    assert subbot._caption_source({"caption": "@chan XAUUSD"}) == "@chan"
    assert subbot._caption_source({"caption": "без метки"}) == ""


def test_intake_media_ignores_non_owner(env, monkeypatch):
    import subbot

    monkeypatch.setattr(cfg, "OWNER_CHAT_ID", "42")
    called = []
    monkeypatch.setattr(subbot, "download_telegram_image",
                        lambda *a: called.append(a) or "")
    assert subbot.intake_media({"photo": [{"file_id": "x"}]}, "99") is False
    assert called == []


def test_intake_media_parsed_becomes_vote(env, tmp_path, monkeypatch):
    import subbot

    monkeypatch.setattr(cfg, "OWNER_CHAT_ID", "42")
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"img")
    monkeypatch.setattr(subbot, "download_telegram_image", lambda *a: str(shot))
    monkeypatch.setattr(subbot.ocr_text, "image_text",
                        lambda _p: "XAUUSD Long Position Entry 4020 SL 4005")
    replies = []
    monkeypatch.setattr(subbot, "reply", lambda uid, text: replies.append(text))

    msg = {"photo": [{"file_id": "x"}], "message_id": 5, "caption": "#trades"}
    assert subbot.intake_media(msg, "42") is True
    rows = consensus._load()
    assert rows and rows[0]["symbol"] == "XAUUSD"
    assert rows[0]["source"] == "#trades"
    assert any("✅" in r for r in replies)


def test_intake_media_unparsed_goes_to_review(env, tmp_path, monkeypatch):
    import subbot

    monkeypatch.setattr(cfg, "OWNER_CHAT_ID", "42")
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"img")
    monkeypatch.setattr(subbot, "download_telegram_image", lambda *a: str(shot))
    monkeypatch.setattr(subbot.ocr_text, "image_text", lambda _p: "XAUUSD H4")
    sent = []
    monkeypatch.setattr(subbot.tg, "send_message", lambda text, **kw: (
        sent.append((text, kw)) or (True, "ok")))

    msg = {"photo": [{"file_id": "x"}], "message_id": 6}
    assert subbot.intake_media(msg, "42") is True
    assert consensus._load() == []
    assert di.review_count() == 1
    assert sent and "drev:tg_6:LONG" in str(sent[0][1]["markup"])


def test_intake_media_breakdown_goes_to_archive(env, tmp_path, monkeypatch):
    import subbot

    monkeypatch.setattr(cfg, "OWNER_CHAT_ID", "42")
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"img")
    monkeypatch.setattr(subbot, "download_telegram_image", lambda *a: str(shot))
    monkeypatch.setattr(subbot.ocr_text, "image_text",
                        lambda _p: "XAUUSD закрыл лонг, итог +2.5R")
    replies = []
    monkeypatch.setattr(subbot, "reply", lambda uid, text: replies.append(text))

    msg = {"photo": [{"file_id": "x"}], "message_id": 7,
           "caption": "#trade разбор сделки"}
    assert subbot.intake_media(msg, "42") is True
    assert consensus._load() == []                 # голосом не стало
    rows = di._load_archive()
    assert rows and rows[0]["outcome"] == "win"
    assert rows[0]["r"] == 2.5
    assert any("архив" in r for r in replies)


def test_forward_with_media_only_defers_to_media_handler(env, monkeypatch):
    import subbot

    monkeypatch.setattr(cfg, "OWNER_CHAT_ID", "42")
    msg = {"forward_origin": {"type": "channel", "chat": {"username": "chan"}},
           "photo": [{"file_id": "x"}]}
    assert subbot.intake_forward(msg, "42") is False


def test_notify_review_skipped_without_owner(env, monkeypatch):
    monkeypatch.setattr(cfg, "OWNER_CHAT_ID", "")
    di.notify_review({"status": "review"}, "discord:g#a", "https://x")  # не падает
