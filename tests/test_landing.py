"""Лендинг: сборка, ключевые блоки, отсутствие секретов."""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools import build_landing  # noqa: E402


def test_build_landing(tmp_path):
    path = build_landing.build(tmp_path)
    html = open(path, encoding="utf-8").read()
    assert "KidzordFX" in html
    assert "Публичный журнал" in html
    assert "Пробный доступ" in html
    assert "src_landing" in html
    assert "Не является инвестиционной рекомендацией" in html
    assert "<svg" in html
    assert not re.search(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b", html)
    assert "TG_BOT_TOKEN" not in html
