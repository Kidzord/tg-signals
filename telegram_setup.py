"""Оформление Telegram-группы: закреп, описание, команды premium-бота.

Запуск:
    python telegram_setup.py pin           # отправить и закрепить пост-визитку
    python telegram_setup.py description   # описание группы
    python telegram_setup.py commands      # команды premium-бота
    python telegram_setup.py all           # всё сразу

Скрипт идемпотентен: если закреплённый пост уже совпадает с текстом,
повторно он не отправляется. Старый закреп снимается только если это
предыдущая версия нашей визитки.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg
import tg

MARKER = "KidzordFX — журнал сделок"

PIN_TEXT = (
    "📊 <b>KidzordFX — журнал сделок</b>\n\n"
    "Здесь публикуем сигналы по золоту, форексу и индексам: вход, стоп, "
    "три цели и результат в R.\n\n"
    "• Бесплатно — с задержкой 30 минут\n"
    "• В боте — сразу, плюс расчёт объёма под твой депозит и риск\n"
    "• Публикуем и прибыльные, и убыточные сделки\n\n"
    "📈 Статистика и журнал — <a href=\"{premium}\">@{handle}</a> → /stats\n"
    "⚡ Premium и пробный доступ на 3 дня — /buy в боте\n\n"
    "<i>Не является инвестиционной рекомендацией.</i>"
)

DESCRIPTION = (
    "Публичный журнал сделок по золоту, форексу и индексам. "
    "Бесплатные сигналы с задержкой 30 минут. Premium — сразу и под твой риск. "
    "Не является инвестиционной рекомендацией."
)


def _pin_text() -> str:
    url = cfg.PREMIUM_URL or f"https://t.me/{cfg.SUB_BOT_USERNAME}"
    handle = cfg.SUB_BOT_USERNAME or "KidzordPremim_bot"
    return PIN_TEXT.format(premium=url, handle=handle)


def _pin_markup() -> dict:
    url = cfg.PREMIUM_URL or f"https://t.me/{cfg.SUB_BOT_USERNAME}"
    stats = f"https://t.me/{cfg.SUB_BOT_USERNAME}?start=src_group" if cfg.SUB_BOT_USERNAME else url
    return {"inline_keyboard": [[
        {"text": "📈 Статистика", "url": stats},
        {"text": "⚡ Premium", "url": url},
    ]]}


def _plain(text: str) -> str:
    """Telegram отдаёт закреп без HTML-тегов — сравниваем в одном виде."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _pinned_text(chat: dict) -> str:
    pm = chat.get("pinned_message") or {}
    return str(pm.get("text") or pm.get("caption") or "")


def pin() -> bool:
    if not cfg.BOT_TOKEN or not cfg.CHAT_ID:
        print("нет TG_BOT_TOKEN / TG_CHAT_ID в .env")
        return False
    ok, chat = tg.get_chat(cfg.CHAT_ID, token=cfg.BOT_TOKEN)
    if not ok or not isinstance(chat, dict):
        print("getChat:", chat)
        return False
    current = _pinned_text(chat)
    wanted = _pin_text()
    if _plain(current) == _plain(wanted):
        print("закреп уже актуален — ничего не меняю")
        return True

    res = tg.send_message_result(wanted, token=cfg.BOT_TOKEN, chat_id=cfg.CHAT_ID,
                                 markup=_pin_markup())
    if not res.ok or not isinstance(res.result, dict):
        print("sendMessage:", res.error)
        return False
    mid = res.result.get("message_id")
    ok, info = tg.pin_chat_message(cfg.CHAT_ID, mid, token=cfg.BOT_TOKEN)
    print("пост отправлен:", mid, "закреплён:", ok, info)
    if not ok:
        return False

    # Снимаем предыдущую версию нашей визитки, чтобы не висели две.
    pm = chat.get("pinned_message") or {}
    old_id = pm.get("message_id")
    if old_id and MARKER in _pinned_text(chat):
        ok2, info2 = tg.unpin_chat_message(cfg.CHAT_ID, old_id, token=cfg.BOT_TOKEN)
        print("старый закреп снят:", ok2, info2)
    return True


def description() -> bool:
    if not cfg.BOT_TOKEN or not cfg.CHAT_ID:
        print("нет TG_BOT_TOKEN / TG_CHAT_ID в .env")
        return False
    ok, info = tg.set_chat_description(DESCRIPTION, cfg.CHAT_ID, token=cfg.BOT_TOKEN)
    print("описание:", ok, info)
    return ok


def commands() -> bool:
    if not cfg.SUB_BOT_TOKEN:
        print("нет SUB_BOT_TOKEN в .env")
        return False
    ok, info = tg.set_commands([
        ("start", "Меню"),
        ("buy", "Подписка и пробный доступ"),
        ("status", "Статус подписки"),
        ("promo", "Активировать промокод"),
        ("stats", "Статистика сигналов"),
        ("mycard", "Карточка счёта"),
        ("symbols", "Фильтр инструментов"),
        ("maxday", "Лимит сигналов в сутки"),
        ("help", "Помощь"),
    ], token=cfg.SUB_BOT_TOKEN)
    print("команды premium-бота:", ok, info)
    return ok


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "pin":
        pin()
    elif cmd == "description":
        description()
    elif cmd == "commands":
        commands()
    elif cmd == "all":
        pin()
        description()
        commands()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
