"""Редакционный режим Threads: бот присылает владельцу картинку и готовый текст.

API Threads не нужен. Владелец получает в личку два сообщения:

1. картинку 1080×1350 (готова к загрузке);
2. текст поста без разметки — его можно скопировать одним долгим нажатием
   и вставить в Threads. Кнопка «Открыть Threads» подставляет текст в intent.

Команды в premium-боте (только владелец):
    /post              следующий пост из банка
    /post 3            конкретный пост
    /post signal       последний сигнал с карточкой
    /post result       последняя закрытая сделка
    /post week         недельный трек-рекорд с equity-картинкой
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse

import config as cfg
import render as card
import tg
import threads
import threads_post
import track_record

HERE = os.path.dirname(os.path.abspath(__file__))
SIGNALS_PATH = os.path.join(HERE, "signals_queue.jsonl")
TRADES_PATH = os.path.join(HERE, "trades.jsonl")
RENDERS = os.path.join(HERE, "renders")


def _cta() -> str:
    handle = (cfg.CHANNEL_HANDLE or "@KidzordFX").lstrip("@")
    lines = [f"\n\nЖурнал сделок: t.me/{handle}"]
    days = cfg.PROMO_CODES.get("THREADS")
    if days:
        lines.append(f"Промокод THREADS: +{days} дн. к доступу в боте.")
    return "\n".join(lines)


def _finish(text: str) -> str:
    """Убрать HTML, добавить CTA и уложиться в лимит Threads (500 байт)."""
    cta = _cta()
    budget = max(60, 500 - len(cta.encode("utf-8")))
    body = threads.plain((text or "").rstrip(), limit=budget)
    return body + cta


def _buttons(text: str) -> dict:
    intent = threads_post.INTENT + urllib.parse.quote(text, safe="")
    return {"inline_keyboard": [
        [{"text": "🚀 Открыть Threads", "url": intent}],
        [{"text": "➡️ Следующий", "callback_data": "post:next"},
         {"text": "📈 Сигнал", "callback_data": "post:signal"}],
        [{"text": "📊 Неделя", "callback_data": "post:week"},
         {"text": "🏁 Результат", "callback_data": "post:result"}],
    ]}


def _send(chat_id: str, token: str | None, image: str | None,
          caption: str, text: str) -> bool:
    sent_photo = False
    if image and os.path.exists(image):
        res = tg.send_photo_result(image, caption, token=token, chat_id=chat_id)
        sent_photo = res.ok
        if not res.ok:
            print("threads_editor: картинка не ушла:", res.error)
    # Текст отдельным сообщением и без HTML: так его удобно копировать.
    res = tg.send_plain_result(text, token=token, chat_id=chat_id,
                               markup=_buttons(text))
    if not res.ok:
        print("threads_editor: текст не ушёл:", res.error)
        return False
    if not sent_photo and image:
        # Картинка была, но не ушла — предупредим, чтобы не искать её молча.
        tg.send_plain("Картинка не приложилась — проверь файл на диске.",
                      token=token, chat_id=chat_id)
    return True


def _read_last(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    last = None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                last = rec
    except OSError:
        return None
    return last


def send_next(chat_id: str, token: str | None = None, number: int | None = None) -> bool:
    n = number or threads_post.next_unused()
    if n is None:
        tg.send_plain("📝 Банк постов для Threads закончился. "
                      "Пора добавить новые — скажи мне, и я подготовлю.",
                      token=token, chat_id=chat_id)
        return False
    post = threads_post.POSTS[n - 1]
    text = _finish(post["text"])
    image = str(threads_post.IMG_DIR / f"{n:02d}.png")
    caption = (f"📝 <b>Пост #{n} · {post['title']}</b>\n"
               "Картинка 1080×1350 готова. Текст ниже — скопируй одним нажатием.")
    ok = _send(chat_id, token, image, caption, text)
    if ok:
        threads_post.mark_used(n)
    return ok


def send_signal(chat_id: str, token: str | None = None) -> bool:
    rec = _read_last(SIGNALS_PATH)
    if not rec or rec.get("kind") != "entry":
        tg.send_plain("📈 Пока нет нового сигнала для Threads.",
                      token=token, chat_id=chat_id)
        return False
    text = _finish(str(rec.get("caption", "")))
    image = str(rec.get("path", "") or "")
    side = "LONG" if str(rec.get("side", "")).upper() == "LONG" else "SHORT"
    caption = (f"📈 <b>Сигнал {side} {rec.get('symbol', '')}</b>\n"
               "Карточка готова. Текст ниже — для Threads.")
    return _send(chat_id, token, image, caption, text)


def send_week(chat_id: str, token: str | None = None) -> bool:
    text = _finish(track_record.text_report(7))
    os.makedirs(RENDERS, exist_ok=True)
    image = track_record.render_equity(
        7, out_path=os.path.join(RENDERS, "threads_week.png"), portrait=True)
    caption = ("📊 <b>Итог недели</b>\n"
               "Картинка 1080×1350. Текст ниже — для Threads.")
    return _send(chat_id, token, image or None, caption, text)


def send_result(chat_id: str, token: str | None = None) -> bool:
    rec = _read_last(TRADES_PATH)
    if not rec:
        tg.send_plain("🏁 Пока нет закрытых сделок для поста.",
                      token=token, chat_id=chat_id)
        return False
    kind = str(rec.get("kind", ""))
    r = float(rec.get("total_r", rec.get("r", 0)) or 0)
    symbol = str(rec.get("symbol", ""))
    side = str(rec.get("side", ""))
    emoji = {"sl": "🛑", "tp3": "🏁", "tp": "✅", "be": "⚪", "time": "⏱"}.get(kind, "📌")
    label = {
        "sl": "Стоп по правилам.",
        "tp3": "Цель взята.",
        "tp": "Цель взята.",
        "be": "Безубыток.",
        "time": "Закрыл по времени.",
    }.get(kind, "Сделка закрыта.")
    body = (f"{emoji} {symbol}, {'лонг' if side == 'LONG' else 'шорт'}: {r:+.1f}R\n"
            f"{label}\nПубликую и минусы, и плюсы — это и есть журнал.")
    text = _finish(body)

    image = None
    try:
        os.makedirs(RENDERS, exist_ok=True)
        path = os.path.join(RENDERS, f"threads_result_{int(float(rec.get('ts', time.time())))}.png")
        card.render_result_card(
            symbol=symbol, side=side, kind=kind or "sl", r=r,
            entry=rec.get("entry"), stop=rec.get("stop"), exit_px=rec.get("exit"),
            note=str(rec.get("note", "") or ""), tf=str(rec.get("tf", "15")),
            out_path=path)
        image = path
    except Exception as e:
        print("threads_editor: карточка результата не отрисовалась:", e)

    caption = (f"🏁 <b>Результат {symbol}</b>\n"
               "Карточка готова. Текст ниже — для Threads.")
    return _send(chat_id, token, image, caption, text)
