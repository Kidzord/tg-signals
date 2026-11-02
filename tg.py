"""Минимальная обёртка над Telegram Bot API.

Функции без явных token/chat_id работают с сигнальным ботом (cfg.BOT_TOKEN,
cfg.CHAT_ID). Premium-бот передаёт свой токен и chat_id явно.

Ключевое отличие от «просто requests.post»: результат вызова — структура
``ApiResult``. По ней вызывающий код понимает, что делать дальше:

* ``retry_after`` — Telegram ответил 429, строку надо повторить позже;
* ``blocked`` — пользователь заблокировал бота или удалил чат;
* ``parse_error`` — сломался HTML в подписи, надо повторить без parse_mode.

Раньше любой не-200 молча превращался в ``False``, и сообщение терялось.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

import config as cfg

log = logging.getLogger("tg")

CAPTION_LIMIT = 1024
# У Telegram подписка бывает только на 30 дней (константа Bot API)
SUBSCRIPTION_PERIOD = 2592000


@dataclass
class ApiResult:
    ok: bool
    result: Any = None
    error: str = ""
    status: int = 0
    retry_after: float = 0.0
    blocked: bool = False
    parse_error: bool = False

    def __bool__(self) -> bool:
        return self.ok


def _is_blocked(status: int, description: str) -> bool:
    d = (description or "").lower()
    if status == 403:
        return True
    return any(s in d for s in (
        "blocked by the user", "user is deactivated", "chat not found",
        "bot was kicked", "bot is not a member", "peer_id_invalid",
    ))


def _is_parse_error(status: int, description: str) -> bool:
    d = (description or "").lower()
    return status == 400 and any(s in d for s in (
        "can't parse entities", "unsupported start tag", "can't find end tag",
        "unexpected end tag", "entity text is empty",
    ))


def api_call(method: str, data: dict | None = None,
             files: dict[str, str] | None = None, token: str | None = None,
             timeout: int = 30, retries: int = 2) -> ApiResult:
    """Вызов Bot API. ``files`` — {поле: путь к файлу}; файл переоткрывается на попытку."""
    tok = token or cfg.BOT_TOKEN
    if not tok:
        log.error("не задан токен бота")
        return ApiResult(False, error="no token")

    payload = dict(data or {})
    last: ApiResult | None = None
    for attempt in range(retries + 1):
        handle = None
        fh: dict = {}
        try:
            if files:
                fh = {k: open(v, "rb") for k, v in files.items()}
            handle = requests.post(f"https://api.telegram.org/bot{tok}/{method}",
                                   data=payload, files=fh or None, timeout=timeout)
        except requests.RequestException as e:
            last = ApiResult(False, error=f"сеть: {e}")
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            log.error("сеть %s: %s", method, e)
            return last
        except OSError as e:
            log.error("файл %s: %s", method, e)
            return ApiResult(False, error=f"файл: {e}")
        finally:
            for f in fh.values():
                try:
                    f.close()
                except Exception:
                    pass

        try:
            body = handle.json()
        except ValueError:
            body = {}
        if handle.status_code == 200 and body.get("ok"):
            return ApiResult(True, result=body.get("result"))

        desc = str(body.get("description") or handle.text[:300])
        retry_after = float(((body.get("parameters") or {}).get("retry_after")) or 0)
        res = ApiResult(
            False, error=desc, status=handle.status_code,
            retry_after=retry_after,
            blocked=_is_blocked(handle.status_code, desc),
            parse_error=_is_parse_error(handle.status_code, desc),
        )
        if handle.status_code == 429:
            return res
        if handle.status_code >= 500 and attempt < retries:
            last = res
            time.sleep(1.5 * (attempt + 1))
            continue
        log.error("telegram %s: %s %s", method, handle.status_code, desc[:300])
        return res
    return last or ApiResult(False, error="unknown")


def _api(method: str, data: dict, files: dict | None = None,
         timeout: int = 30, token: str | None = None) -> tuple[bool, str]:
    """Совместимость со старым кодом: (успех, «ok» либо текст ошибки)."""
    res = api_call(method, data, files, token=token, timeout=timeout)
    return res.ok, ("ok" if res.ok else res.error)


def _caption(text: str) -> str:
    text = text or ""
    return text if len(text) <= CAPTION_LIMIT else text[: CAPTION_LIMIT - 1] + "…"


def send_photo_result(path: str, caption: str, token: str | None = None,
                      chat_id: str | int | None = None,
                      markup: dict | None = None) -> ApiResult:
    chat = cfg.CHAT_ID if chat_id is None else str(chat_id)
    if not chat:
        return ApiResult(False, error="no chat_id")
    data = {"chat_id": chat, "caption": _caption(caption), "parse_mode": "HTML"}
    if markup:
        data["reply_markup"] = json.dumps(markup, ensure_ascii=False)
    res = api_call("sendPhoto", data, {"photo": path}, token=token)
    if res.parse_error:
        # Подпись — украшение, карточка важнее. Повторяем без HTML.
        data.pop("parse_mode", None)
        res = api_call("sendPhoto", data, {"photo": path}, token=token)
    return res


def send_photo(path: str, caption: str, token: str | None = None,
               chat_id: str | int | None = None,
               markup: dict | None = None) -> tuple[bool, str]:
    res = send_photo_result(path, caption, token=token, chat_id=chat_id, markup=markup)
    return res.ok, ("ok" if res.ok else res.error)


def send_message_result(text: str, token: str | None = None,
                        chat_id: str | int | None = None,
                        markup: dict | None = None) -> ApiResult:
    chat = cfg.CHAT_ID if chat_id is None else str(chat_id)
    if not chat:
        return ApiResult(False, error="no chat_id")
    data = {"chat_id": chat, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": "true"}
    if markup:
        data["reply_markup"] = json.dumps(markup, ensure_ascii=False)
    res = api_call("sendMessage", data, token=token)
    if res.parse_error:
        data.pop("parse_mode", None)
        res = api_call("sendMessage", data, token=token)
    return res


def send_message(text: str, token: str | None = None,
                 chat_id: str | int | None = None,
                 markup: dict | None = None) -> tuple[bool, str]:
    res = send_message_result(text, token=token, chat_id=chat_id, markup=markup)
    return res.ok, ("ok" if res.ok else res.error)


def edit_caption_result(chat_id: str | int, message_id: int | str, caption: str,
                        markup: dict | None = None,
                        token: str | None = None) -> ApiResult:
    data = {"chat_id": str(chat_id), "message_id": str(message_id),
            "caption": _caption(caption), "parse_mode": "HTML"}
    if markup is not None:
        data["reply_markup"] = json.dumps(markup, ensure_ascii=False)
    res = api_call("editMessageCaption", data, token=token)
    if res.parse_error:
        data.pop("parse_mode", None)
        res = api_call("editMessageCaption", data, token=token)
    return res


def edit_caption(chat_id: str | int, message_id: int | str, caption: str,
                 markup: dict | None = None,
                 token: str | None = None) -> tuple[bool, str]:
    res = edit_caption_result(chat_id, message_id, caption, markup=markup, token=token)
    return res.ok, ("ok" if res.ok else res.error)


def _api_json(method: str, data: dict, token: str | None = None,
              timeout: int = 30) -> tuple[bool, object]:
    """Вызов метода с возвратом поля result. (успех, result либо текст ошибки)."""
    res = api_call(method, data, token=token, timeout=timeout)
    return (True, res.result) if res.ok else (False, res.error)


def create_invoice_link(title: str, description: str, payload: str, stars: int,
                        subscription: bool = True,
                        token: str | None = None) -> tuple[bool, str]:
    """Счёт в Stars. Возвращает (успех, ссылка на оплату либо причина ошибки).

    subscription=True — автопродление раз в 30 дней (другой период Telegram не даёт).
    subscription=False — разовый счёт (например, годовой план).
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


def get_chat(chat_id: str | int | None = None,
             token: str | None = None) -> tuple[bool, object]:
    """Информация о чате: заголовок, закреплённое сообщение, описание."""
    chat = cfg.CHAT_ID if chat_id is None else str(chat_id)
    return _api_json("getChat", {"chat_id": chat}, token=token)


def set_chat_description(description: str, chat_id: str | int | None = None,
                         token: str | None = None) -> tuple[bool, str]:
    chat = cfg.CHAT_ID if chat_id is None else str(chat_id)
    return _api("setChatDescription",
                {"chat_id": chat, "description": description[:255]}, token=token)


def pin_chat_message(chat_id: str | int | None, message_id: int | str,
                     token: str | None = None) -> tuple[bool, str]:
    chat = cfg.CHAT_ID if chat_id is None else str(chat_id)
    return _api("pinChatMessage", {"chat_id": chat, "message_id": str(message_id),
                                   "disable_notification": "true"}, token=token)


def unpin_chat_message(chat_id: str | int | None, message_id: int | str | None = None,
                       token: str | None = None) -> tuple[bool, str]:
    chat = cfg.CHAT_ID if chat_id is None else str(chat_id)
    data = {"chat_id": chat}
    if message_id is not None:
        data["message_id"] = str(message_id)
    return _api("unpinChatMessage", data, token=token)


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
