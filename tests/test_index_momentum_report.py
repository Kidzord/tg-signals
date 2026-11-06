"""Индексная месячная стратегия: метрики считаются без nan и с компаундингом."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.index_momentum_report import stats  # noqa: E402


def test_stats_compounds_and_counts_months():
    rows = [
        {"month": "2026-01", "net": 0.10},
        {"month": "2026-02", "net": -0.05},
        {"month": "2026-03", "net": 0.02},
    ]
    s = stats(rows, "test")
    assert s["months"] == 3
    assert s["positive_months"] == 2
    assert s["negative_months"] == 1
    expected = (1.10 * 0.95 * 1.02 - 1.0) * 100.0
    assert abs(s["total_return_pct"] - expected) < 0.1
    assert s["max_dd_pct"] > 0
    assert s["sharpe"] != 0
