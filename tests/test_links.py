"""Bio-ссылки: у каждой площадки свой src_* тег, бот принимает любой."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import links  # noqa: E402


def test_each_platform_has_unique_tag():
    urls = links.all_links("KidzordPremim_bot")
    assert urls["threads"] == "https://t.me/KidzordPremim_bot?start=src_threads"
    assert urls["tiktok"].endswith("src_tiktok")
    assert urls["x"].endswith("src_x")
    assert len(set(urls.values())) == len(links.PLATFORMS)


def test_username_with_at_is_cleaned():
    assert links.bot_link("threads", "@Bot") == "https://t.me/Bot?start=src_threads"


def test_missing_username_uses_placeholder(monkeypatch):
    monkeypatch.setattr(links.cfg, "SUB_BOT_USERNAME", "")
    assert "SUB_BOT_USERNAME" in links.bot_link("threads")


def test_tags_match_what_bot_accepts():
    # subbot принимает любой payload, начинающийся с src_; теги должны быть такими
    for tag in links.PLATFORMS:
        assert tag.isascii() and " " not in tag
        assert f"start=src_{tag}" in links.bot_link(tag, "bot")


def test_markdown_table_lists_all_platforms():
    table = links.markdown_table("bot")
    assert table.startswith("| Площадка | Ссылка |")
    assert table.count("src_") == len(links.PLATFORMS)
