"""Telegram-посты (превью/публикация) и карточка результата с графиком."""
from __future__ import annotations

import datetime as dt
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import feeds  # noqa: E402
import render  # noqa: E402
import telegram_editor  # noqa: E402
import telegram_posts  # noqa: E402
import tg  # noqa: E402


def test_telegram_posts_fit_caption_limit():
    for i, post in enumerate(telegram_posts.POSTS, 1):
        assert post["title"], f"#{i}: нет заголовка"
        assert len(post["text"].encode("utf-8")) <= 1024, f"#{i}: длиннее caption"


def test_preview_sends_photo_and_marks_used(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(telegram_posts, "next_unused", lambda: 1)
    monkeypatch.setattr(telegram_posts, "mark_used", lambda n: calls.append(("used", n)))
    monkeypatch.setattr(telegram_editor.tg, "send_photo_result",
                        lambda *a, **kw: calls.append(("photo", a[0])) or tg.ApiResult(True))
    assert telegram_editor.send_next("42", "tok") is True
    assert [c[0] for c in calls] == ["photo", "used"]


def test_publish_goes_to_group(monkeypatch):
    monkeypatch.setattr(telegram_editor.cfg, "CHAT_ID", "-1001")
    monkeypatch.setattr(telegram_editor.cfg, "BOT_TOKEN", "tok")
    sent: list[tuple] = []
    monkeypatch.setattr(telegram_editor.tg, "send_photo_result",
                        lambda image, text, **kw: sent.append((image, text, kw.get("chat_id")))
                        or tg.ApiResult(True))
    monkeypatch.setattr(telegram_posts, "mark_used", lambda n: None)
    assert telegram_editor.publish(2, "42", "tok") is True
    assert sent and sent[0][2] == "-1001"


def test_trade_chart_renders(tmp_path):
    now = dt.datetime.now(dt.timezone.utc)
    bars = []
    price = 4000.0
    for i in range(60):
        o = price
        c = price + (2 if i % 3 else -1)
        bars.append(feeds.Bar(now - dt.timedelta(minutes=15 * (60 - i)),
                              o, max(o, c) + 1, min(o, c) - 1, c))
        price = c
    out = tmp_path / "chart.png"
    render.render_trade_chart(
        symbol="XAUUSD", side="LONG", kind="tp", r=1.5,
        entry=4000.0, stop=3990.0, exit_px=4015.0, bars=bars, tf="15",
        entry_idx=40, exit_idx=55, out_path=str(out),
        tp1=4005.0, tp2=4010.0, tp3=4015.0, note="Цель взята")
    assert out.exists() and out.stat().st_size > 10_000


def test_trade_levels_dedupe_single_target():
    # range/sonar/ict: одна цель лежит в tp1/tp2/tp3 — рисуем только TP1
    levels = render._trade_levels(0.70107, 0.70107, 0.70107,
                                  0.70263, 0.70224, 0.70263, "#f00")
    names = [n for n, _, _, _ in levels]
    assert names.count("TP1") == 1
    assert "TP2" not in names and "TP3" not in names
    assert "Выход" not in names  # выход совпал со стопом
    assert "SL" in names and "Вход" in names


def test_trade_levels_keeps_distinct_targets():
    levels = render._trade_levels(100.0, 105.0, 110.0,
                                  90.0, 95.0, 112.0, "#f00")
    names = [n for n, _, _, _ in levels]
    assert {"TP1", "TP2", "TP3", "SL", "Вход", "Выход"} <= set(names)
