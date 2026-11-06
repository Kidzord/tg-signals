"""Карусель для TikTok: 5–6 готовых картинок + текст, всё приходит в бот.

TikTok API для фото-каруселей существует, но требует одобренное приложение,
OAuth и аудит; до аудита посты уходят только в приватный аккаунт. Поэтому
рабочий путь — как с Threads: бот готовит альбом и текст, владелец загружает
в приложении и выбирает музыку.

Команда в premium-боте (только владелец):
    /tt            дневниковая карусель (кто пишет, сделки, цифры)
    /tt shipped    карусель «что сделал в боте» (изменения + статистика)

Запуск: python tiktok_editor.py [story|shipped]
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time

from PIL import Image

import config as cfg
import devlog
import render as card
import social_mix
import tg
import threads_editor
import track_record

HERE = os.path.dirname(os.path.abspath(__file__))
SIGNALS_PATH = os.path.join(HERE, "signals_queue.jsonl")
TRADES_PATH = os.path.join(HERE, "trades.jsonl")
OUT_DIR = os.path.join(HERE, "renders", "tiktok")


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
                    last = json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return None
    return last


def _fit(src: str, name: str) -> str | None:
    """TikTok: JPEG, максимум 1080p. Наши карточки 1280×720 тоже подходят."""
    if not src or not os.path.exists(src):
        return None
    os.makedirs(OUT_DIR, exist_ok=True)
    dst = os.path.join(OUT_DIR, f"{name}.jpg")
    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            resample = getattr(Image, "Resampling", Image).LANCZOS
            im.thumbnail((1080, 1920), resample)
            im.save(dst, "JPEG", quality=92, optimize=True)
        return dst
    except Exception as e:
        print("tiktok: не удалось подготовить картинку", src, e)
        return None


def _generic(n: int, title: str, body: str, name: str) -> str | None:
    src = threads_editor._generic_image(n, {"title": title, "text": body,
                                            "label": ""})
    return _fit(src, name)


def _latest_signal() -> dict | None:
    rec = _read_last(SIGNALS_PATH)
    if rec and rec.get("kind") == "entry":
        return rec
    return None


def _latest_trade() -> dict | None:
    return _read_last(TRADES_PATH)


def _week_rows(now_ts: float | None = None) -> list[dict]:
    """Закрытые сделки за последние 7 дней — цифры для карусели."""
    cutoff = (time.time() if now_ts is None else now_ts) - 7 * 86400
    rows: list[dict] = []
    if not os.path.exists(TRADES_PATH):
        return rows
    try:
        with open(TRADES_PATH, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("kind") in ("tp1", "tp2"):
                    continue
                if float(rec.get("ts", 0)) >= cutoff:
                    rows.append(rec)
    except OSError:
        return rows
    return rows


def _trades_word(n: int) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return "сделка"
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return "сделки"
    return "сделок"


def _stats_text(now_ts: float | None = None) -> str:
    rows = _week_rows(now_ts)
    if not rows:
        return "За 7 дней сделок не было.\nЖду сетап, а не повод зайти."
    n = len(rows)
    total = sum(float(t.get("r", 0)) for t in rows)
    wins = sum(1 for t in rows if float(t.get("r", 0)) > 0)
    return (f"За 7 дней: {n} {_trades_word(n)}, {total:+.1f}R.\n"
            f"Винрейт {100.0 * wins / n:.0f}%.")


def _short(text: str, limit: int = 170) -> str:
    """Обрезать длинную заметку по границе слова — для слайда."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(".,;:!?") + "…"


def caption(variant: str = "story") -> str:
    """Короткий текст для TikTok (до 2200 символов), в тоне дневника разработки."""
    handle = (cfg.CHANNEL_HANDLE or "@KidzordFX").lstrip("@")
    day_no = devlog.day_number()
    trade = social_mix.latest_positive()
    trade_line = (f"Последняя закрытая в плюс: {social_mix.trade_line(trade)}. "
                  "Скрин — в карусели." if trade else "")
    if variant == "shipped":
        notes = [devlog.note_for_day(day_no, i) for i in (0, 1)]
        lines = [f"Вот мой пост на сегодня. День {day_no}: что сделал по проекту."]
        for note in notes:
            if note:
                lines.append(f"— {note}.")
        if trade_line:
            lines.append(trade_line)
        lines.append(_stats_text().replace("\n", " "))
        lines.append(f"Полный дневник и журнал сделок — в Telegram: t.me/{handle}")
    else:
        lines = [f"Вот мой пост на сегодня. День {day_no}: месяц пилю торгового "
                 "бота, пишу сам, цифры беру из журнала сделок."]
        if trade_line:
            lines.append(trade_line)
        else:
            sig = _latest_signal()
            if sig:
                lines.append(f"Свежий сигнал: {sig.get('side', '')} {sig.get('symbol', '')}. "
                             "Вход, стоп и цели — в карусели.")
        note = devlog.note_for_day(day_no, 0)
        if note:
            lines.append(f"Что делал по проекту: {note}.")
        lines.append(_stats_text().replace("\n", " "))
        lines.append(f"Полный дневник и журнал сделок — в Telegram: t.me/{handle}")
    days = cfg.PROMO_CODES.get("TIKTOK")
    if days:
        lines.append(f"Промокод TIKTOK: +{days} дн. к доступу в боте.")
    lines.append("#трейдинг #золото #форекс #трейдер")
    return "\n".join(lines)


def _story_slides(day_no: int, handle: str) -> list[tuple[int, str, str, str]]:
    """Сюжетные слайды дневника: (сид картинки, заголовок, текст, имя файла).

    Первые три — обложка и рассказ, кто пишет; последние два — набор тестеров
    и CTA. Между ними бот вставляет реальные карточки: вход, выход, счёт, equity.
    """
    return [
        (310, f"ДЕНЬ {day_no}",
         "Вот мой пост на сегодня.\n"
         "Код, сделки и цифры — без прикрас.\n\n"
         "Месяц пилю торгового бота.", "01_cover"),
        (311, "КТО ЗДЕСЬ ПИШЕТ",
         "Пишу сам. Код — по вечерам, вместе с ИИ.\n"
         "Бот считает цифры, текст — мой.", "02_who"),
        (312, "ЧТО БУДЕТ В ДНЕВНИКЕ",
         "Код. Сделки. Стопы. Цифры из журнала.\n"
         "Ничего не рисую, ничего не удаляю.", "03_what"),
        (313, "ИЩУ ТЕСТЕРОВ",
         "Заходи в группу в Telegram: покрути бота, поломай, поругай.\n"
         "Бесплатно — за фидбек.", "08_testers"),
        (314, "ДНЕВНИК РАЗРАБОТКИ",
         "Каждый день: что добавил в код.\n"
         "Каждая сделка: вход, стоп, итог.\n"
         f"Telegram: @{handle}", "09_cta"),
    ]


def _shipped_slides(day_no: int, handle: str,
                    notes: list[str]) -> list[tuple[int, str, str, str]]:
    """Слайды «что сделал в боте»: обложка, две заметки, цифры, CTA."""
    first = _short(notes[0]) if notes else "Новые правки в коде и доставке."
    second = (_short(notes[1]) if len(notes) > 1
              else "Продолжаю чинить то, что ломается на живых данных.")
    return [
        (320, f"ДЕНЬ {day_no}",
         "Вот мой пост на сегодня.\n"
         "Что реально изменилось в проекте.\n"
         "Без маркетинга: только код.", "01_shipped_cover"),
        (321, "ЧТО ДОБАВИЛ", first, "02_shipped_note1"),
        (322, "ЕЩЁ В КОДЕ", second, "03_shipped_note2"),
        (323, "ЦИФРЫ НЕДЕЛИ", _stats_text(), "04_shipped_stats"),
        (324, "ДНЕВНИК РАЗРАБОТКИ",
         "Каждый день: что добавил в код.\n"
         f"Telegram: @{handle}", "07_shipped_cta"),
    ]


def _build_story() -> tuple[list[str], str]:
    """Собрать дневниковую карусель: сюжет + настоящие карточки сделок и цифр."""
    images: list[str] = []
    day_no = devlog.day_number()
    handle = (cfg.CHANNEL_HANDLE or "@KidzordFX").lstrip("@")
    story = _story_slides(day_no, handle)

    for seed, title, body, name in story[:3]:
        img = _generic(seed, title, body, name)
        if img:
            images.append(img)

    sig = _latest_signal()
    if sig:
        entry = _fit(str(sig.get("path", "") or ""), "04_entry")
        if entry:
            images.append(entry)

    trade = social_mix.latest_positive()
    if trade:
        exit_img = _fit(str(trade.get("image", "") or ""), "05_exit")
        if exit_img:
            images.append(exit_img)

    # План счёта: не реальный баланс, а правила риска. Без номеров счёта.
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        plan_path = os.path.join(OUT_DIR, "06_account.png")
        card.render_account_card(card.AccountPlan(
            deposit=cfg.PLAN_DEPOSIT,
            risk_pct=cfg.PLAN_RISK_PCT,
            daily_pct=cfg.PLAN_DAILY_PCT,
            total_pct=cfg.PLAN_TOTAL_PCT,
            stops_today=0,
            title="Правила счёта",
            handle=cfg.CHANNEL_HANDLE,
            when=dt.datetime.now(dt.timezone.utc),
        ), plan_path)
        fitted = _fit(plan_path, "06_account")
        if fitted:
            images.append(fitted)
    except Exception as e:
        print("tiktok: карточка счёта не отрисовалась", e)

    try:
        eq = track_record.render_equity(
            30, out_path=os.path.join(OUT_DIR, "07_equity.png"), portrait=True)
        fitted = _fit(eq, "07_equity")
        if fitted:
            images.append(fitted)
    except Exception as e:
        print("tiktok: equity не отрисовалась", e)

    for seed, title, body, name in story[3:]:
        img = _generic(seed, title, body, name)
        if img:
            images.append(img)

    # Дубликаты бывают, если сигнал и результат ссылаются на один файл.
    seen: set[str] = set()
    unique = [p for p in images if p and not (p in seen or seen.add(p))]
    return unique[:10], caption("story")


def _build_shipped() -> tuple[list[str], str]:
    """Карусель «что сделал в боте»: заметки из дневника + статистика недели."""
    images: list[str] = []
    day_no = devlog.day_number()
    handle = (cfg.CHANNEL_HANDLE or "@KidzordFX").lstrip("@")
    notes = [n for n in (devlog.note_for_day(day_no, i) for i in (0, 1)) if n]
    slides = _shipped_slides(day_no, handle, notes)

    for seed, title, body, name in slides[:4]:
        img = _generic(seed, title, body, name)
        if img:
            images.append(img)

    trade = social_mix.latest_positive()
    if trade:
        exit_img = _fit(str(trade.get("image", "") or ""), "05_shipped_exit")
        if exit_img:
            images.append(exit_img)

    try:
        eq = track_record.render_equity(
            30, out_path=os.path.join(OUT_DIR, "06_shipped_equity.png"),
            portrait=True)
        fitted = _fit(eq, "06_shipped_equity")
        if fitted:
            images.append(fitted)
    except Exception as e:
        print("tiktok: equity не отрисовалась", e)

    seed, title, body, name = slides[4]
    cta = _generic(seed, title, body, name)
    if cta:
        images.append(cta)

    seen: set[str] = set()
    unique = [p for p in images if p and not (p in seen or seen.add(p))]
    return unique[:10], caption("shipped")


def build(variant: str = "story") -> tuple[list[str], str]:
    """Собрать карусель: story — дневник, shipped — что сделал в боте."""
    if variant == "shipped":
        return _build_shipped()
    return _build_story()


def send(chat_id: str, token: str | None = None,
         variant: str = "story") -> bool:
    images, text = build(variant)
    if not images:
        tg.send_plain("Не удалось собрать карусель: нет картинок.",
                      token=token, chat_id=chat_id)
        return False
    if variant == "shipped":
        header = ("🎠 <b>Карусель для TikTok · что сделал</b>\n"
                  "Порядок: обложка → что добавил → ещё в коде → цифры недели "
                  "→ сделка → CTA. Сохрани фото как есть.")
    else:
        header = ("🎠 <b>Карусель для TikTok · дневник</b>\n"
                  "Порядок: крючок → кто пишет → что в дневнике → сделки и цифры "
                  "→ тестеры → CTA. Сохрани фото как есть.")
    res = tg.send_media_group(
        images, caption=header,
        token=token, chat_id=chat_id)
    if not res.ok:
        tg.send_plain(f"Альбом не ушёл: {res.error[:200]}", token=token, chat_id=chat_id)
        return False
    tg.send_plain(
        "📋 Текст для TikTok:\n\n" + text +
        "\n\n🎵 Музыка: спокойный lofi / chill phonk.\n"
        "📲 TikTok → + → Загрузить → Фото → выбери картинки по порядку → вставь текст.",
        token=token, chat_id=chat_id)
    return True


if __name__ == "__main__":
    imgs, text = build(sys.argv[1] if len(sys.argv) > 1 else "story")
    print("images:", len(imgs))
    for p in imgs:
        print(" ", p)
    print("\n" + text)
