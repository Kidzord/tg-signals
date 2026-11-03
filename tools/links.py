"""Ссылки для bio: у каждой площадки свой src_*, чтобы видеть, откуда пришли.

Бот принимает любой `?start=src_<tag>` (см. subbot.py) и сохраняет первый
источник в users.json. При оплате источник уходит в payments, а `/revenue`
показывает разбивку «источник: люди / оплаты / деньги». Ничего не публикует —
это генератор ссылок, которые владелец вставляет в профиль.

    python tools/links.py                 # все площадки
    python tools/links.py threads         # одна
    python tools/links.py --markdown      # таблицей для документа

Источник определяется первым касанием: если человек уже заходил без тега,
позднейший src_* его не перезапишет.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg

# tag → как называется площадка в отчёте
PLATFORMS = {
    "threads": "Threads",
    "tiktok": "TikTok",
    "x": "X (Twitter)",
    "instagram": "Instagram",
    "youtube": "YouTube",
    "discord": "Discord",
    "landing": "Лендинг",
    "method": "Методички на лендинге",
    "group": "Бесплатная группа",
}


def bot_link(tag: str, username: str | None = None) -> str:
    """https://t.me/<bot>?start=src_<tag>."""
    name = (username if username is not None else cfg.SUB_BOT_USERNAME).strip().lstrip("@")
    if not name:
        name = "SUB_BOT_USERNAME"
    return f"https://t.me/{name}?start=src_{tag}"


def all_links(username: str | None = None) -> dict[str, str]:
    return {tag: bot_link(tag, username) for tag in PLATFORMS}


def text_report(username: str | None = None) -> str:
    name = (username if username is not None else cfg.SUB_BOT_USERNAME).strip().lstrip("@")
    lines = ["Ссылки для bio (источник = первый src_*, его сохраняет бот):"]
    if not name:
        lines.append("⚠️ SUB_BOT_USERNAME не задан в .env — в ссылке заглушка.")
    for tag, title in PLATFORMS.items():
        lines.append(f"  {title:<22} {bot_link(tag, username)}")
    lines.append("")
    lines.append("Проверить, откуда пришли: /revenue в premium-боте.")
    return "\n".join(lines)


def markdown_table(username: str | None = None) -> str:
    rows = ["| Площадка | Ссылка |", "|---|---|"]
    for tag, title in PLATFORMS.items():
        rows.append(f"| {title} | {bot_link(tag, username)} |")
    return "\n".join(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Ссылки с атрибуцией для bio")
    ap.add_argument("platform", nargs="?", choices=sorted(PLATFORMS))
    ap.add_argument("--markdown", action="store_true", help="таблица для документа")
    ap.add_argument("--bot", default=None, help="username бота (по умолчанию из .env)")
    args = ap.parse_args()

    if args.platform:
        print(bot_link(args.platform, args.bot))
    elif args.markdown:
        print(markdown_table(args.bot))
    else:
        print(text_report(args.bot))
    return 0


if __name__ == "__main__":
    sys.exit(main())
