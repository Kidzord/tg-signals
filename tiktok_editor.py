"""Карусель для TikTok: 5–6 готовых картинок + текст, всё приходит в бот.

TikTok API для фото-каруселей существует, но требует одобренное приложение,
OAuth и аудит; до аудита посты уходят только в приватный аккаунт. Поэтому
рабочий путь — как с Threads: бот готовит альбом и текст, владелец загружает
в приложении и выбирает музыку.

Команда в premium-боте (только владелец):
    /tt          собрать карусель по последним данным
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

from PIL import Image

import config as cfg
import devlog
import render as card
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


def caption() -> str:
    """Короткий текст для TikTok (до 2200 символов), в тоне дневника разработки."""
    handle = (cfg.CHANNEL_HANDLE or "@KidzordFX").lstrip("@")
    day_no = devlog.day_number()
    lines = [f"День {day_no} дневника. Я — обычный парень, месяц пилю торгового "
             "бота: канал и соцсети ведёт он сам, я пишу код и вычитываю."]
    trade = _latest_trade()
    if trade:
        r = float(trade.get("total_r", trade.get("r", 0)) or 0)
        lines.append(f"Последняя сделка: {trade.get('symbol', '')} {r:+.1f}R. "
                     "Вход, стоп и выход — в карусели, цифры из журнала сделок.")
    else:
        sig = _latest_signal()
        if sig:
            lines.append(f"Свежий сигнал: {sig.get('side', '')} {sig.get('symbol', '')}. "
                         "Вход, стоп и цели — в карусели.")
    note = devlog.next_devnote()
    if note:
        lines.append(f"В разработке: {note}.")
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
        (210, f"ДЕНЬ {day_no}",
         "Этот пост написал не я.\n"
         "Его написал бот, которого пишу я.\n\n"
         "Месяц пилю торгового бота.", "01_cover"),
        (211, "КТО ЗДЕСЬ ПИШЕТ",
         "Обычный парень. Код — по вечерам, вместе с ИИ.\n"
         "Канал и соцсети ведёт сам бот.", "02_who"),
        (212, "ЧТО БУДЕТ В ДНЕВНИКЕ",
         "Код. Сделки. Стопы. Цифры из журнала.\n"
         "Ничего не рисую, ничего не удаляю.", "03_what"),
        (213, "ИЩУ ТЕСТЕРОВ",
         "Заходи в группу в Telegram: покрути бота, поломай, поругай.\n"
         "Бесплатно — за фидбек.", "08_testers"),
        (214, "ДНЕВНИК РАЗРАБОТКИ",
         "Каждый день: что добавил в код.\n"
         "Каждая сделка: вход, стоп, итог.\n"
         f"Telegram: @{handle}", "09_cta"),
    ]


def build() -> tuple[list[str], str]:
    """Собрать карусель: сюжет дневника + настоящие карточки сделок и цифр."""
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

    trade = _latest_trade()
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
    return unique[:10], caption()


def send(chat_id: str, token: str | None = None) -> bool:
    images, text = build()
    if not images:
        tg.send_plain("Не удалось собрать карусель: нет картинок.",
                      token=token, chat_id=chat_id)
        return False
    res = tg.send_media_group(
        images, caption="🎠 <b>Карусель для TikTok · дневник</b>\n"
                        "Порядок: крючок → кто пишет → что в дневнике → сделки "
                        "и цифры → тестеры → CTA. Сохрани фото как есть.",
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
    imgs, text = build()
    print("images:", len(imgs))
    for p in imgs:
        print(" ", p)
    print("\n" + text)
