"""Тесты restore: проверка снимка, песочница и защита живых данных."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backup  # noqa: E402
from tools import restore  # noqa: E402

TABLES = ("deliveries", "payments", "group_outbox", "nurture", "meta")


def make_snapshot(base: Path, name: str = "20261103-030000") -> Path:
    snap = base / "backups" / name
    snap.mkdir(parents=True)
    con = sqlite3.connect(snap / "state.db")
    try:
        for table in TABLES:
            con.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        con.execute("INSERT INTO payments (id) VALUES (1)")
        con.commit()
    finally:
        con.close()
    (snap / "users.json").write_text(json.dumps({"1": {"deposit": 5000}}),
                                     encoding="utf-8")
    (snap / "trades.jsonl").write_text(
        json.dumps({"ts": 1, "r": -1.0}) + "\n" +
        json.dumps({"ts": 2, "r": 3.0}) + "\n", encoding="utf-8")
    (snap / ".ok").write_text(name, encoding="utf-8")
    return snap


@pytest.fixture()
def backups(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "BACKUP_DIR", str(tmp_path / "backups"))
    return make_snapshot(tmp_path)


def test_inspect_valid_snapshot(backups):
    res = restore.inspect(backups)
    assert res["ok"] is True
    assert res["users"] == 1
    assert res["trades.jsonl_lines"] == 2
    assert res["payments"] == 1
    assert set(res["tables"]) >= set(TABLES)
    assert res["warnings"] == []


def test_inspect_detects_broken_json(backups):
    (backups / "users.json").write_text("{not json", encoding="utf-8")
    res = restore.inspect(backups)
    assert res["ok"] is False
    assert any("users.json" in e for e in res["errors"])


def test_inspect_detects_missing_table(backups):
    con = sqlite3.connect(backups / "state.db")
    con.execute("DROP TABLE nurture")
    con.commit()
    con.close()
    res = restore.inspect(backups)
    assert res["ok"] is False
    assert any("nurture" in e for e in res["errors"])


def test_drill_restores_latest_into_sandbox(backups):
    res = restore.drill()
    assert res["ok"] is True
    assert res["snapshot"] == backups.name
    assert res["users"] == 1


def test_restore_copies_and_verifies(backups, tmp_path):
    dest = tmp_path / "restored"
    copied = restore.restore(backups, dest)
    assert "state.db" in copied
    assert (dest / "trades.jsonl").exists()
    assert restore.inspect(dest)["ok"] is True


def test_restore_refuses_non_empty_dest_without_force(backups, tmp_path):
    dest = tmp_path / "restored"
    dest.mkdir()
    (dest / "keep.txt").write_text("live", encoding="utf-8")
    with pytest.raises(RuntimeError):
        restore.restore(backups, dest)


def test_restore_refuses_project_root_without_force(backups):
    with pytest.raises(RuntimeError):
        restore.restore(backups, restore.ROOT)
