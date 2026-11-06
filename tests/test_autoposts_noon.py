"""Контент 12:00 по дням недели и промо 2 раза в неделю."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import autoposts  # noqa: E402
import risk_guard  # noqa: E402

MON = dt.datetime(2026, 11, 2, 12, 0)   # понедельник
TUE = dt.datetime(2026, 11, 3, 12, 0)
WED = dt.datetime(2026, 11, 4, 12, 0)
THU = dt.datetime(2026, 11, 5, 12, 0)
FRI = dt.datetime(2026, 11, 6, 12, 0)
SAT = dt.datetime(2026, 11, 7, 12, 0)
SUN = dt.datetime(2026, 11, 8, 12, 0)


def test_noon_mapping_by_weekday(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(autoposts, "_post_stats",
                        lambda d, t: (calls.append(("stats", d)), True)[1])
    monkeypatch.setattr(autoposts, "_post_last_trade",
                        lambda now=None: (calls.append(("trade",)), True)[1])
    monkeypatch.setattr(autoposts, "post_tg_content",
                        lambda: (calls.append(("bank",)), True)[1])
    assert autoposts.post_noon(MON) is True
    autoposts.post_noon(TUE)
    autoposts.post_noon(WED)
    autoposts.post_noon(THU)
    autoposts.post_noon(FRI)
    assert autoposts.post_noon(SAT) is True
    assert autoposts.post_noon(SUN) is True
    assert calls == [("stats", 7), ("trade",), ("bank",),
                     ("stats", 30), ("bank",)]


def test_plan_mentions_only_live_instruments_and_varies(monkeypatch):
    monkeypatch.setattr(autoposts.social_mix, "latest_positive", lambda: None)
    head1, body1 = autoposts.build_plan(MON)
    head2, body2 = autoposts.build_plan(TUE)
    text = "\n".join(body1)
    assert "золот" in text.lower() and "евро" in text.lower()
    for bad in ("US500", "EUSTX50", "UK100", "DE40", "JPN225"):
        assert bad not in text
    assert body1 != body2, "план не должен быть одинаковым каждый день"
    assert autoposts.build_plan(MON) == (head1, body1), "выбор по дате детерминирован"


def test_promo_text_uses_real_link_and_no_placeholder(monkeypatch):
    monkeypatch.setattr(autoposts.cfg, "PREMIUM_URL", "https://t.me/TestPremium")
    monkeypatch.setattr(autoposts.cfg, "DISCLAIMER", "Не рекомендация.")
    text = autoposts._promo_text("indicator")
    assert "https://t.me/TestPremium" in text
    assert "@your_username" not in text
    assert "Не рекомендация." in text


def test_promo_variant_depends_on_weekday(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(autoposts, "_send_public",
                        lambda text, markup=None, photo=None: (seen.append(text), True)[1])
    autoposts.post_promo(MON)
    autoposts.post_promo(THU)
    assert "Публичный журнал" in seen[0]
    assert "Что даёт Premium" in seen[1]


def test_daily_plan_has_two_promos_and_weekday_noon(monkeypatch):
    monkeypatch.setattr(autoposts.cfg, "PROMO_ENABLED", True)
    monkeypatch.setattr(autoposts.cfg, "TG_CONTENT_ENABLED", True)
    plan = {name: (when, dow) for name, when, dow, _ in autoposts.daily_plan()}
    assert plan["promo_mon"][1] == "mon"
    assert plan["promo_thu"][1] == "thu"
    assert plan["noon"][1] == "mon-fri"
    assert "promo" not in plan and "tgcontent" not in plan


def test_last_trade_record_picks_latest(monkeypatch):
    records = [
        {"trade_id": "a", "kind": "sl", "ts": dt.datetime.now().timestamp() - 3600},
        {"trade_id": "b", "kind": "tp", "ts": dt.datetime.now().timestamp() - 60},
    ]
    monkeypatch.setattr(risk_guard, "read_closed_trades", lambda path=None: records)
    tr = autoposts._last_trade_record(7)
    assert tr and tr["trade_id"] == "b"


def test_post_last_trade_formats_r_and_note(monkeypatch):
    tr = {"symbol": "XAUUSD", "side": "LONG", "kind": "tp", "total_r": 1.0,
          "ts": dt.datetime.now().timestamp(), "entry": 4000.0, "exit": 4020.0,
          "note": "Разбор: вход по чек-листу."}
    sent: list[str] = []
    monkeypatch.setattr(autoposts, "_last_trade_record", lambda days=7: tr)
    monkeypatch.setattr(autoposts, "_send_public",
                        lambda text, markup=None, photo=None: (sent.append(text), True)[1])
    autoposts._post_last_trade(MON)
    assert "Разбор сделки" in sent[0] and "+1.00R" in sent[0]
    assert "Разбор: вход по чек-листу." in sent[0]


def test_post_stats_falls_back_to_bank_without_trades(monkeypatch):
    monkeypatch.setattr(autoposts.track_record, "load_trades", lambda days: [])
    monkeypatch.setattr(autoposts, "post_tg_content", lambda: True)
    assert autoposts._post_stats(7, "title") is True


def test_bank_low_warns_once_and_resets_after_refill(tmp_path, monkeypatch):
    monkeypatch.setattr(autoposts, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(autoposts, "owner_chat", lambda: "123")
    sent: list[str] = []
    monkeypatch.setattr(autoposts.tg, "send_plain",
                        lambda text, **kw: (sent.append(text), True)[1])
    monkeypatch.setattr(autoposts.telegram_posts, "remaining", lambda: 5)
    autoposts._maybe_warn_bank()
    autoposts._maybe_warn_bank()
    assert len(sent) == 1 and "осталось 5" in sent[0]
    # после пополнения флаг сбрасывается и следующее падение снова алертит
    monkeypatch.setattr(autoposts.telegram_posts, "remaining", lambda: 10)
    autoposts._maybe_warn_bank()
    monkeypatch.setattr(autoposts.telegram_posts, "remaining", lambda: 4)
    autoposts._maybe_warn_bank()
    assert len(sent) == 2
