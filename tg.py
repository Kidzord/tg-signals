"""Минимальная обёртка над Telegram Bot API.

Функции без явных token/chat_id работают с сигнальным ботом (cfg.BOT_TOKEN,
cfg.CHAT_ID). Premium-бот передаёт свой токен и chat_id явно.
"""
from __future__ import annotations

import json
import logging

import requests

import config as cfg

log = logging.getLogger("tg")

CAPTION_LIMIT = 1024
# У Telegram подписка бывает только на 30 дней (константа Bot API)
SUBSCRIPTION_PERIOD = 2592000


def _api(method: str, data: dict, files: dict | None = None,
         timeout: int = 30, token: str | None = None) -> tuple[bool, str]:
    tok = token or cfg.BOT_TOKEN
    if not tok:
        log.error("не задан токен бота")
        return False, "no token"
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/{method}",
                          data=data, files=files, timeout=timeout)
    except requests.RequestException as e:
        log.error("сеть: %s", e)
        return False, str(e)
    if r.status_code != 200:
        log.error("telegram %s: %s %s", method, r.status_code, r.text[:300])
        return False, r.text[:300]
    return True, "ok"


def send_photo(path: str, caption: str, token: str | None = None,
               chat_id: str | int | None = None,
               markup: dict | None = None) -> tuple[bool, str]:
    chat = cfg.CHAT_ID if chat_id is None else str(chat_id)
    if not chat:
        log.error("не задан chat_id")
        return False, "no chat_id"
    text = caption if len(caption) <= CAPTION_LIMIT else caption[: CAPTION_LIMIT - 1] + "…"
    data = {"chat_id": chat, "caption": text, "parse_mode": "HTML"}
    if markup:
        data["reply_markup"] = json.dumps(markup, ensure_ascii=False)
    with open(path, "rb") as f:
        return _api("sendPhoto", data, files={"photo": f}, token=token)


def send_message(text: str, token: str | None = None,
                 chat_id: str | int | None = None,
                 markup: dict | None = None) -> tuple[bool, str]:
    chat = cfg.CHAT_ID if chat_id is None else str(chat_id)
    if not chat:
        log.error("не задан chat_id")
        return False, "no chat_id"
    data = {"chat_id": chat, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": "true"}
    if markup:
        data["reply_markup"] = json.dumps(markup, ensure_ascii=False)
    return _api("sendMessage", data, token=token)


def _api_json(method: str, data: dict, token: str | None = None,
              timeout: int = 30) -> tuple[bool, object]:
    """Вызов метода с возвратом поля result. (успех, result либо текст ошибки)."""
    tok = token or cfg.BOT_TOKEN
    if not tok:
        return False, "нет токена"
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/{method}",
                          data=data, timeout=timeout)
    except requests.RequestException as e:
        return False, f"сеть: {e}"
    try:
        res = r.json()
    except ValueError:
        return False, f"не JSON: {r.text[:200]}"
    if not res.get("ok"):
        return False, str(res.get("description", "ошибка"))
    return True, res.get("result")


def create_invoice_link(title: str, description: str, payload: str, stars: int,
                        subscription: bool = True,
                        token: str | None = None) -> tuple[bool, str]:
    """Счёт в Stars. Возвращает (успех, ссылка на оплату либо причина ошибки).

    subscription=True — автопродление раз в 30 дней (другой период Telegram не даёт).
    """
    data = {
        "title": title,
        "description": description,
        "payload": payload,
        "currency": "XTR",
        "prices": json.dumps([{"label": title, "amount": int(stars)}]),
    }
    if subscription:
        data["subscription_period"] = str(SUBSCRIPTION_PERIOD)
    return _api_json("createInvoiceLink", data, token=token)


def answer_pre_checkout(query_id: str, ok: bool = True, error: str = "",
                        token: str | None = None) -> tuple[bool, str]:
    """Ответ на pre_checkout_query. Telegram ждёт его не дольше 10 секунд."""
    data = {"pre_checkout_query_id": str(query_id), "ok": "true" if ok else "false"}
    if not ok and error:
        data["error_message"] = error
    return _api("answerPreCheckoutQuery", data, token=token)


def answer_callback(cb_id: str, text: str = "", alert: bool = False,
                    token: str | None = None) -> tuple[bool, str]:
    """Убрать «часики» на inline-кнопке. text — всплывающая подсказка."""
    data = {"callback_query_id": str(cb_id)}
    if text:
        data["text"] = text
        if alert:
            data["show_alert"] = "true"
    return _api("answerCallbackQuery", data, token=token)


def edit_text(chat_id: str | int, message_id: int | str, text: str,
              markup: dict | None = None, token: str | None = None) -> tuple[bool, str]:
    """Переписать текст сообщения (навигация по меню без спама новыми сообщениями)."""
    data = {"chat_id": str(chat_id), "message_id": str(message_id),
            "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if markup is not None:
        data["reply_markup"] = json.dumps(markup, ensure_ascii=False)
    return _api("editMessageText", data, token=token)


def set_commands(commands: list[tuple[str, str]], token: str | None = None) -> tuple[bool, str]:
    """Оформить список команд в кнопке «Меню» у бота."""
    data = {"commands": json.dumps([{"command": c, "description": d} for c, d in commands],
                                   ensure_ascii=False)}
    return _api("setMyCommands", data, token=token)


def get_updates(offset: int | None = None, timeout: int = 30,
                token: str | None = None) -> list[dict]:
    """Long polling: разовая порция апдейтов. Пустой список — таймаут или ошибка."""
    tok = token or cfg.BOT_TOKEN
    if not tok:
        log.error("не задан токен бота")
        return []
    data: dict = {"timeout": timeout}
    if offset is not None:
        data["offset"] = offset
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/getUpdates",
                          data=data, timeout=timeout + 15)
    except requests.RequestException as e:
        log.error("сеть getUpdates: %s", e)
        return []
    if r.status_code != 200:
        log.error("getUpdates: %s %s", r.status_code, r.text[:200])
        return []
    try:
        return r.json().get("result", [])
    except ValueError:
        return []
