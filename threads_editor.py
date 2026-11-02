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
import textwrap
import time
import urllib.parse

import config as cfg
import devlog
import render as card
import tg
import theme
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


def _generic_image(n: int, post: dict) -> str:
    """Карточка для постов, под которые ещё нет нарисованной картинки."""
    gen_dir = os.path.join(str(threads_post.IMG_DIR), "generated")
    os.makedirs(gen_dir, exist_ok=True)
    path = os.path.join(gen_dir, f"{n:02d}.png")
    if os.path.exists(path):
        return path
    c = theme.Card(10.8, 13.5)
    c.text(0.06, 0.945, "@KIDZORDFX", size=13, weight="bold",
           display=True, color=theme.LABEL)
    c.text(0.06, 0.905, theme.spaced("ЖУРНАЛ СДЕЛОК"), size=8.5,
           color=theme.LABEL3)
    label = post.get("label", f"#{n:02d}")
    if label:
        c.text(0.94, 0.945, str(label), size=13, color=theme.ACCENT, ha="right")
    c.hairline(0.06, 0.94, 0.875)

    y = 0.80
    for line in textwrap.wrap(post.get("title", "").upper(), width=16)[:2]:
        c.text(0.06, y, line, size=26, weight="bold", display=True)
        y -= 0.065
    y -= 0.03
    body = " ".join(str(post.get("text", "")).splitlines()[:4])
    for line in textwrap.wrap(body, width=30)[:7]:
        c.text(0.06, y, line, size=17, color=theme.LABEL2, va="top")
        y -= 0.052

    c.hairline(0.06, 0.94, 0.15)
    c.text(0.06, 0.105, "t.me/KidzordFX", size=13, color=theme.ACCENT)
    c.text(0.06, 0.065, "не является инвестиционной рекомендацией",
           size=8, color=theme.LABEL3)
    return c.save(path)


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
    image = _generic_image(n, post)
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
    image = str(rec.get("path", "") or "")
    symbol = str(rec.get("symbol", ""))
    side = "лонг" if str(rec.get("side", "")).upper() == "LONG" else "шорт"
    entry = float(rec.get("entry") or 0)
    sl = float(rec.get("sl") or 0)
    tp1 = float(rec.get("tp1") or 0)
    if entry > 0 and sl > 0:
        levels = f"Вход {entry:g}, стоп {sl:g}"
        if tp1 > 0:
            levels += f", цель {tp1:g}"
        body = (f"День {devlog.day_number()}. Бот нашёл сетап: {side} {symbol}.\n"
                f"{levels}.\n"
                "Работаю до цели. Итог будет в журнале — вместе со стопами.")
        text = _finish(body)
    else:
        text = _finish(str(rec.get("caption", "")))
    caption = (f"📈 <b>Сигнал {side} {symbol}</b>\n"
               "Карточка готова. Текст ниже — для Threads.")
    return _send(chat_id, token, image, caption, text)


def send_week(chat_id: str, token: str | None = None) -> bool:
    text = _finish(f"День {devlog.day_number()}.\n" + track_record.text_report(7))
    os.makedirs(RENDERS, exist_ok=True)
    image = track_record.render_equity(
        7, out_path=os.path.join(RENDERS, "threads_week.png"), portrait=True)
    caption = ("📊 <b>Итог недели</b>\n"
               "Картинка 1080×1350. Текст ниже — для Threads.")
    return _send(chat_id, token, image or None, caption, text)


_RESULT_VOICE: dict[str, tuple[str, ...]] = {
    "sl": (
        "Бот вошёл по правилам, рынок сказал «нет». Журнал записал — идём дальше.",
        "Стоп. Не каждый сетап выживает — потому и дневник, а не витрина.",
        "Выбило по стопу. Риск был ограничен, эмоций ноль — за это и код.",
    ),
    "tp3": (
        "Бот дожал финальную цель, я не мешал. Математика 3R против 1R работает.",
        "Полный выход по цели. Заход скучный, выход по плану — иначе и не надо.",
    ),
    "tp": (
        "Бот дожал цель, я не мешал. Одна 3R перекрывает три стопа.",
        "Плюс. Такие сделки обычно пропускаешь глазами — а они и кормят.",
    ),
    "be": (
        "Ноль. Выход в безубытке: часть спасли, часть отдали рынку. Так задумано.",
        "В ноль — не минус. Правила сработали, идея не взлетела.",
    ),
    "time": (
        "Закрыл по времени: идея выдохлась. Журнал честный, даже скучный.",
        "Тайм-аут. Сетап не отработал за отведённое — фиксируем и дальше.",
    ),
}

_RESULT_CLOSERS = (
    "Публикую и плюсы, и минусы — это журнал, не витрина.",
    "Цифры из журнала сделок, рисовать тут нечего.",
    "Минусы публикую так же спокойно, как плюсы. Так работает дневник.",
)


def _pick(options: tuple[str, ...], seed: str) -> str:
    import hashlib

    h = int(hashlib.md5(seed.encode()).hexdigest()[:8], 16)
    return options[h % len(options)]


def send_result(chat_id: str, token: str | None = None) -> bool:
    rec = _read_last(TRADES_PATH)
    if not rec:
        tg.send_plain("🏁 Пока нет закрытых сделок для поста.",
                      token=token, chat_id=chat_id)
        return False
    kind = str(rec.get("kind", ""))
    r = float(rec.get("total_r", rec.get("r", 0)) or 0)
    symbol = str(rec.get("symbol", ""))
    side = "лонг" if str(rec.get("side", "")).upper() == "LONG" else "шорт"
    emoji = {"sl": "🛑", "tp3": "🏁", "tp": "✅", "be": "⚪", "time": "⏱"}.get(kind, "📌")
    seed = str(rec.get("trade_id", "") or f"{symbol}{r}")
    line = _pick(_RESULT_VOICE.get(kind) or _RESULT_VOICE["sl"], seed)
    closer = _pick(_RESULT_CLOSERS, seed + "closer")
    body = (f"День {devlog.day_number()}.\n"
            f"{emoji} {symbol}, {side}: {r:+.1f}R\n"
            f"{line}\n{closer}")
    text = _finish(body)

    image = str(rec.get("image") or "")
    if not image or not os.path.exists(image):
        image = ""
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
