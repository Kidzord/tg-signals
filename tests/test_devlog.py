"""Дневник разработки: счётчик дней и расход банка заметок."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import devlog  # noqa: E402


@pytest.fixture()
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(devlog, "STATE_PATH", str(tmp_path / "devlog_state.json"))
    yield tmp_path / "devlog_state.json"


def test_day_number_counts_from_fixed_start(state, monkeypatch):
    monkeypatch.setenv("DEVLOG_START", "2026-11-01")
    assert devlog.day_number(dt.date(2026, 11, 1)) == 1
    assert devlog.day_number(dt.date(2026, 11, 2)) == 2
    assert devlog.day_number(dt.date(2026, 12, 1)) == 31


def test_start_date_persists_without_env(state, monkeypatch):
    monkeypatch.delenv("DEVLOG_START", raising=False)
    first = devlog.start_date()
    assert devlog.start_date() == first, "повторный вызов не должен сдвигать старт"
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["start"] == first.isoformat()


def test_devnotes_used_once_in_order(state):
    a, b = devlog.next_devnote(), devlog.next_devnote()
    assert a == devlog.DEVNOTES[0]
    assert b == devlog.DEVNOTES[1]


def test_devnotes_exhausted_returns_none(state):
    for _ in devlog.DEVNOTES:
        assert devlog.next_devnote() is not None
    assert devlog.next_devnote() is None
    assert devlog.next_devnotes(2) == []


def test_next_devnotes_takes_k(state):
    notes = devlog.next_devnotes(2)
    assert notes == list(devlog.DEVNOTES[:2])


def test_note_for_day_does_not_consume_bank(state):
    note = devlog.note_for_day(3, 0)
    assert note in devlog.DEVNOTES
    # следующая «расходуемая» заметка всё ещё первая
    assert devlog.next_devnote() == devlog.DEVNOTES[0]
