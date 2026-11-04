"""Живой цикл: один бар — одна обработка, выход по времени, дедуп журналов."""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import autoposts  # noqa: E402
import bot  # noqa: E402
import config as cfg  # noqa: E402
import heartbeat  # noqa: E402
import strategy as st  # noqa: E402
import subbot  # noqa: E402
import track_record  # noqa: E402


def test_mt5_init_pins_terminal_path(monkeypatch):
    import types

    calls: dict = {}
    fake = types.SimpleNamespace(initialize=lambda **kw: calls.update(kw) or True)
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    monkeypatch.setattr(cfg, "MT5_TERMINAL_PATH", r"C:\MT5\terminal64.exe")
    assert cfg.mt5_init(timeout=5) is True
    assert calls["path"] == r"C:\MT5\terminal64.exe"
    assert calls["timeout"] == 5


def test_mt5_init_without_explicit_path(monkeypatch):
    import types

    calls: dict = {}
    fake = types.SimpleNamespace(initialize=lambda **kw: calls.update(kw) or True)
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    monkeypatch.setattr(cfg, "MT5_TERMINAL_PATH", "")
    assert cfg.mt5_init() is True
    assert "path" not in calls


def _frame(t, h, l, c) -> st.Frame:
    n = len(t)
    return st.Frame(
        t=np.array(t, dtype="int64"),
        o=np.array(c, dtype=float),
        h=np.array(h, dtype=float),
        l=np.array(l, dtype=float),
        c=np.array(c, dtype=float),
        v=np.ones(n),
    )


def _trade(**kw) -> dict:
    tr = {
        "dir": 1, "entry": 100.0, "stop": 95.0, "risk": 5.0,
        "tp1": 105.0, "tp2": 110.0, "tp3": 115.0,
        "bar_idx": 1, "bar_ts": 900.0, "last_mgd_ts": 900.0,
        "hit1": False, "hit2": False, "hit3": False, "be": False,
        "done": 0.0, "r_partial": 0.0, "trade_id": "T1", "single": False,
    }
    tr.update(kw)
    return tr


def test_manage_trade_processes_bar_once(monkeypatch):
    """Повторный опрос того же бара не должен давать ложный выход в БУ."""
    monkeypatch.setattr(bot, "post_result", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "risk_note_stop", lambda *a: None)
    # бар: high >= TP1, low касается entry — старый код закрывал BE на втором проходе
    chart = _frame([0, 900, 1800], [100, 100, 106], [99, 99, 100], [100, 100, 101])
    state = {"trade": _trade()}
    bot.manage_trade(state, chart, 2)
    assert state["trade"] is not None
    assert state["trade"]["hit1"] is True
    assert state["trade"]["stop"] == 100.0
    bot.manage_trade(state, chart, 2)   # тот же закрытый бар
    assert state["trade"] is not None, "повторный проход закрыл сделку в безубыток"


def test_zero_partials_only_move_stop_to_breakeven(monkeypatch):
    """p1=p2=0: TP1 не постится как «0R», но стоп переводится в БУ."""
    seen: list[str] = []
    monkeypatch.setattr(bot, "post_result", lambda kind, *a, **kw: seen.append(kind))
    monkeypatch.setattr(bot, "risk_note_stop", lambda *a: None)
    chart = _frame([0, 900, 1800], [100, 100, 106], [99, 99, 99], [100, 100, 101])
    state = {"trade": _trade(p1=0.0, p2=0.0, p3=1.0)}
    bot.manage_trade(state, chart, 2)
    assert state["trade"]["hit1"] is True
    assert state["trade"]["stop"] == 100.0
    assert "tp1" not in seen


def test_manage_trade_time_exit_uses_bar_time(monkeypatch):
    """max_bars считается по времени: индекс в окне постоянен и не растёт."""
    seen: list[str] = []
    monkeypatch.setattr(bot, "post_result", lambda kind, *a, **kw: seen.append(kind))
    monkeypatch.setattr(bot, "risk_note_stop", lambda *a: None)
    monkeypatch.setattr(bot.PARAMS, "max_bars", 1)
    chart = _frame([0, 900, 1800], [101, 101, 101], [99, 99, 99], [100, 100, 100])
    state = {"trade": _trade(bar_ts=0.0)}
    bot.manage_trade(state, chart, 2)
    assert "time" in seen
    assert state["trade"] is None


def test_manage_open_trade_replays_missed_bars(monkeypatch):
    """Стоп, сработавший на пропущенном баре, должен закрыть сделку, а не потеряться."""
    seen: list[str] = []
    monkeypatch.setattr(bot, "post_result", lambda kind, *a, **kw: seen.append(kind))
    monkeypatch.setattr(bot, "risk_note_stop", lambda *a: None)
    chart = _frame([0, 900, 1800, 2700], [101, 101, 101, 101],
                   [99, 99, 94, 99], [100, 100, 100, 100])
    state = {"trade": _trade(bar_ts=0.0, last_mgd_ts=0.0)}
    bot.manage_open_trade(state, chart, 3)
    assert state["trade"] is None
    assert "sl" in seen


def test_spread_guard_blocks_only_wide_spread(monkeypatch):
    monkeypatch.setattr(bot, "SPREAD_MAX_R", 0.25)
    monkeypatch.setattr(bot, "spread_cost_r", lambda s, r: 0.40)
    assert bot.spread_guard_ok("XAUUSD", 1.0) is False
    monkeypatch.setattr(bot, "spread_cost_r", lambda s, r: 0.10)
    assert bot.spread_guard_ok("XAUUSD", 1.0) is True
    monkeypatch.setattr(bot, "spread_cost_r", lambda s, r: None)   # нет MT5
    assert bot.spread_guard_ok("XAUUSD", 1.0) is True
    monkeypatch.setattr(bot, "SPREAD_MAX_R", 0.0)                  # выключено
    monkeypatch.setattr(bot, "spread_cost_r", lambda s, r: 9.0)
    assert bot.spread_guard_ok("XAUUSD", 1.0) is True


def test_heartbeat_sent_state_survives_restart(tmp_path, monkeypatch):
    path = tmp_path / "heartbeat_state.json"
    monkeypatch.setattr(heartbeat, "STATE_PATH", path)
    assert heartbeat.load_sent() == {}
    heartbeat.save_sent({"hb": "2026-11-02"})
    assert heartbeat.load_sent() == {"hb": "2026-11-02"}


class _Cp1251Console:
    """Консоль Windows без эмодзи: запись падает так же, как cp1251."""

    def write(self, _text):
        raise UnicodeEncodeError("charmap", "🫀", 0, 1, "character maps to <undefined>")

    def flush(self):
        pass

    def reconfigure(self, **_kwargs):
        raise ValueError("not configurable")


def test_heartbeat_send_does_not_resend_when_console_cannot_print(monkeypatch):
    # Отчёт уже улетел в Telegram; падение print не должно превращаться в
    # «ошибка отправки» и повтор каждые 5 минут.
    monkeypatch.setattr(heartbeat.sys, "stdout", _Cp1251Console())
    monkeypatch.setattr(heartbeat, "build_report", lambda: "🫀 Heartbeat")
    monkeypatch.setattr(heartbeat.cfg, "OWNER_CHAT_ID", "1")
    monkeypatch.setattr(heartbeat.tg, "send_message", lambda *a, **k: (True, None))
    assert heartbeat.send_report() is True


def _write_jsonl(path, rows) -> str:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")
    return str(path)


def test_autoposts_dedups_trade_journal(tmp_path, monkeypatch):
    rows = [
        {"ts": 1, "r": -1.0, "symbol": "A", "trade_id": "T1", "kind": "sl"},
        {"ts": 1, "r": -1.0, "symbol": "A", "trade_id": "T1", "kind": "sl"},
        {"ts": 2, "r": 3.0, "symbol": "A", "trade_id": "T2", "kind": "tp"},
    ]
    monkeypatch.setattr(autoposts, "TRADES_PATH", _write_jsonl(tmp_path / "t.jsonl", rows))
    assert len(autoposts.load_trades()) == 2


def test_track_record_dedups_trade_journal(tmp_path, monkeypatch):
    rows = [
        {"ts": time.time(), "r": -1.0, "symbol": "A", "trade_id": "T1", "kind": "sl"},
        {"ts": time.time(), "r": -1.0, "symbol": "A", "trade_id": "T1", "kind": "sl"},
    ]
    monkeypatch.setattr(track_record, "TRADES_PATH",
                        _write_jsonl(tmp_path / "t.jsonl", rows))
    assert len(track_record.load_trades()) == 1


def test_post_tg_content_uses_telegram_bank(monkeypatch):
    # Регресс: telegram_posts использовался без импорта, слот 12:00 падал NameError.
    import telegram_posts
    monkeypatch.setattr(autoposts, "owner_chat", lambda: "1")
    monkeypatch.setattr(telegram_posts, "next_unused", lambda: None)
    monkeypatch.setattr(autoposts.telegram_editor, "publish_next",
                        lambda *_a, **_k: True)
    assert autoposts.post_tg_content() is True


def test_subbot_import_does_not_require_python_telegram_bot():
    """Мёртвые ui-модули отвязаны: CI не должен ставить python-telegram-bot."""
    assert not hasattr(subbot, "ui_components")
    assert not hasattr(subbot, "subbot_ui")
