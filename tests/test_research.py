"""Реестр идей: обязательная атрибуция, WFA-гейт, журнал решений."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config as cfg  # noqa: E402
import research  # noqa: E402


@pytest.fixture()
def reg(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "RESEARCH_PATH", str(tmp_path / "research.json"))
    monkeypatch.setattr(research, "LOG_PATH", str(tmp_path / "docs" / "RESEARCH_LOG.md"))
    monkeypatch.setattr(cfg, "RESEARCH_MIN_TRADES", 20)
    monkeypatch.setattr(cfg, "RESEARCH_MIN_POS_FOLDS", 0.6)
    return tmp_path


def idea(**kw) -> dict:
    base = dict(title="Sweep PDH/PDL", source="@chan", url="https://t.me/chan/1",
                hypothesis="снятие дневного максимума даёт возврат",
                symbols="AUDUSD,USDCAD", tf="15")
    base.update(kw)
    return research.add(**base)


# --- регистрация ----------------------------------------------------------

def test_slug_transliterates_cyrillic():
    assert research.slugify("Свип дневного максимума") == "svip-dnevnogo-maksimuma"


def test_add_requires_source(reg):
    with pytest.raises(ValueError):
        research.add("Идея", "")


def test_add_requires_title(reg):
    with pytest.raises(ValueError):
        research.add("", "@chan")


def test_add_deduplicates_ids(reg):
    a = idea()
    b = idea()
    assert a["id"] != b["id"]


def test_registry_persists(reg):
    created = idea()
    loaded = research._load()
    assert loaded["ideas"][0]["id"] == created["id"]
    assert loaded["ideas"][0]["status"] == "idea"


# --- WFA и ворота ---------------------------------------------------------

def test_record_wfa_marks_tested_and_verdict(reg):
    created = idea()
    updated = research.record_wfa(created["id"], strategy="range", symbol="AUDUSD",
                                  trades=42, net_r=18.5, folds="5/7")
    assert updated["status"] == "tested"
    assert updated["wfa"]["verdict"] == "ПРОХОДИТ"
    assert updated["wfa"]["folds_total"] == 7


def test_record_wfa_fails_below_thresholds(reg):
    created = idea()
    updated = research.record_wfa(created["id"], strategy="range", symbol="AUDUSD",
                                  trades=5, net_r=2.0, folds="1/7")
    assert updated["wfa"]["verdict"] == "НЕ ПРОХОДИТ"


def test_gate_requires_wfa(reg):
    created = idea()
    ok, reasons = research.gate(created["id"])
    assert ok is False
    assert "нет результата WFA" in reasons[0]


def test_gate_rejects_too_few_trades(reg):
    created = idea()
    research.record_wfa(created["id"], strategy="range", symbol="AUDUSD",
                        trades=10, net_r=15.0, folds="5/7", verdict="ПРОХОДИТ")
    ok, reasons = research.gate(created["id"])
    assert ok is False
    assert any("порог не пройден" in r for r in reasons)


def test_promote_passes_on_good_wfa(reg):
    created = idea()
    research.record_wfa(created["id"], strategy="range", symbol="AUDUSD",
                        trades=42, net_r=18.5, folds="5/7")
    ok, reasons = research.promote(created["id"])
    assert ok is True, reasons
    assert research._find(research._load(), created["id"])["status"] == "promoted"


def test_promote_refuses_failed_verdict(reg):
    created = idea()
    research.record_wfa(created["id"], strategy="range", symbol="AUDUSD",
                        trades=42, net_r=-4.0, folds="1/7")
    ok, reasons = research.promote(created["id"])
    assert ok is False
    assert reasons


def test_rejected_idea_cannot_be_promoted(reg):
    created = idea()
    research.record_wfa(created["id"], strategy="range", symbol="AUDUSD",
                        trades=42, net_r=18.5, folds="5/7")
    research.set_status(created["id"], "rejected", "выборка подобрана")
    ok, reasons = research.promote(created["id"])
    assert ok is False
    assert any("отклонена" in r for r in reasons)


def test_parse_folds_clamps_values():
    assert research._parse_folds("5/7") == (7, 5)
    assert research._parse_folds("9/7") == (7, 7)
    assert research._parse_folds("мусор") == (0, 0)


# --- журнал ---------------------------------------------------------------

def test_log_written_with_source_and_verdict(reg):
    created = idea()
    research.record_wfa(created["id"], strategy="range", symbol="AUDUSD",
                        trades=42, net_r=18.5, folds="5/7")
    text = Path(research.LOG_PATH).read_text(encoding="utf-8")
    assert "@chan" in text
    assert "https://t.me/chan/1" in text
    assert "Sweep PDH/PDL" in text
    assert "ПРОХОДИТ" in text
    assert "tested" in text
