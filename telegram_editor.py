"""Публикатор Telegram-постов: превью владельцу → кнопка «Опубликовать».

Банк лежит в telegram_posts.py, картинки — общие с Threads (портретные 4:5).
Владелец получает готовый пост с фото и двумя кнопками. Пока он не нажал
«Опубликовать», в группу ничего не уходит.
"""
from __future__ import annotations

import os

import config as cfg
import telegram_posts
import tg
import threads_editor

HERE = os.path.dirname(os.path.abspath(__file__))


def _image(n: int, post: dict) -> str:
    # 100+n — отдельный диапазон, чтобы не перезаписать карточки Threads 1–28.
    return threads_editor._generic_image(100 + n, post)


def _markup(n: int) -> dict:
    return {"inline_keyboard": [[
        {"text": "📤 Опубликовать в группу", "callback_data": f"tgpost:pub:{n}"},
        {"text": "➡️ Следующий", "callback_data": "tgpost:next"},
    ]]}


def send_next(chat_id: str, token: str | None = None,
              number: int | None = None) -> bool:
    """Показать владельцу следующий пост с кнопками."""
    n = number or telegram_posts.next_unused()
    if n is None:
        tg.send_plain("📝 Банк Telegram-постов закончился. Скажи — добавлю новые.",
                      token=token, chat_id=chat_id)
        return False
    if not 1 <= n <= len(telegram_posts.POSTS):
        tg.send_plain(f"Нет поста #{n}. Всего {len(telegram_posts.POSTS)}.",
                      token=token, chat_id=chat_id)
        return False
    post = telegram_posts.POSTS[n - 1]
    image = _image(n, post)
    res = tg.send_photo_result(image, post["text"], token=token, chat_id=chat_id,
                               markup=_markup(n))
    if not res.ok:
        res = tg.send_message_result(post["text"], token=token, chat_id=chat_id,
                                     markup=_markup(n))
    if res.ok:
        # Помечаем, чтобы «Следующий» не показывал тот же пост.
        telegram_posts.mark_used(n)
    return res.ok


def publish(n: int, owner_chat: str | None = None,
            owner_token: str | None = None) -> bool:
    """Опубликовать конкретный пост в группу сигналов."""
    if not cfg.CHAT_ID or not cfg.BOT_TOKEN:
        tg.send_plain("Нет TG_CHAT_ID или TG_BOT_TOKEN — публиковать некуда.",
                      token=owner_token, chat_id=owner_chat)
        return False
    if not 1 <= n <= len(telegram_posts.POSTS):
        return False
    post = telegram_posts.POSTS[n - 1]
    image = _image(n, post)
    res = tg.send_photo_result(image, post["text"], token=cfg.BOT_TOKEN,
                               chat_id=cfg.CHAT_ID)
    if not res.ok:
        res = tg.send_message_result(post["text"], token=cfg.BOT_TOKEN,
                                     chat_id=cfg.CHAT_ID)
    if res.ok:
        telegram_posts.mark_used(n)
        if owner_chat:
            tg.send_plain(f"✅ Пост #{n} опубликован в группе.",
                          token=owner_token, chat_id=owner_chat)
    elif owner_chat:
        tg.send_plain(f"❗ Не удалось опубликовать #{n}: {res.error[:200]}",
                      token=owner_token, chat_id=owner_chat)
    return res.ok
