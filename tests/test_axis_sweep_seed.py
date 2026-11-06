"""axis_sweep берёт фиксированные параметры кандидата, а не live-базу."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.axis_sweep import _grid_seed  # noqa: E402


def test_fixed_candidate_seed_is_used():
    assert _grid_seed("range_short").get("side") == "short"
    assert _grid_seed("range_live").get("tp_r") == 3.0
    assert _grid_seed("ict_gold3").get("tp_r") == 3.0


def test_varying_grid_only_keeps_common_fields():
    seed = _grid_seed("range")
    assert seed.get("side") == "both"
    assert seed.get("stop_atr") == 1.0
    assert "tp_r" not in seed and "er_max" not in seed
