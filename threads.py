"""Публикация в Threads через официальный Threads API (graph.threads.com).

Нужны:
  THREADS_USER_ID  — id профиля Threads
  THREADS_TOKEN    — long-lived access token
  THREADS_IMAGE_BASE (необязательно) — публичный адрес, по которому доступны карточки;
                     без него постим только текст.

Особенности API, которые важно помнить:
  * текст ограничен 500 символами, эмодзи считаются по байтам;
  * HTML-разметки нет — теги вырезаются;
  * картинка передаётся ссылкой, Threads сам её скачивает;
  * публикация двухшаговая: создать контейнер → дождаться обработки → опубликовать.
"""
from __future__ import annotations

import os
import re
import time

import requests

API = "https://graph.threads.com/v1.0"
LIMIT = 500


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def enabled() -> bool:
    return bool(_env("THREADS_USER_ID") and _env("THREADS_TOKEN"))


def plain(text: str) -> str:
    """Убираем HTML-теги и лишние пустые строки — Threads разметку не понимает."""
    t = re.sub(r"<[^>]+>", "", text)
    t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if len(t.encode("utf-8")) > LIMIT:
        # режем по символам, но с запасом на многобайтовые эмодзи
        t = t[: LIMIT - 1].rstrip() + "…"
    return t


def _create(payload: dict) -> tuple[bool, str]:
    payload = {**payload, "access_token": _env("THREADS_TOKEN")}
    try:
        r = requests.post(f"{API}/{_env('THREADS_USER_ID')}/threads", data=payload, timeout=60)
    except requests.RequestException as e:
        return False, f"сеть: {e}"
    if r.status_code != 200:
        return False, f"{r.status_code}: {r.text[:300]}"
    return True, r.json().get("id", "")


def _publish(creation_id: str) -> tuple[bool, str]:
    data = {"creation_id": creation_id, "access_token": _env("THREADS_TOKEN")}
    try:
        r = requests.post(f"{API}/{_env('THREADS_USER_ID')}/threads_publish", data=data, timeout=60)
    except requests.RequestException as e:
        return False, f"сеть: {e}"
    if r.status_code != 200:
        return False, f"{r.status_code}: {r.text[:300]}"
    return True, r.json().get("id", "")


def post(text: str, image_url: str | None = None, wait_sec: int = 30) -> tuple[bool, str]:
    """Текст или картинка с текстом. Возвращает (успех, id публикации либо ошибка)."""
    if not enabled():
        return False, "не заданы THREADS_USER_ID / THREADS_TOKEN"

    body = plain(text)
    payload = {"media_type": "TEXT", "text": body}
    if image_url:
        payload = {"media_type": "IMAGE", "image_url": image_url, "text": body}

    ok, info = _create(payload)
    if not ok:
        return False, info

    if image_url and wait_sec:
        time.sleep(wait_sec)   # Threads просит ~30 с на обработку медиа

    return _publish(info)


def health() -> dict:
    """Проверка токена: дёргаем собственный профиль."""
    if not enabled():
        return {"ok": False, "reason": "нет THREADS_USER_ID / THREADS_TOKEN"}
    try:
        r = requests.get(
            f"{API}/{_env('THREADS_USER_ID')}",
            params={"fields": "id,username", "access_token": _env("THREADS_TOKEN")},
            timeout=30,
        )
    except requests.RequestException as e:
        return {"ok": False, "reason": f"сеть: {e}"}
    if r.status_code != 200:
        return {"ok": False, "reason": f"{r.status_code}: {r.text[:200]}"}
    return {"ok": True, "profile": r.json()}


if __name__ == "__main__":
    import json
    print(json.dumps(health(), ensure_ascii=False, indent=2))
