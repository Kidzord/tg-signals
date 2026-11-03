"""Консенсус внешних источников: разбор, дедуп, голосование, fail-open."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config as cfg  # noqa: E402
import consensus  # noqa: E402

NOW = 1_800_000_000.0


@pytest.fixture()
def store(tmp_path, monkeypatch):
    target = tmp_path / "consensus.jsonl"
    monkeypatch.setattr(cfg, "CONSENSUS_PATH", str(target))
    monkeypatch.setattr(cfg, "CONSENSUS_ENABLED", True)
    monkeypatch.setattr(cfg, "CONSENSUS_MODE", "warn")
    monkeypatch.setattr(cfg, "CONSENSUS_MIN_AGREE", 2)
    monkeypatch.setattr(cfg, "CONSENSUS_WINDOW_MIN", 60)
    monkeypatch.setattr(cfg, "CONSENSUS_MAX_AGE_MIN", 240)
    monkeypatch.setattr(cfg, "CONSENSUS_SOURCES", ())
    monkeypatch.setattr(cfg, "CONSENSUS_BLOCK_CONFLICT", True)
    return target


def add(source: str, text: str, ts: float = NOW) -> dict | None:
    return consensus.add_payload({"source": source, "text": text, "ts": ts}, now_ts=ts)


# --- нормализация ---------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("золото", "XAUUSD"),
    ("GOLD", "XAUUSD"),
    ("XAU/USD", "XAUUSD"),
    ("eurusd", "EURUSD"),
    ("EUR/USD", "EURUSD"),
    ("BTCUSDT", "BTCUSD"),
    ("dax", "DE40"),
    ("SIGNAL", None),
    ("", None),
    (None, None),
])
def test_normalize_symbol(raw, expected):
    assert consensus.normalize_symbol(raw) == expected


def test_normalize_side_ru_and_en():
    assert consensus.normalize_side("BUY") == "LONG"
    assert consensus.normalize_side("Лонг") == "LONG"
    assert consensus.normalize_side("продаём") == "SHORT"
    assert consensus.normalize_side("SELL LIMIT") == "SHORT"
    assert consensus.normalize_side("buy и sell") is None


# --- разбор текста --------------------------------------------------------

def test_parse_english_signal_with_levels():
    row = consensus.parse_external(
        "XAUUSD BUY 4020 SL 4005 TP 4050", "@chan", ts=NOW)
    assert row["symbol"] == "XAUUSD"
    assert row["side"] == "LONG"
    assert row["entry"] == 4020.0
    assert row["sl"] == 4005.0
    assert row["tp"] == 4050.0


def test_parse_russian_signal():
    row = consensus.parse_external("Продаём золото, стоп 4060, тейк 3980", "@ru", ts=NOW)
    assert row["symbol"] == "XAUUSD"
    assert row["side"] == "SHORT"
    assert row["sl"] == 4060.0
    assert row["tp"] == 3980.0


def test_parse_side_override_for_screenshot():
    row = consensus.parse_external("XAUUSD H4 Resistance 4050", "@chan",
                                   side_override="SHORT")
    assert row["symbol"] == "XAUUSD"
    assert row["side"] == "SHORT"
    assert consensus.parse_external("без тикера", "@chan", side_override="LONG") is None


def test_summary_text_counts_sources(store):
    add("@a", "XAUUSD BUY 4020", ts=NOW - 60)
    add("@b", "EURUSD SELL 1.09", ts=NOW - 30)
    text = consensus.summary_text(24, now_ts=NOW)
    assert "2 голосов" in text
    assert "2 источников" in text
    assert "XAUUSD" in text


def test_summary_text_empty(store):
    assert "нет" in consensus.summary_text(24, now_ts=NOW)


def test_parse_noise_and_ambiguous_returns_none():
    assert consensus.parse_external("Просто новость без сделки", "@chan") is None
    assert consensus.parse_external("XAUUSD buy и sell", "@chan") is None


# --- приём и дедуп --------------------------------------------------------

def test_add_requires_source(store):
    with pytest.raises(ValueError):
        consensus.add_payload({"text": "XAUUSD BUY 4020", "ts": NOW}, now_ts=NOW)


def test_add_ignores_non_signal(store):
    assert add("@chan", "сегодня без сделок") is None
    assert consensus._load() == []


def test_duplicate_from_same_source_not_stored_twice(store):
    first = add("@chan", "XAUUSD BUY 4020 SL 4005")
    second = add("@chan", "XAUUSD BUY 4020 SL 4005", ts=NOW + 60)
    assert first is not None
    assert second is None
    assert len(consensus._load()) == 1


def test_ingest_reports_errors_and_keeps_good_rows(store):
    res = consensus.ingest([
        {"source": "@a", "text": "XAUUSD BUY 4020", "ts": NOW},
        {"text": "без источника", "ts": NOW},
        {"source": "@b", "text": "шум", "ts": NOW},
    ], now_ts=NOW)
    assert res["stored"] == 1
    assert res["skipped"] == 1
    assert len(res["errors"]) == 1


# --- голосование ----------------------------------------------------------

def test_two_sources_confirm(store):
    add("@a", "XAUUSD BUY 4020", ts=NOW - 60)
    add("@b", "золото лонг 4021", ts=NOW - 30)
    vote = consensus.evaluate("XAUUSD", "LONG", NOW)
    assert vote["decision"] == "confirm"
    assert vote["for"] == 2


def test_one_source_twice_counts_once(store):
    add("@a", "XAUUSD BUY 4020", ts=NOW - 60)
    add("@a", "XAUUSD BUY 4021", ts=NOW - 50)
    vote = consensus.evaluate("XAUUSD", "LONG", NOW)
    assert vote["decision"] == "neutral"
    assert vote["for"] == 1


def test_conflict_when_sources_disagree(store):
    add("@a", "XAUUSD BUY 4020", ts=NOW - 60)
    add("@b", "золото лонг 4019", ts=NOW - 55)
    add("@c", "XAUUSD SELL 4010", ts=NOW - 40)
    add("@d", "золото шорт 4009", ts=NOW - 35)
    vote = consensus.evaluate("XAUUSD", "LONG", NOW)
    assert vote["decision"] == "conflict"
    assert vote["against"] == 2


def test_other_symbol_and_old_signal_do_not_vote(store):
    add("@a", "EURUSD BUY 1.0850", ts=NOW - 30)
    add("@b", "XAUUSD BUY 4020", ts=NOW - 5 * 3600)
    vote = consensus.evaluate("XAUUSD", "LONG", NOW)
    assert vote["decision"] == "neutral"
    assert vote["for"] == 0


def test_source_whitelist_filters_votes(store, monkeypatch):
    monkeypatch.setattr(cfg, "CONSENSUS_SOURCES", ("@trusted",))
    add("@trusted", "XAUUSD BUY 4020", ts=NOW - 60)
    add("@random", "XAUUSD SELL 4010", ts=NOW - 50)
    vote = consensus.evaluate("XAUUSD", "LONG", NOW)
    assert vote["for"] == 1
    assert vote["against"] == 0


# --- precheck и fail-open -------------------------------------------------

def test_precheck_filter_mode_blocks_conflict(store, monkeypatch):
    monkeypatch.setattr(cfg, "CONSENSUS_MODE", "filter")
    add("@a", "XAUUSD SELL 4010", ts=NOW - 60)
    add("@b", "золото шорт 4009", ts=NOW - 50)
    allowed, vote = consensus.precheck("XAUUSD", "LONG", NOW)
    assert allowed is False
    assert vote["decision"] == "conflict"


def test_precheck_warn_mode_never_blocks(store):
    add("@a", "XAUUSD SELL 4010", ts=NOW - 60)
    add("@b", "золото шорт 4009", ts=NOW - 50)
    allowed, vote = consensus.precheck("XAUUSD", "LONG", NOW)
    assert allowed is True
    assert vote["decision"] == "conflict"


def test_precheck_disabled_is_off(store, monkeypatch):
    monkeypatch.setattr(cfg, "CONSENSUS_ENABLED", False)
    allowed, vote = consensus.precheck("XAUUSD", "LONG", NOW)
    assert allowed is True
    assert vote["decision"] == "off"


def test_precheck_fails_open_on_error(store, monkeypatch):
    monkeypatch.setattr(cfg, "CONSENSUS_MODE", "filter")

    def boom(*_a, **_kw):
        raise OSError("диск отвалился")

    monkeypatch.setattr(consensus, "evaluate", boom)
    allowed, vote = consensus.precheck("XAUUSD", "LONG", NOW)
    assert allowed is True
    assert vote["decision"] == "error"


def test_post_signal_respects_consensus_block(store, monkeypatch):
    """Бот обязан остановиться до рендера карточки, если фильтр запретил вход."""
    import bot

    monkeypatch.setattr(bot.consensus, "precheck", lambda *_a, **_k: (
        False, {"decision": "conflict", "against": 2,
                "sources_against": ["@a", "@b"], "for": 0}))
    monkeypatch.setattr(bot.risk_guard, "pause_state",
                        lambda *_a, **_k: {"paused": False, "reason": ""})
    monkeypatch.setattr(bot.news_calendar, "blackout",
                        lambda *_a, **_k: (False, None))
    monkeypatch.setattr(bot, "open_trades_in_group", lambda *_a, **_k: 0)
    monkeypatch.setattr(bot, "spread_guard_ok", lambda *_a, **_k: True)

    sig = bot.card.Signal(side="LONG", symbol="XAUUSD", tf="15",
                          entry=4000.0, sl=3990.0, tp1=4020.0)
    assert bot.post_signal(sig, chart=None) is False


# --- приём пересылкой в premium-бот --------------------------------------

def test_forward_source_new_and_old_format():
    import subbot

    assert subbot.forward_source({
        "forward_origin": {"type": "channel",
                           "chat": {"username": "chan", "title": "Канал"}}}) == "@chan"
    assert subbot.forward_source({
        "forward_origin": {"type": "channel", "chat": {"title": "Закрытый"}}}) == "Закрытый"
    assert subbot.forward_source({
        "forward_origin": {"type": "hidden_user",
                           "sender_user_name": "Аноним"}}) == ""
    assert subbot.forward_source({"forward_from_chat": {"username": "old"}}) == "@old"
    assert subbot.forward_source({}) == ""


def test_intake_forward_ignores_non_owner(store, monkeypatch):
    import subbot

    monkeypatch.setattr(cfg, "OWNER_CHAT_ID", "42")
    msg = {"forward_origin": {"type": "channel", "chat": {"username": "chan"}},
           "text": "XAUUSD BUY 4020"}
    assert subbot.intake_forward(msg, "99") is False
    assert consensus._load() == []


def test_intake_forward_saves_signal(store, monkeypatch):
    import subbot

    monkeypatch.setattr(cfg, "OWNER_CHAT_ID", "42")
    replies = []
    monkeypatch.setattr(subbot, "reply", lambda uid, text: replies.append(text))
    msg = {"forward_origin": {"type": "channel", "chat": {"username": "chan"}},
           "text": "XAUUSD BUY 4020 SL 4005"}
    assert subbot.intake_forward(msg, "42") is True
    rows = consensus._load()
    assert len(rows) == 1
    assert rows[0]["source"] == "@chan"
    assert "✅" in replies[0]


def test_intake_forward_rejects_hidden_source(store, monkeypatch):
    import subbot

    monkeypatch.setattr(cfg, "OWNER_CHAT_ID", "42")
    replies = []
    monkeypatch.setattr(subbot, "reply", lambda uid, text: replies.append(text))
    msg = {"forward_origin": {"type": "hidden_user", "sender_user_name": "Аноним"},
           "text": "XAUUSD BUY 4020"}
    assert subbot.intake_forward(msg, "42") is True
    assert consensus._load() == []
    assert "скрыт" in replies[0]


def test_intake_forward_ignores_plain_owner_message(store, monkeypatch):
    import subbot

    monkeypatch.setattr(cfg, "OWNER_CHAT_ID", "42")
    assert subbot.intake_forward({"text": "XAUUSD BUY 4020"}, "42") is False


def test_precheck_reads_broken_file_without_raising(store, monkeypatch):
    store.write_text("не json\n" + json.dumps(
        {"symbol": "XAUUSD", "side": "SHORT", "source": "@a", "ts": NOW - 10}) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(cfg, "CONSENSUS_MODE", "filter")
    allowed, vote = consensus.precheck("XAUUSD", "LONG", NOW)
    assert allowed is True          # одного голоса мало для порога 2
    assert vote["against"] == 1
