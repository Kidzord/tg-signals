"""Тесты монитора: отсутствие компонентов дольше порога."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config as cfg  # noqa: E402
import monitor  # noqa: E402

ALL = [
    r"C:\Python\python.exe subbot.py",
    r"C:\Python\python.exe autoposts.py",
    r"C:\Python\python.exe heartbeat.py",
    r"C:\Python\python.exe monitor.py",
    r"C:\Windows\powershell.exe -File C:\bot\watchdog.ps1",
]


def without(pattern: str) -> list[str]:
    return [line for line in ALL if pattern not in line]


def test_ancient_heartbeat_file_is_ignored(tmp_path, monkeypatch):
    hb = tmp_path / "hb_OLD_range.json"
    hb.write_text('{"pid": 1, "ok": true}', encoding="utf-8")
    old = time.time() - 8 * 86400
    os.utime(hb, (old, old))
    monkeypatch.setattr(monitor, "HERE", str(tmp_path))
    monkeypatch.setattr(cfg, "EXPECTED_BOTS", 4)
    lines = [r"C:\Python\python.exe bot.py"] * 4
    ok, text = monitor.check_heartbeats(lines)
    assert ok, text


def test_all_components_present():
    state: dict = {}
    ok, text = monitor.check_components(state, ALL, now=1000.0)
    assert ok is True
    assert "premium-бот" in text
    assert state["missing_since"] == {}


def test_missing_component_alerts_only_after_threshold(monkeypatch):
    monkeypatch.setattr(cfg, "COMPONENT_ALERT_MIN", 10)
    state: dict = {}
    lines = without("autoposts.py")

    ok, _ = monitor.check_components(state, lines, now=1000.0)
    assert ok is True                      # только что пропал — ждём
    assert "автопосты" in state["missing_since"]

    ok, text = monitor.check_components(state, lines, now=1000.0 + 9 * 60)
    assert ok is True

    ok, text = monitor.check_components(state, lines, now=1000.0 + 11 * 60)
    assert ok is False
    assert "автопосты" in text
    assert "11 мин" in text


def test_component_return_clears_missing_state(monkeypatch):
    monkeypatch.setattr(cfg, "COMPONENT_ALERT_MIN", 10)
    state: dict = {}
    monitor.check_components(state, without("subbot.py"), now=1000.0)
    assert "premium-бот" in state["missing_since"]
    ok, _ = monitor.check_components(state, ALL, now=1100.0)
    assert ok is True
    assert "premium-бот" not in state["missing_since"]


def test_process_list_unavailable_is_a_problem(monkeypatch):
    monkeypatch.setattr(monitor, "_process_cmdlines", lambda: None)
    ok, text = monitor.check_components({}, None, now=1000.0)
    assert ok is False
    assert "список процессов" in text


def test_unknown_cli_argument_does_not_start_monitor(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["monitor.py", "check"])
    monitor.main()
    assert "использование" in capsys.readouterr().out
