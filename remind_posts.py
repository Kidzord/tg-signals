"""Упрощённые напоминания о постах для соцсетей (без matplotlib).

Отправляет напоминания владельцу о постах для Threads/X/VK/Дзен.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg
import tg

OWNER_PATH = Path(__file__).parent / "owner.json"
THREADS_USED = Path(__file__).parent / "threads_used.json"


def get_owner_chat_id() -> str:
    """Получить chat_id владельца."""
    if cfg.OWNER_CHAT_ID:
        return cfg.OWNER_CHAT_ID

    try:
        with open(OWNER_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return data.get("chat_id", "")
    except (FileNotFoundError, json.JSONDecodeError):
        return ""


def get_next_post_num() -> int:
    """Получить номер следующего поста."""
    used = set()
    if THREADS_USED.exists():
        try:
            used = set(json.loads(THREADS_USED.read_text(encoding="utf-8")))
        except Exception:
            pass

    for i in range(1, 29):
        if i not in used:
            return i
    return 1


def send_threads_reminder():
    """Отправить напоминание о посте для Threads."""
    chat_id = get_owner_chat_id()
    if not chat_id:
        print("OWNER_CHAT_ID не задан")
        return

    post_num = get_next_post_num()
    img_path = Path(__file__).parent / "threads_img" / f"{post_num:02d}.png"

    if not img_path.exists():
        print(f"Картинка не найдена: {img_path}")
        return

    text = f"""
📱 Памятка: пост для Threads

Пост #{post_num}
Картинка: {img_path}

Открой https://www.threads.net/ и опубликуй!
    """.strip()

    tg.send_message(text, token=cfg.TG_BOT_TOKEN, chat_id=chat_id)
    print(f"Напоминание отправлено в {chat_id}")


def send_x_reminder():
    """Отправить напоминание о посте для X."""
    chat_id = get_owner_chat_id()
    if not chat_id:
        print("OWNER_CHAT_ID не задан")
        return

    post_num = get_next_post_num()

    text = f"""
🐦 Памятка: пост для X (Twitter)

Пост #{post_num}

Запусти: python x_post.py {post_num}
    """.strip()

    tg.send_message(text, token=cfg.TG_BOT_TOKEN, chat_id=chat_id)
    print(f"Напоминание отправлено в {chat_id}")


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "threads":
        send_threads_reminder()
    elif cmd == "x":
        send_x_reminder()
    elif cmd == "help":
        print("Команды:")
        print("  python remind_posts.py threads  - напоминание про Threads")
        print("  python remind_posts.py x  - напоминание про X")
    else:
        print("Неизвестная команда. Используй: python remind_posts.py help")
