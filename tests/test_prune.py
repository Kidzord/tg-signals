"""Ретеншн: удаляются только старые обычные файлы в разрешённых папках."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import prune  # noqa: E402

NOW = 1_800_000_000.0


def make(path: Path, age_days: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 100)
    ts = NOW - age_days * 86400
    os.utime(path, (ts, ts))
    return path


def test_dry_run_reports_without_deleting(tmp_path):
    old = make(tmp_path / "renders" / "old.png", 40)
    report = prune.prune(30, apply=False, dirs={"renders": str(tmp_path / "renders")},
                         now_ts=NOW)
    assert report["renders"]["files"] == 1
    assert report["renders"]["deleted"] == 0
    assert old.exists()


def test_apply_deletes_only_old_files(tmp_path):
    old = make(tmp_path / "renders" / "old.png", 40)
    fresh = make(tmp_path / "renders" / "fresh.png", 5)
    report = prune.prune(30, apply=True, dirs={"renders": str(tmp_path / "renders")},
                         now_ts=NOW)
    assert report["renders"]["deleted"] == 1
    assert not old.exists()
    assert fresh.exists()


def test_subdirectories_untouched(tmp_path):
    nested = make(tmp_path / "renders" / "sub" / "old.png", 400)
    prune.prune(30, apply=True, dirs={"renders": str(tmp_path / "renders")},
                now_ts=NOW)
    assert nested.exists()


def test_missing_folder_is_not_error(tmp_path):
    report = prune.prune(30, apply=True,
                         dirs={"renders": str(tmp_path / "nope")}, now_ts=NOW)
    assert report["renders"]["missing"] is True
    assert report["renders"]["deleted"] == 0


def test_multiple_targets(tmp_path):
    make(tmp_path / "a" / "1.png", 90)
    make(tmp_path / "b" / "2.png", 90)
    make(tmp_path / "b" / "3.png", 1)
    report = prune.prune(30, apply=True,
                         dirs={"a": str(tmp_path / "a"), "b": str(tmp_path / "b")},
                         now_ts=NOW)
    assert report["a"]["deleted"] == 1
    assert report["b"]["deleted"] == 1
    assert report["b"]["files"] == 1


def test_text_report_mentions_totals(tmp_path):
    make(tmp_path / "renders" / "old.png", 40)
    report = prune.prune(30, dirs={"renders": str(tmp_path / "renders")}, now_ts=NOW)
    text = prune.text_report(report, 30, False)
    assert "renders: 1 файлов" in text
    assert "--apply" in text
