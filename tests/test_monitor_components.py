"""Тесты монитора: отсутствие компонентов дольше порога."""
from __future__ import annotations

import sys
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
