"""Premium-бот: подписка в Telegram Stars или крипте, быстрые сигналы и карточка счёта.

Запуск:  python subbot.py
Токен:   SUB_BOT_TOKEN в .env (отдельный бот от сигнального, см. subbot.bat).

Сигналы бот не считает сам: bot.py кладёт каждый опубликованный сигнал в
signals_queue.jsonl, а premium-бот рассылает их подписчикам сразу — бесплатная
группа получает тот же сигнал с задержкой FREE_DELAY_SEC.

Доступ — по активной подписке (users.json, поле paid_until). Оплата в Stars
(автопродление раз в 30 дней) или криптой через Crypto Pay (разово, бот
напоминает о продлении).

Команды:
    /start             приветствие и инструкция
    /buy               оформить подписку (Stars или крипта)
    /status            до какого числа активна подписка
    /deposit 10000     депозит, $
    /risk 0.5          риск на сделку, % от депозита
    /daily 5           дневной стоп, % от депозита
    /total 10          общий стоп, % от депозита
    /limits 0.5 5 10   риск, дневной и общий стоп сразу
    /mycard            карточка-сводка под твои параметры
    /lesson            следующая методичка (бесплатно)
    /lessons           список методичек
    /id                твой Telegram ID
    /threads           привязать этот чат владельцу (напоминания о постах для Threads)
    /help              список команд

Владельцу: перешли пост из чужого канала — бот разберёт его как голос
консенсуса (см. consensus.py). Без пересылки: /ext @канал XAUUSD BUY 4020.
Чужие тексты никуда не публикуются, источник обязателен.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import brand
import backup
import config as cfg
import consensus
import crypto_pay
import discord_intake
import edu_posts
import ocr_text
import position_size
import render as card
import revenue
import risk_guard
import store
import telegram_editor
import tg
import threads_editor
import tiktok_editor
import track_record

HERE = os.path.dirname(os.path.abspath(__file__))
USERS_PATH = os.path.join(HERE, "users.json")
STATE_PATH = os.path.join(HERE, "sub_state.json")
QUEUE_PATH = os.path.join(HERE, "signals_queue.jsonl")
# События закрытия сделок (пишет bot.py) — ими обновляем карточку входа.
RESULTS_PATH = os.path.join(HERE, "results_queue.jsonl")
# Журнал закрытых сделок (пишет bot.py) — по нему считаем публичную статистику.
TRADES_PATH = os.path.join(HERE, "trades.jsonl")
OUT_DIR = os.path.join(HERE, "renders")
RISK_PATH = os.path.join(HERE, "daily_risk.log")
# Личный чат владельца: autoposts.py шлёт сюда напоминания о постах для Threads.
OWNER_PATH = os.path.join(HERE, "owner.json")
# Уже выданные счета Crypto Pay: чтобы не продлить подписку дважды по одному счёту.
CRYPTO_STATE_PATH = os.path.join(HERE, "crypto_paid.json")
BRAND_DIR = os.path.join(HERE, "brand")
ASSETS: dict[str, str] = {}
TOKEN = cfg.SUB_BOT_TOKEN
POLL_TIMEOUT = 30
# как часто проверять оплаченные счета Crypto Pay
CRYPTO_POLL_SEC = 30
# сколько живёт подписка, если Telegram не прислал дату окончания
SUB_FALLBACK_SEC = 30 * 24 * 60 * 60
# напоминание о продлении: за сколько до конца подписки и как часто проверять
REMIND_BEFORE_SEC = 3 * 24 * 60 * 60
REMIND_EVERY_SEC = 60 * 60

# границы допустимых значений
DEP_MIN, DEP_MAX = 100.0, 10_000_000.0
RISK_MIN, RISK_MAX = 0.05, 5.0
DAILY_MIN, DAILY_MAX = 0.5, 50.0
TOTAL_MIN, TOTAL_MAX = 1.0, 80.0


def price_line() -> str:
    """Строка с ценой подписки: Stars и, если подключена, крипта."""
    parts = [f"{cfg.SUB_PRICE_STARS} ⭐/мес"]
    if cfg.SUB_PRICE_STARS_YEAR > 0:
        parts.append(f"{cfg.SUB_PRICE_STARS_YEAR} ⭐/год")
    if crypto_pay.enabled():
        parts.append(f"{cfg.CRYPTO_PRICE_USDT:g} {cfg.CRYPTO_ASSET}/мес")
        if cfg.CRYPTO_PRICE_USDT_YEAR > 0:
            parts.append(f"{cfg.CRYPTO_PRICE_USDT_YEAR:g} {cfg.CRYPTO_ASSET}/год")
    return " · ".join(parts)


HELP = (
    "<b>KidzordFX Premium</b>\n\n"
    "Всё делается кнопками — внизу экрана и внутри сообщений.\n\n"
    "<b>Меню:</b>\n"
    "⚡ Premium — подписка и продление\n"
    "📊 Мой счёт — карточка счёта\n"
    "⚙️ Настройки — депозит и лимиты\n"
    "ℹ️ Помощь — этот текст\n\n"
    "<b>Команды (если удобнее текстом):</b>\n"
    f"/buy — подписка, {price_line()}\n"
    "/status — до какого числа активна\n"
    "/promo КОД — активировать промокод\n"
    "/stats — статистика сигналов (7 и 30 дней)\n"
    "/lesson — следующая методичка (бесплатно)\n"
    "/lessons — все методички списком\n"
    "/faq — частые вопросы\n"
    "/mycard — карточка счёта\n"
    "/limits 0.5 5 10 — риск, дневной и общий стоп\n"
    "/id — твой Telegram ID"
)


def channel_link() -> str:
    """Ссылка на бесплатную группу: CHANNEL_INVITE или @username в CHANNEL_HANDLE."""
    if cfg.CHANNEL_INVITE:
        return cfg.CHANNEL_INVITE
    h = cfg.CHANNEL_HANDLE
    return f"https://t.me/{h[1:]}" if h.startswith("@") else ""


def asset(name: str) -> str:
    """Путь к бренд-карточке в brand/. Недостающие рисуются на месте."""
    if not ASSETS:
        ASSETS.update(brand.ensure_brand_assets(BRAND_DIR))
    return ASSETS.get(name, "")


def channel_buttons() -> list[dict]:
    """Кнопка-ссылка на бесплатную группу, если ссылка задана в .env."""
    link = channel_link()
    return [{"text": "🆓 Бесплатные сигналы", "url": link}] if link else []


def not_paid_text() -> str:
    """Заглушка для тех, у кого нет активной подписки."""
    tail = ""
    if channel_link():
        tail = (f'\n\nБесплатные сигналы с задержкой — '
                f'<a href="{channel_link()}">{cfg.CHANNEL_HANDLE or "группа"}</a>.')
    trial = ""
    if cfg.TRIAL_ENABLED and cfg.TRIAL_DAYS > 0:
        trial = f"\n\n🎁 Есть пробный доступ на {cfg.TRIAL_DAYS} дн. — кнопка ниже."
    return (
        "🔒 <b>Нужна подписка</b>\n\n"
        "Premium открывает быстрые сигналы в боте и карточку твоего счёта.\n"
        f"Цена — {price_line()}.\n\n"
        "Бесплатно и без подписки: методички — /lesson." + trial + tail
    )


def onboard_text(until: float) -> str:
    """Что делать после первой оплаты: настроить план и получить карточку."""
    tail = ""
    if channel_link():
        tail = (f"\n\nБесплатная группа с сигналами с задержкой — "
                f'<a href="{channel_link()}">{cfg.CHANNEL_HANDLE or "группа"}</a>.')
    return (
        f"✅ <b>Подписка активна до {_date(until)}</b>\n\n"
        "Осталось два шага, чтобы бот считал под тебя:\n\n"
        "<b>1.</b> Вбей свой депозит: <code>/deposit 10000</code>\n"
        "<b>2.</b> Задай свои лимиты: <code>/limits 0.5 5 10</code>\n"
        "     (риск на сделку, дневной стоп, общий стоп — в % от депозита)\n\n"
        "Потом жми /mycard — увидишь карточку счёта: сколько риска на сделку "
        "в деньгах и сколько лимита осталось.\n\n"
        "Быстрые сигналы прилетают сюда сразу, как только система их даёт: "
        "вход, стоп и три цели." + tail +
        "\n\nВсе команды — /help."
    )


def remind_text(until: float) -> str:
    """Напоминание о продлении за REMIND_BEFORE_SEC до конца подписки."""
    left = int((until - time.time()) // 86400) + 1
    tail = ("Продлить — /buy. Оплата в Stars продлевается автоматически, "
            "крипта — разово." if crypto_pay.enabled()
            else "Продлить можно заранее, срок просто добавится: /buy\n"
                 "Отключить автопродление — в «Мои звёзды» в Telegram.")
    return (
        "⏳ <b>Подписка скоро истекает</b>\n\n"
        f"Доступ активен до <b>{_date(until)}</b> — осталось {left} дн.\n"
        + tail
    )


def usd(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ") + " $"


def _date(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, ZoneInfo(cfg.DISPLAY_TZ)).strftime("%d.%m.%Y")


# ------------------------------------------------------------------ хранилище

def load_json(path: str) -> dict:
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def user_plan(users: dict, uid: str) -> dict:
    plan = {"deposit": cfg.PLAN_DEPOSIT, "risk": cfg.PLAN_RISK_PCT,
            "daily": cfg.PLAN_DAILY_PCT, "total": cfg.PLAN_TOTAL_PCT}
    plan.update(users.get(uid, {}))
    return plan


def paid_until(users: dict, uid: str) -> float:
    try:
        return float(users.get(uid, {}).get("paid_until", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def is_paid(users: dict, uid: str) -> bool:
    return paid_until(users, uid) > time.time()


def size_line(users: dict, uid: str, rec: dict) -> str:
    """Объём под депозит и риск конкретного подписчика (или пустая строка)."""
    size = rec.get("size") if isinstance(rec, dict) else None
    if not isinstance(size, dict):
        return ""
    p = user_plan(users, uid)
    try:
        risk_money = float(p.get("deposit", 0)) * float(p.get("risk", 0)) / 100.0
    except (TypeError, ValueError):
        return ""
    res = position_size.size_for_risk(risk_money, size)
    if not res:
        return ""
    step = float(size.get("step") or 0.01)
    if res["exceeds"]:
        minimum = position_size.fmt_lots(float(size.get("min") or step), step)
        return (f"📐 Мин. лот {minimum} даст риск {usd(res['risk'])} — "
                f"это больше твоих {usd(risk_money)}")
    lots = position_size.fmt_lots(res["lots"], step)
    return f"📐 Под твой счёт: риск {usd(risk_money)} · {lots} лота"


def grant(users: dict, uid: str, until_ts: float) -> None:
    """Продлить подписку до абсолютного срока (оплата могла прийти заново)."""
    u = users.setdefault(uid, {})
    u["paid_until"] = max(paid_until(users, uid), until_ts)
    save_json(USERS_PATH, users)


def extend_subscription(users: dict, uid: str, days: int) -> float:
    """Добавить N дней к остатку: оплата при активной подписке не сжигает старый срок."""
    base = max(time.time(), paid_until(users, uid))
    until = base + max(0, int(days)) * 86400
    grant(users, uid, until)
    return until


def alert_owner(text: str) -> None:
    """Технический алерт владельцу. Не влияет на обслуживание подписчика."""
    if not cfg.DELIVERY_ALERT or not cfg.OWNER_CHAT_ID:
        return
    tg.send_message(text, token=TOKEN, chat_id=cfg.OWNER_CHAT_ID)


def apply_payment(users: dict, *, provider: str, external_id: str, uid: str,
                  amount: float = 0.0, currency: str = "", days: int = 30,
                  until_ts: float | None = None,
                  raw: dict | None = None) -> bool:
    """Идемпотентно записать платёж и выдать/продлить подписку.

    ``external_id`` — уникальный ID платежа у провайдера (charge_id Stars,
    invoice_id крипты). Повторный апдейт с тем же ID вернёт False и ничего
    не продлит.
    """
    new = store.record_payment(
        provider=provider, external_id=external_id, uid=uid,
        amount=amount, currency=currency, days=days,
        source=str((users.get(uid) or {}).get("source", "") or ""),
        raw=raw,
    )
    if not new:
        return False
    try:
        if until_ts and until_ts > 0:
            # подписка Telegram: срок диктует Telegram, берём абсолютную дату
            grant(users, uid, until_ts)
            granted = paid_until(users, uid)
        else:
            # разовый платёж на N дней: прибавляем к остатку, а не от «сейчас»
            granted = extend_subscription(users, uid, days)
        # фиксируем факт выдачи: сверка при старте не продлит платёж повторно
        store.mark_payment_granted(provider, external_id, granted)
    except Exception:
        # доступ не выдан — откатываем ledger, чтобы апдейт можно было повторить
        store.delete_payment(provider, external_id)
        raise
    if provider in ("stars", "crypto"):
        store.cancel_nurture(uid)
    return True


def reconcile_payments(users: dict) -> None:
    """Сверка ledger → users после падения между записью платежа и выдачей доступа."""
    fixed = 0
    for row in store.ungranted_payments(time.time() - 7 * 86400):
        uid = str(row.get("uid") or "")
        days = int(row.get("days") or 0)
        if not uid or days <= 0:
            continue
        until = extend_subscription(users, uid, days)
        store.mark_payment_granted(row["provider"], row["external_id"], until)
        fixed += 1
        alert_owner(
            f"⚠️ Платёж <code>{row['provider']}/{row['external_id']}</code> был записан "
            f"без выдачи доступа — восстановил: <code>{uid}</code> до {_date(until)}.")
    if fixed:
        print(f"сверка платежей: восстановлено {fixed}")


def stops_today() -> int:
    """Стопы за сегодня (UTC) — из общего журнала сигнального бота."""
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    try:
        with open(RISK_PATH, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip() == day)
    except FileNotFoundError:
        return 0


# ---------------------------------------------------- доставка (durable outbox)

def result_status(rec: dict) -> str:
    """Человеческая строка статуса сделки для подписи под карточкой входа."""
    event = str(rec.get("event", "")).lower()
    total = float(rec.get("total_r", rec.get("r", 0)) or 0)
    add = float(rec.get("r", 0) or 0)
    if event in ("tp1", "tp2"):
        text = f"{event.upper()} взят · +{add:.1f}R · всего {total:+.1f}R"
    elif event == "sl":
        text = f"Стоп · итог {total:+.1f}R"
    elif event == "be":
        text = f"Безубыток · итог {total:+.1f}R"
    elif event == "time":
        text = f"Закрыто по времени · итог {total:+.1f}R"
    else:
        text = f"Сделка закрыта · итог {total:+.1f}R"
    note = str(rec.get("note", "") or "").strip()
    if note:
        text += f"\n{note}"
    return text


def _with_status(base: str, status: str) -> str:
    """Подпись карточки + статус, не выходя за лимит Telegram (1024)."""
    base = base or ""
    room = tg.CAPTION_LIMIT - len(status) - 2
    if len(base) > room:
        base = base[: max(0, room - 1)] + "…"
    return (base + "\n\n" + status).strip()


def _symbol_allowed(user: dict, symbol: str) -> bool:
    flt = user.get("symbols") or []
    if isinstance(flt, str):
        flt = [x.strip().upper() for x in flt.split(",") if x.strip()]
    return not flt or symbol.upper() in [str(x).upper() for x in flt]


def _notify_result(row, uid: str, mid: int) -> "tg.ApiResult":
    """Результат — отдельным сообщением-ответом, чтобы Telegram дал пуш.

    Редактирование подписи карточки уведомления не вызывает: подписчик видел
    «тихий» тейк, только если сам открывал сообщение. Поэтому сначала шлём
    короткое сообщение ответом на карточку, затем best-effort обновляем
    подпись. Если отправить ответ нельзя (429/блок), отдаём ошибку наверх —
    outbox повторит; карточку в этом случае не трогаем.
    """
    res = tg.send_message_result(row["caption"], token=TOKEN,
                                 chat_id=row["chat_id"], reply_to=mid)
    if not res.ok and not res.blocked and not res.retry_after:
        # reply мог не пройти (например, карточку удалили) — шлём без reply,
        # чтобы уведомление всё равно дошло.
        res = tg.send_message_result(row["caption"], token=TOKEN,
                                     chat_id=row["chat_id"])
    if res.ok:
        caption = _with_status(
            store.entry_caption(str(row["trade_id"]), uid), row["caption"])
        try:
            tg.edit_caption_result(row["chat_id"], mid, caption, token=TOKEN)
        except Exception:
            pass
    return res


def process_deliveries(users: dict) -> None:
    """Отправить пачку сообщений из outbox с retry/429/блокировками."""
    for row in store.claim_deliveries(20):
        uid = str(row["uid"])
        kind = str(row["kind"])
        user = users.get(uid) or {}
        if user.get("blocked"):
            store.mark_delivery_terminal(row["id"], "blocked", "пользователь заблокирован")
            continue

        if kind == "entry":
            if not is_paid(users, uid):
                store.mark_delivery_terminal(row["id"], "skipped", "подписка закончилась")
                continue
            if not _symbol_allowed(user, str(row["symbol"] or "")):
                store.mark_delivery_terminal(row["id"], "skipped", "фильтр символов")
                continue
            maxday = int(user.get("maxday", 0) or 0)
            if maxday > 0 and store.sent_entries_today(uid, time.time() - 86400) >= maxday:
                store.mark_delivery_terminal(row["id"], "skipped", "дневной лимит сигналов")
                continue
            path = str(row["image_path"] or "")
            if path and os.path.exists(path):
                res = tg.send_photo_result(path, row["caption"], token=TOKEN,
                                           chat_id=row["chat_id"])
                if (not res.ok and not res.blocked and not res.retry_after
                        and int(row["attempts"]) >= 2):
                    # фото стабильно отвергается (битое/слишком большое) — отдаём текстом
                    res = tg.send_message_result(row["caption"], token=TOKEN,
                                                 chat_id=row["chat_id"])
            else:
                res = tg.send_message_result(row["caption"], token=TOKEN,
                                             chat_id=row["chat_id"])
        else:
            mid = store.entry_message_id(str(row["trade_id"]), uid)
            if mid is None:
                if store.entry_pending_exists(str(row["trade_id"]), uid):
                    # вход ещё не ушёл — результат подождёт, иначе он обгонит карточку
                    if time.time() - float(row["created_at"] or 0) > 86400:
                        store.mark_delivery_terminal(row["id"], "failed",
                                                     "карточка входа так и не доставлена")
                    else:
                        store.mark_delivery_retry(row["id"], "ждём карточку входа", 60,
                                                  count_attempt=False)
                    continue
                res = tg.send_message_result(row["caption"], token=TOKEN,
                                             chat_id=row["chat_id"])
            else:
                res = _notify_result(row, uid, mid)

        if res.ok:
            sent_mid = None
            if isinstance(res.result, dict):
                sent_mid = res.result.get("message_id")
            store.mark_delivery_sent(row["id"], sent_mid)
        elif res.blocked:
            users.setdefault(uid, {})["blocked"] = 1
            save_json(USERS_PATH, users)
            store.mark_user_blocked(uid, res.error)
            alert_owner(f"⛔ Подписчик <code>{uid}</code> заблокировал бота — "
                        "исключён из рассылки.")
        elif res.retry_after:
            if int(row["attempts"]) + 1 >= cfg.DELIVERY_MAX_ATTEMPTS:
                store.mark_delivery_terminal(row["id"], "failed", res.error)
                alert_owner(f"❗ Не доставлено {row['kind']} {row['trade_id']} "
                            f"→ <code>{uid}</code>\n429 держится: {res.error[:200]}")
            else:
                store.mark_delivery_retry(row["id"], res.error, res.retry_after)
        elif int(row["attempts"]) + 1 >= cfg.DELIVERY_MAX_ATTEMPTS:
            store.mark_delivery_terminal(row["id"], "failed", res.error)
            alert_owner(f"❗ Не доставлено {row['kind']} {row['trade_id']} "
                        f"→ <code>{uid}</code>\n{res.error[:200]}")
        else:
            delay = min(1800, 30 * (2 ** int(row["attempts"])))
            store.mark_delivery_retry(row["id"], res.error, delay)


# ---------------------------------------------------- nurture триала (0/1/3)

def process_nurture(users: dict) -> None:
    """День 1 — как читать карточку; день 3 — окончание триала."""
    for row in store.claim_nurture(20):
        uid = str(row["uid"])
        step = str(row["step"])
        user = users.get(uid) or {}
        if user.get("blocked"):
            store.mark_nurture_terminal(row["id"], "blocked", "пользователь заблокирован")
            continue
        if store.has_paid_subscription(uid):
            store.mark_nurture_terminal(row["id"], "skipped", "уже оплатил")
            continue

        markup = None
        if step == "trial_day1":
            text = (
                "📊 <b>Как читать карточку</b>\n\n"
                "Вход — цена, по которой открываем. Стоп — уровень, где идея ломается. "
                "Цели 1/2/3 — где фиксируем части позиции.\n\n"
                "R — единица риска. Риск 0,5% депозита и результат +2R = +1% депозита. "
                "Считать в R удобнее, чем в деньгах: результат не зависит от размера счёта.\n\n"
                "Проверь свой план: депозит, риск и лимиты.\n"
                "Разборы основ — /lesson: смарт-мани, MSNR, структура и риск."
            )
            markup = {"inline_keyboard": [[
                {"text": "📊 Мой счёт", "callback_data": "card"},
                {"text": "⚙️ Настройки", "callback_data": "set:menu"}]]}
        elif step == "trial_day3":
            until = paid_until(users, uid)
            text = (
                "⏳ <b>Пробный доступ заканчивается</b>\n\n"
                f"Доступ активен до <b>{_date(until)}</b>.\n\n"
                "Если сигналы и расчёт под твой счёт подходят — продли подписку. "
                "Если нет — останешься в бесплатной группе: сигналы там с задержкой 30 минут."
            )
            markup = {"inline_keyboard": [[
                {"text": f"⚡ Продлить — {cfg.SUB_PRICE_STARS} ⭐",
                 "callback_data": "buy"}]]}
        else:
            store.mark_nurture_terminal(row["id"], "skipped", "неизвестный шаг")
            continue

        res = tg.send_message_result(text, token=TOKEN, chat_id=uid, markup=markup)
        if res.ok:
            store.mark_nurture_sent(row["id"])
        elif res.blocked:
            users.setdefault(uid, {})["blocked"] = 1
            save_json(USERS_PATH, users)
            store.mark_nurture_terminal(row["id"], "blocked", res.error)
            alert_owner(f"⛔ Подписчик <code>{uid}</code> заблокировал бота (nurture).")
        elif res.retry_after:
            store.mark_nurture_retry(row["id"], res.error, res.retry_after)
        elif int(row["attempts"]) + 1 >= cfg.DELIVERY_MAX_ATTEMPTS:
            store.mark_nurture_terminal(row["id"], "failed", res.error)
            alert_owner(f"❗ Nurture {step} не доставлен → <code>{uid}</code>\n{res.error[:200]}")
        else:
            delay = min(1800, 60 * (2 ** int(row["attempts"])))
            store.mark_nurture_retry(row["id"], res.error, delay)


# ---------------------------------------------------- статистика, рефералы, проба

def load_trades() -> list[dict]:
    """Журнал закрытых сделок (пишет bot.py): время, R, символ."""
    rows: list[dict] = []
    if not os.path.exists(TRADES_PATH):
        return rows
    try:
        with open(TRADES_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    rows.append({"ts": float(d["ts"]), "r": float(d["r"]),
                                 "symbol": str(d.get("symbol", ""))})
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        pass
    return rows


def _stats_line(days: int, label: str) -> str:
    return track_record.short_line(days, label)


def send_stats(uid: str, users: dict | None = None) -> None:
    """Публичный послужной список канала: неделя и месяц + equity-картинка."""
    tail = ""
    if channel_link():
        tail = (f'\n\nВсе сигналы публикуем открыто — вход, стоп и результат. '
                f'Полный поток: <a href="{channel_link()}">{cfg.CHANNEL_HANDLE or "группа"}</a>.')
    text = (
        "📈 <b>Статистика сигналов</b>\n\n"
        + _stats_line(7, "7 дней") + "\n"
        + _stats_line(30, "30 дней") +
        "\n\n<i>R — размер результата в рисках. PF — profit factor, "
        "DD — максимальная просадка. Публикуем и плюсы, и стопы.</i>" + tail
    )
    img = track_record.render_equity(30)
    if img:
        ok, _ = tg.send_photo(img, text, token=TOKEN, chat_id=uid,
                              markup=menu_markup(users, uid) if users else None)
        if ok:
            return
    reply(uid, text)


def ref_link(uid: str) -> str:
    """Реферальная ссылка пользователя (пусто, если username бота не задан)."""
    if not cfg.REF_ENABLED or not cfg.SUB_BOT_USERNAME:
        return ""
    return f"https://t.me/{cfg.SUB_BOT_USERNAME}?start=ref_{uid}"


def bind_referrer(users: dict, uid: str, payload: str) -> None:
    """Запомнить, кто пригласил пользователя (?start=ref_<id>). Награда — при оплате."""
    if not cfg.REF_ENABLED or not payload.startswith("ref_"):
        return
    ref = payload[4:].strip()
    u = users.setdefault(uid, {})
    if not ref or ref == uid or u.get("ref_by") or u.get("ref_rewarded"):
        return
    u["ref_by"] = ref
    save_json(USERS_PATH, users)


def reward_referrer(users: dict, uid: str) -> None:
    """После оплаты: бонус пригласившему и приглашённому. Один раз на пользователя."""
    if not cfg.REF_ENABLED:
        return
    u = users.setdefault(uid, {})
    ref = str(u.get("ref_by", "") or "")
    if not ref:
        return
    changed = False

    # Пригласивший: +REF_DAYS к подписке и зачёт реферала.
    if not u.get("ref_rewarded"):
        u["ref_rewarded"] = True
        changed = True
        if ref != uid and ref in users:
            r = users[ref]
            until = max(paid_until(users, ref), time.time()) + cfg.REF_DAYS * 86400
            r["paid_until"] = until
            r["refs"] = int(r.get("refs", 0) or 0) + 1
            store.record_payment(provider="ref", external_id=f"ref:{uid}", uid=ref,
                                 amount=0.0, days=cfg.REF_DAYS,
                                 raw={"invited": uid, "source": "referral"})
            tg.send_message(
                f"🎁 По твоей ссылке оформили Premium — начислил <b>+{cfg.REF_DAYS} дн.</b>\n"
                f"Доступ активен до <b>{_date(until)}</b>. Спасибо, что приводишь своих.",
                token=TOKEN, chat_id=ref)

    # Приглашённый: +REF_DAYS_FRIEND после первой оплаты (двусторонняя программа).
    if cfg.REF_DAYS_FRIEND > 0 and not u.get("ref_friend_rewarded"):
        u["ref_friend_rewarded"] = True
        until = max(paid_until(users, uid), time.time()) + cfg.REF_DAYS_FRIEND * 86400
        u["paid_until"] = until
        changed = True
        store.record_payment(provider="ref_friend", external_id=f"ref_friend:{uid}",
                             uid=uid, amount=0.0, days=cfg.REF_DAYS_FRIEND,
                             raw={"referrer": ref, "source": "referral_friend"})
        tg.send_message(
            f"🎁 Ты пришёл по приглашению — начислил <b>+{cfg.REF_DAYS_FRIEND} дн.</b>\n"
            f"Доступ активен до <b>{_date(until)}</b>.",
            token=TOKEN, chat_id=uid)

    if changed:
        save_json(USERS_PATH, users)
        # оплатил или получил бонус — «триал заканчивается» больше не актуально
        store.cancel_nurture(uid)


def trial_available(users: dict, uid: str) -> bool:
    """Пробный доступ: доступен один раз тому, кто ещё не платил и не пробовал."""
    if not cfg.TRIAL_ENABLED or cfg.TRIAL_DAYS <= 0:
        return False
    u = users.get(uid) or {}
    return (not u.get("trial_used") and paid_until(users, uid) <= time.time()
            and not store.has_paid_subscription(uid))


def grant_trial(users: dict, uid: str) -> None:
    """Выдать пробные дни один раз: отмечаем trial_used и включаем подписку."""
    u = users.setdefault(uid, {})
    u["trial_used"] = True
    now = time.time()
    until = now + cfg.TRIAL_DAYS * 86400
    users[uid]["paid_until"] = max(paid_until(users, uid), until)
    save_json(USERS_PATH, users)
    store.record_payment(provider="trial", external_id=f"trial:{uid}", uid=uid,
                         amount=0.0, days=cfg.TRIAL_DAYS,
                         source=str(u.get("source", "") or ""),
                         raw={"source": "trial"})
    # День 0 — сразу; день 1 — как читать карточку; «заканчивается» — за 12 ч до конца.
    store.enqueue_nurture(uid, "trial_day1", now + 86400)
    day3_at = until - 12 * 3600
    store.enqueue_nurture(uid, "trial_day3", max(now + 3600, day3_at))
    tg.send_message(
        f"🎁 <b>Пробный доступ открыт на {cfg.TRIAL_DAYS} дн.</b>\n\n"
        + onboard_text(until), token=TOKEN, chat_id=uid,
        markup=menu_markup(users, uid))


def support_buttons() -> list[list[dict]]:
    """Кнопка «Поддержка», если задан SUPPORT_URL."""
    return [[{"text": "💬 Поддержка", "url": cfg.SUPPORT_URL}]] if cfg.SUPPORT_URL else []


FAQ = (
    "❓ <b>Частые вопросы</b>\n\n"
    "<b>Как считается риск?</b>\n"
    "Ты задаёшь депозит и риск на сделку (/deposit, /risk). Бот считает объём и "
    "показывает на карточке, сколько это в деньгах.\n\n"
    "<b>Что делать при стопе?</b>\n"
    "Ничего. Стоп — часть системы: риск ограничен заранее. После N стопов за день "
    "бот закрывает торговлю до завтра — это защита, а не ошибка.\n\n"
    "<b>Как отменить подписку?</b>\n"
    "Telegram Stars — «Мои звёзды» в настройках Telegram. Крипта — оплата разовая, "
    "сама не продлевается.\n\n"
    "<b>Чем Premium отличается от бесплатной группы?</b>\n"
    "В группе сигнал приходит с задержкой. В Premium — сразу, плюс расчёт под твой счёт.\n\n"
    "<b>Сигналы — это инвестсовет?</b>\n"
    "Нет. Это информация, решение по сделке принимаешь ты."
)


# ------------------------------------------------------------------ ответы

def reply(uid: str, text: str) -> None:
    tg.send_message(text, token=TOKEN, chat_id=uid)


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _send_feedback(users: dict, uid: str, raw: str) -> None:
    """Отзыв тестера → в личку владельца с пометкой источника и статуса."""
    parts = raw.split(None, 1)
    text = parts[1].strip() if len(parts) > 1 else ""
    if not text:
        reply(uid, "Напиши отзыв после команды: /feedback <текст> — что работает, "
                   "что сломалось, чего не хватает. Автор читает всё сам.")
        return
    owner = str(cfg.OWNER_CHAT_ID or "")
    if not owner:
        reply(uid, "Сейчас не могу передать автору — попробуй позже.")
        return
    u = users.get(uid) or {}
    src = str(u.get("source", "") or "direct")
    status = ("premium" if is_paid(users, uid)
              else "триал" if u.get("trial_used") else "новый")
    tg.send_message(f"💬 <b>Фидбек · {status} · {src}</b>\n"
                    f"От: <code>{uid}</code>\n\n{_esc(text)}",
                    token=TOKEN, chat_id=owner)
    reply(uid, "Спасибо, передал автору 👍")


def _refund_stars(owner_uid: str, ref: str) -> None:
    """Возврат Stars по платежу из ledger: refundStarPayment + пометка refunded."""
    row = store.payment_by_ref(ref)
    if not row:
        reply(owner_uid, f"Платёж <code>{_esc(ref)}</code> не найден в ledger.")
        return
    if str(row["provider"]) != "stars":
        reply(owner_uid, f"Платёж #{row['id']} — {row['provider']}: возвращается "
                         f"вручную через провайдера. Stars — автоматически.")
        return
    if str(row["status"]) != "paid":
        reply(owner_uid, f"Платёж #{row['id']} уже в статусе {row['status']}.")
        return
    res = tg.api_call(
        "refundStarPayment",
        {"user_id": int(row["uid"]),
         "telegram_payment_charge_id": str(row["external_id"])},
        token=TOKEN)
    if not res.ok:
        reply(owner_uid, f"❗ Возврат не прошёл: {_esc(res.error[:200])}")
        return
    store.mark_payment_refunded(provider="stars",
                                external_id=str(row["external_id"]))
    reply(owner_uid,
          f"↩️ Возврат оформлен: {row['amount']:g} Stars → <code>{row['uid']}</code>.\n"
          f"Доступ до конца оплаченного периода остаётся — снять вручную, "
          f"если нужно: /status у подписчика.")


def _num(raw: str, lo: float, hi: float) -> float | None:
    try:
        v = float(raw.replace(",", ".").strip())
    except (ValueError, AttributeError):
        return None
    return v if lo <= v <= hi else None


def card_status_line(users: dict, uid: str) -> str:
    """Строка статуса доступа в карточке счёта: срок или следующий шаг."""
    if is_paid(users, uid):
        ts = paid_until(users, uid)
        left = int((ts - time.time()) // 86400) + 1
        return f"✅ Доступ до <b>{_date(ts)}</b> — осталось {left} дн."
    if trial_available(users, uid):
        return f"🎁 Пробный доступ на {cfg.TRIAL_DAYS} дн. ещё не использован — кнопка ниже."
    return "🔒 Подписка не активна — оформить можно кнопкой ниже."


def send_card(uid: str, users: dict, name: str = "") -> None:
    p = user_plan(users, uid)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"account_{uid}.png")
    plan = card.AccountPlan(
        deposit=float(p["deposit"]),
        risk_pct=float(p["risk"]),
        daily_pct=float(p["daily"]),
        total_pct=float(p["total"]),
        stops_today=stops_today(),
        title=name or "План счёта",
        handle=cfg.CHANNEL_HANDLE,
        when=dt.datetime.now(dt.timezone.utc),
    )
    card.render_account_card(plan, path)
    caption = (
        "<b>Твой план счёта</b>\n"
        f"{card_status_line(users, uid)}\n\n"
        f"Депозит {usd(plan.deposit)} · риск {plan.risk_pct:.2f}% = "
        f"{usd(plan.risk_money)} на сделку\n"
        f"Дневной стоп {plan.daily_pct:.1f}% · общий стоп {plan.total_pct:.1f}%\n"
        f"<i>{cfg.DISCLAIMER}</i>"
    )
    ok, info = tg.send_photo(path, caption, token=TOKEN, chat_id=uid,
                             markup=menu_markup(users, uid))
    if not ok:
        reply(uid, "Не удалось отправить карточку. Попробуй позже.")


def buy_options(users: dict, uid: str) -> list[list[dict]]:
    """Способы входа: сначала бесплатный триал, затем оплата."""
    rows: list[list[dict]] = []
    if trial_available(users, uid):
        rows.append([{"text": f"🎁 Попробовать бесплатно — {cfg.TRIAL_DAYS} дн.",
                      "callback_data": "trial"}])
    rows.append([{"text": f"⭐ Telegram Stars — {cfg.SUB_PRICE_STARS} ⭐ / мес",
                  "callback_data": "pay:stars"}])
    if cfg.SUB_PRICE_STARS_YEAR > 0:
        rows.append([{"text": f"⭐ Stars — {cfg.SUB_PRICE_STARS_YEAR} ⭐ / год",
                      "callback_data": "pay:stars:year"}])
    if crypto_pay.enabled():
        rows.append([{"text": f"💠 Крипта — {cfg.CRYPTO_PRICE_USDT:g} {cfg.CRYPTO_ASSET} / мес",
                      "callback_data": "pay:crypto"}])
    if crypto_pay.enabled() and cfg.CRYPTO_PRICE_USDT_YEAR > 0:
        rows.append([{"text": f"💠 Крипта — {cfg.CRYPTO_PRICE_USDT_YEAR:g} {cfg.CRYPTO_ASSET} / год",
                      "callback_data": "pay:crypto:year"}])
    return rows


def send_buy(uid: str, users: dict | None = None) -> None:
    """Оплата подписки: карточка выгод и выбор способа (Stars / крипта)."""
    if not crypto_pay.enabled() and cfg.SUB_PRICE_STARS_YEAR <= 0:
        send_stars(uid)
        return
    rows = buy_options(users or {}, uid)
    cb = channel_buttons()
    if cb:
        rows.append(cb)
    markup = {"inline_keyboard": rows}
    trial_hint = (f"🎁 Сначала можно попробовать бесплатно — {cfg.TRIAL_DAYS} дн.\n\n"
                  if trial_available(users or {}, uid) else "")
    caption = (
        f"<b>{cfg.SUB_TITLE}</b>\n\n{trial_hint}{cfg.SUB_DESCRIPTION}\n\n"
        "Выбери, как удобнее оплатить:\n"
        f"• <b>Stars</b> — {cfg.SUB_PRICE_STARS} ⭐ в месяц, продление автоматическое\n"
    )
    if cfg.SUB_PRICE_STARS_YEAR > 0:
        caption += (f"• <b>Stars, год</b> — {cfg.SUB_PRICE_STARS_YEAR} ⭐ разово, "
                    "без автопродления\n")
    if crypto_pay.enabled():
        caption += (f"• <b>Крипта</b> — {cfg.CRYPTO_PRICE_USDT:g} {cfg.CRYPTO_ASSET}, "
                    "оплата разовая\n")
    offer = asset("offer")
    if offer and os.path.exists(offer):
        sent, _ = tg.send_photo(offer, caption, token=TOKEN, chat_id=uid, markup=markup)
        if sent:
            return
    tg.send_message(caption, token=TOKEN, chat_id=uid, markup=markup)


def send_stars(uid: str, days: int = 30) -> None:
    """Карточка выгод + счёт в Stars: подписка (авто) или годовой пакет."""
    year = days > 30
    stars = cfg.SUB_PRICE_STARS_YEAR if year else cfg.SUB_PRICE_STARS
    if year and stars <= 0:
        send_stars(uid, 30)
        return
    period = "365 дней" if year else "30 дней"
    ok, res = tg.create_invoice_link(
        title=f"{cfg.SUB_TITLE} — {period}", description=cfg.SUB_DESCRIPTION,
        payload=f"sub:{uid}:{days}", stars=stars,
        subscription=not year, token=TOKEN)
    if not ok:
        print("createInvoiceLink:", res)
        reply(uid, "Не получилось создать счёт. Попробуй позже.")
        return
    rows = [[{"text": f"Оплатить {stars} ⭐", "url": res}]]
    cb = channel_buttons()
    if cb:
        rows.append(cb)
    markup = {"inline_keyboard": rows}
    renew = ("продлевается автоматически, отменить можно в любой момент "
             "в «Мои звёзды»" if not year else "разовый платёж, без автопродления")
    caption = (
        f"<b>{cfg.SUB_TITLE}</b>\n\n{cfg.SUB_DESCRIPTION}\n\n"
        f"Доступ на <b>{period}</b> — {stars} ⭐, {renew}."
    )
    offer = asset("offer")
    if offer and os.path.exists(offer):
        sent, _ = tg.send_photo(offer, caption, token=TOKEN, chat_id=uid, markup=markup)
        if sent:
            return
    tg.send_message(caption, token=TOKEN, chat_id=uid, markup=markup)


def send_crypto(uid: str, days: int = 30) -> None:
    """Счёт в крипте через Crypto Pay: ссылка на оплату."""
    year = days > 30
    amount = cfg.CRYPTO_PRICE_USDT_YEAR if year else cfg.CRYPTO_PRICE_USDT
    if year and amount <= 0:
        send_crypto(uid, 30)
        return
    period = "365 дней" if year else "30 дней"
    ok, inv = crypto_pay.create_invoice(
        amount, f"{cfg.SUB_TITLE} — {period}", f"sub:{uid}:{days}")
    if not ok or not isinstance(inv, dict):
        print("createInvoice:", inv)
        reply(uid, "Не получилось создать счёт в крипте. Попробуй позже или оплати Stars.")
        return
    url = (inv.get("bot_invoice_url") or inv.get("mini_app_invoice_url")
           or inv.get("web_app_invoice_url"))
    if not url:
        print("createInvoice: нет ссылки на оплату", inv)
        reply(uid, "Не получилось создать счёт в крипте. Попробуй позже.")
        return
    rows = [[{"text": f"💠 Оплатить {amount:g} {cfg.CRYPTO_ASSET}", "url": url}],
            [{"text": "← Другой способ", "callback_data": "buy"}]]
    cb = channel_buttons()
    if cb:
        rows.append(cb)
    tg.send_message(
        f"<b>Оплата криптой</b>\n\n"
        f"Счёт на <b>{amount:g} {cfg.CRYPTO_ASSET}</b> за <b>{period}</b>.\n"
        "Открой счёт кнопкой ниже и оплати — подписка включится сама, "
        "как только платёж подтвердится (обычно 1–3 мин).\n\n"
        "Оплата разовая: продление нужно повторить, бот напомнит заранее.",
        token=TOKEN, chat_id=uid, markup={"inline_keyboard": rows})


def send_status(uid: str, users: dict) -> None:
    ts = paid_until(users, uid)
    if ts <= time.time():
        send_gate(uid, users)
        return
    left = int((ts - time.time()) // 86400) + 1
    text = f"Подписка активна до <b>{_date(ts)}</b> (осталось {left} дн.)."
    rows: list[list[dict]] = []
    link = ref_link(uid)
    if link:
        refs = int((users.get(uid) or {}).get("refs", 0) or 0)
        text += (f"\n\n🎁 <b>Пригласи друга</b>\n"
                 f"За каждого, кто оформит Premium по твоей ссылке: "
                 f"тебе +{cfg.REF_DAYS} дн., другу +{cfg.REF_DAYS_FRIEND} дн. "
                 f"Уже приглашено: {refs}.\n{link}")
        rows.append([{"text": "🎁 Поделиться ссылкой", "url": link}])
    cb = channel_buttons()
    if cb:
        rows.append(cb)
    rows += support_buttons()
    tg.send_message(text, token=TOKEN, chat_id=uid,
                    markup={"inline_keyboard": rows} if rows else None)


# ------------------------------------------------------------------ кнопки и меню

BTN_PREMIUM = "⚡ Premium"
BTN_ACCOUNT = "📊 Мой счёт"
BTN_SETTINGS = "⚙️ Настройки"
BTN_HELP = "ℹ️ Помощь"
BTN_LESSONS = "📚 Методички"

# Готовые варианты для кнопок — чтобы не вводить числа руками
PRESETS = {
    "deposit": [1000.0, 5000.0, 10000.0, 25000.0, 50000.0, 100000.0],
    "risk": [0.25, 0.5, 1.0, 2.0],
    "daily": [3.0, 4.0, 5.0, 10.0],
    "total": [6.0, 8.0, 10.0, 20.0],
}
LABELS = {"deposit": "💰 Депозит", "risk": "🎯 Риск на сделку",
          "daily": "🛑 Дневной стоп", "total": "🧱 Общий стоп"}


def fmt_val(key: str, v: float) -> str:
    return usd(v) if key == "deposit" else f"{v:g}%"


def main_keyboard() -> dict:
    """Нижнее меню — всегда под рукой, без ввода команд."""
    return {
        "keyboard": [[{"text": BTN_PREMIUM}, {"text": BTN_ACCOUNT}],
                     [{"text": BTN_SETTINGS}, {"text": BTN_LESSONS}],
                     [{"text": BTN_HELP}]],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def menu_markup(users: dict, uid: str) -> dict:
    """Inline-кнопки под сообщением: покупка, карточка, настройки, ссылка в группу."""
    rows: list[list[dict]] = []
    if is_paid(users, uid):
        rows.append([{"text": "📊 Карточка счёта", "callback_data": "card"}])
        rows.append([{"text": "⚙️ Настроить план", "callback_data": "set:menu"}])
        rows.append([{"text": f"🔄 Продлить — {cfg.SUB_PRICE_STARS} ⭐", "callback_data": "buy"}])
    else:
        if trial_available(users, uid):
            rows.append([{"text": f"🎁 Пробный доступ — {cfg.TRIAL_DAYS} дн.",
                          "callback_data": "trial"}])
        rows.append([{"text": f"⚡ Оформить подписку — {cfg.SUB_PRICE_STARS} ⭐",
                      "callback_data": "buy"}])
    rows.append([{"text": "📈 Статистика", "callback_data": "stats"},
                 {"text": "❓ FAQ", "callback_data": "faq"}])
    rows.append([{"text": "📚 Методички", "callback_data": "edu:list"}])
    cb = channel_buttons()
    if cb:
        rows.append(cb)
    rows += support_buttons()
    return {"inline_keyboard": rows}


def signal_markup(users: dict, uid: str) -> dict:
    """Кнопки под сигналом: быстрые действия подписчика."""
    return {"inline_keyboard": [
        [{"text": "📊 Мой счёт", "callback_data": "card"},
         {"text": "⚙️ Настройки", "callback_data": "set:menu"}],
    ]}


def welcome_text(users: dict, uid: str, name: str = "") -> str:
    hi = f"<b>{name}</b>, привет." if name else "<b>Привет.</b>"
    body = (
        f"{hi}\n\n"
        "Это <b>KidzordFX Premium</b> — быстрые сигналы по золоту, форексу и "
        "индексам и расчёт риска под твой счёт.\n\n"
        "• сигнал приходит сразу: вход, стоп и три цели\n"
        "• объём и дневной лимит считаются под твой депозит\n"
        "• бесплатные методички: /lesson или «📚 Методички» внизу\n\n"
    )
    if is_paid(users, uid):
        body += (f"✅ Подписка активна до <b>{_date(paid_until(users, uid))}</b>.\n"
                 "Открой «📊 Мой счёт» внизу.")
    elif trial_available(users, uid):
        body += (f"🎁 Пробный доступ — {cfg.TRIAL_DAYS} дн. бесплатно: кнопка под сообщением.\n"
                 f"Дальше — {cfg.SUB_PRICE_STARS} ⭐ в месяц, отменить можно в любой момент.")
    else:
        body += (f"🔒 Подписка — <b>{cfg.SUB_PRICE_STARS} ⭐ в месяц</b>, "
                 "продление автоматическое, отменить можно в любой момент.\n"
                 "Жми «⚡ Premium» внизу.")
    return body


def send_welcome(users: dict, uid: str, name: str = "") -> None:
    """Бренд-карточка + нижнее меню."""
    text = welcome_text(users, uid, name)
    kb = main_keyboard()
    hero = asset("hero")
    if hero and os.path.exists(hero):
        ok, _ = tg.send_photo(hero, text, token=TOKEN, chat_id=uid, markup=kb)
        if ok:
            return
    tg.send_message(text, token=TOKEN, chat_id=uid, markup=kb)


def send_premium(uid: str, users: dict) -> None:
    """Статус подписки: активна — кнопка продления, нет — счёт на оплату."""
    if not is_paid(users, uid):
        send_buy(uid, users)
        return
    ts = paid_until(users, uid)
    left = int((ts - time.time()) // 86400) + 1
    rows = [[{"text": f"🔄 Продлить на 30 дней — {cfg.SUB_PRICE_STARS} ⭐",
              "callback_data": "buy"}]]
    cb = channel_buttons()
    if cb:
        rows.append(cb)
    tg.send_message(
        f"✅ <b>Подписка активна</b>\n\n"
        f"Доступ до <b>{_date(ts)}</b> — осталось {left} дн.\n"
        f"Продление автоматическое, отменить можно в «Мои звёзды».",
        token=TOKEN, chat_id=uid, markup={"inline_keyboard": rows})


def send_gate(uid: str, users: dict | None = None) -> None:
    """Отказ без подписки — бренд-карточка и кнопка оплаты."""
    text = not_paid_text()
    markup = menu_markup(users or {}, uid)
    gate = asset("gate")
    if gate and os.path.exists(gate):
        sent, _ = tg.send_photo(gate, text, token=TOKEN, chat_id=uid, markup=markup)
        if sent:
            return
    tg.send_message(text, token=TOKEN, chat_id=uid, markup=markup)


def settings_text(users: dict, uid: str) -> str:
    p = user_plan(users, uid)
    risk_money = float(p["deposit"]) * float(p["risk"]) / 100.0
    u = users.get(uid) or {}
    syms = u.get("symbols") or []
    if isinstance(syms, str):
        syms = [x.strip().upper() for x in syms.split(",") if x.strip()]
    maxday = int(u.get("maxday", 0) or 0)
    filt = f"{', '.join(syms) if syms else 'все'} · лимит {maxday or 'нет'}"
    return (
        "<b>Настрой свой план</b>\n\n"
        f"Депозит <b>{usd(float(p['deposit']))}</b>\n"
        f"Риск на сделку <b>{float(p['risk']):.2f}%</b> = <b>{usd(risk_money)}</b>\n"
        f"Дневной стоп <b>{float(p['daily']):.1f}%</b> · "
        f"общий стоп <b>{float(p['total']):.1f}%</b>\n"
        f"Фильтры: <b>{filt}</b>\n\n"
        "Нажми на строку, чтобы изменить."
    )


def filters_text(users: dict, uid: str) -> str:
    u = users.get(uid) or {}
    syms = u.get("symbols") or []
    if isinstance(syms, str):
        syms = [x.strip().upper() for x in syms.split(",") if x.strip()]
    maxday = int(u.get("maxday", 0) or 0)
    return (
        "<b>Фильтры сигналов</b>\n\n"
        f"Инструменты: <b>{', '.join(syms) if syms else 'все'}</b>\n"
        f"Максимум входов за 24 ч: <b>{maxday or 'без лимита'}</b>\n\n"
        "Фильтр применяется к новым сигналам. Открытые сделки ведутся до конца, "
        "результаты по ним придут как обычно."
    )


def filters_markup(users: dict, uid: str) -> dict:
    return {"inline_keyboard": [
        [{"text": "Все инструменты", "callback_data": "flt:sym:all"}],
        [{"text": "Только золото (XAUUSD)", "callback_data": "flt:sym:xau"}],
        [{"text": "Золото + форекс", "callback_data": "flt:sym:fx"}],
        [{"text": "Лимит: нет", "callback_data": "flt:max:0"},
         {"text": "1 в сутки", "callback_data": "flt:max:1"}],
        [{"text": "3 в сутки", "callback_data": "flt:max:3"},
         {"text": "5 в сутки", "callback_data": "flt:max:5"}],
        [{"text": "← Назад", "callback_data": "set:menu"}],
    ]}


def settings_markup(users: dict, uid: str) -> dict:
    p = user_plan(users, uid)
    risk_money = float(p["deposit"]) * float(p["risk"]) / 100.0
    u = users.get(uid) or {}
    syms = u.get("symbols") or []
    if isinstance(syms, str):
        syms = [x.strip().upper() for x in syms.split(",") if x.strip()]
    maxday = int(u.get("maxday", 0) or 0)
    filt = f"{len(syms) if syms else 'все'} · {maxday or '∞'}"
    return {"inline_keyboard": [
        [{"text": f"💰 Депозит: {usd(float(p['deposit']))}", "callback_data": "set:deposit"}],
        [{"text": f"🎯 Риск: {float(p['risk']):.2f}% (={usd(risk_money)})",
          "callback_data": "set:risk"}],
        [{"text": f"🛑 День: {float(p['daily']):.1f}%", "callback_data": "set:daily"}],
        [{"text": f"🧱 Всего: {float(p['total']):.1f}%", "callback_data": "set:total"}],
        [{"text": f"🔔 Фильтры: {filt}", "callback_data": "set:filters"}],
        [{"text": "📊 Карточка счёта", "callback_data": "card"}],
    ]}


def send_settings(uid: str, users: dict) -> None:
    tg.send_message(settings_text(users, uid), token=TOKEN, chat_id=uid,
                    markup=settings_markup(users, uid))


def presets_markup(key: str) -> dict:
    rows: list[list[dict]] = []
    row: list[dict] = []
    for v in PRESETS[key]:
        row.append({"text": fmt_val(key, v), "callback_data": f"val:{key}:{v:g}"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "← Назад", "callback_data": "set:menu"}])
    return {"inline_keyboard": rows}


def _edit_or_send(chat_id: str, mid: int, text: str, markup: dict, uid: str) -> None:
    """Переписать сообщение. Под фото (например, под сигналом) текста нет,
    поэтому edit не сработает — тогда отправляем новое сообщение."""
    ok, _ = tg.edit_text(chat_id, mid, text, markup=markup, token=TOKEN)
    if not ok:
        tg.send_message(text, token=TOKEN, chat_id=uid, markup=markup)


def handle_callback(users: dict, uid: str, chat_id: str, mid: int, data: str) -> dict | None:
    """Нажатия на inline-кнопки. Возвращает план, если он менялся."""

    # Методички — бесплатно, поэтому до проверки подписки.
    if data == "edu:list":
        edu_posts.send_index(uid, TOKEN)
        return None
    if data.startswith("edu:lesson:"):
        parts = data.split(":")
        if len(parts) == 3 and parts[2].isdigit():
            u = users.setdefault(uid, {})
            n = int(parts[2])
            if edu_posts.send_to_user(uid, TOKEN, n):
                u["lesson_next"] = edu_posts.next_after(n)
                return u
        return None
    if data.startswith("edu:next:"):
        u = users.setdefault(uid, {})
        n = int(u.get("lesson_next", 1) or 1)
        if not 1 <= n <= edu_posts.total():
            n = 1
        if edu_posts.send_to_user(uid, TOKEN, n):
            u["lesson_next"] = edu_posts.next_after(n)
            return u
        return None

    # Редактор Threads — только владельцу.
    if data.startswith("post:") and str(uid) == str(cfg.OWNER_CHAT_ID):
        kind = data.split(":", 1)[1]
        if kind == "next":
            threads_editor.send_next(uid, TOKEN)
        elif kind == "signal":
            threads_editor.send_signal(uid, TOKEN)
        elif kind == "week":
            threads_editor.send_week(uid, TOKEN)
        elif kind == "result":
            threads_editor.send_result(uid, TOKEN)
        return None

    # Очередь Discord: владелец подтверждает сторону одной кнопкой.
    if data.startswith("drev:") and str(uid) == str(cfg.OWNER_CHAT_ID):
        parts = data.split(":")
        if len(parts) < 3:
            return None
        discord_mid, action = parts[1], parts[2].upper()
        if action == "SKIP":
            res = discord_intake.resolve_review(discord_mid, None)
            note = "⏭ пропущено"
        else:
            res = discord_intake.resolve_review(discord_mid, action)
            if not res.get("ok"):
                note = "⚠️ " + str(res.get("error", "не получилось"))
            elif res.get("stored"):
                note = f"✅ записано как голос: {res.get('symbol', '')} {action}"
            else:
                note = f"♻️ {res.get('symbol', '')} {action} уже был — дубликат"
        row = res.get("row") or {}
        caption = discord_intake.review_caption(row, note) if row else note
        # кнопки убираем только когда элемент закрыт; при ошибке пусть можно
        # нажать другую сторону, не теряя разметку
        markup = {"inline_keyboard": []} if res.get("ok") else None
        ok_edit, _ = tg.edit_caption(chat_id, mid, caption, markup=markup, token=TOKEN)
        if not ok_edit:
            ok_edit, _ = tg.edit_text(chat_id, mid, caption, markup=markup, token=TOKEN)
        if not ok_edit:
            reply(uid, note)
        return None

    # Превью/публикация Telegram-поста — только владельцу.
    if data.startswith("tgpost:") and str(uid) == str(cfg.OWNER_CHAT_ID):
        parts = data.split(":")
        if len(parts) >= 2 and parts[1] == "next":
            telegram_editor.send_next(uid, TOKEN)
        elif len(parts) >= 3 and parts[1] == "pub" and parts[2].isdigit():
            telegram_editor.publish(int(parts[2]), uid, TOKEN)
        return None

    if data == "buy":
        send_buy(uid, users)
        return None

    if data == "pay:stars":
        send_stars(uid)
        return None

    if data == "pay:stars:year":
        send_stars(uid, 365)
        return None

    if data == "pay:crypto":
        send_crypto(uid)
        return None

    if data == "pay:crypto:year":
        send_crypto(uid, 365)
        return None

    if data == "card":
        if is_paid(users, uid):
            send_card(uid, users)
        else:
            _edit_or_send(chat_id, mid, not_paid_text(),
                          menu_markup(users, uid), uid)
        return None

    if data == "stats":
        send_stats(uid, users)
        return None

    if data == "faq":
        _edit_or_send(chat_id, mid, FAQ, menu_markup(users, uid), uid)
        return None

    if data == "trial":
        if trial_available(users, uid):
            grant_trial(users, uid)
        else:
            _edit_or_send(chat_id, mid,
                          "Пробный доступ уже использован. Оформить подписку — /buy.",
                          menu_markup(users, uid), uid)
        return None

    # настройки — только по активной подписке
    if not is_paid(users, uid):
        _edit_or_send(chat_id, mid, not_paid_text(), menu_markup(users, uid), uid)
        return None

    if data == "set:menu":
        _edit_or_send(chat_id, mid, settings_text(users, uid),
                      settings_markup(users, uid), uid)
        return None

    if data == "set:filters":
        _edit_or_send(chat_id, mid, filters_text(users, uid),
                      filters_markup(users, uid), uid)
        return None

    if data.startswith("flt:sym:"):
        val = data.split(":", 2)[2]
        mapping = {
            "all": [],
            "xau": ["XAUUSD"],
            "fx": ["XAUUSD", "AUDUSD", "USDCAD", "GBPUSD"],
        }
        if val in mapping:
            users.setdefault(uid, {})["symbols"] = mapping[val]
            save_json(USERS_PATH, users)
            _edit_or_send(chat_id, mid, filters_text(users, uid),
                          filters_markup(users, uid), uid)
            return users.get(uid) or {}
        return None

    if data.startswith("flt:max:"):
        raw = data.split(":", 2)[2]
        if raw.isdigit():
            users.setdefault(uid, {})["maxday"] = int(raw)
            save_json(USERS_PATH, users)
            _edit_or_send(chat_id, mid, filters_text(users, uid),
                          filters_markup(users, uid), uid)
            return users.get(uid) or {}
        return None

    if data.startswith("set:"):
        key = data.split(":", 1)[1]
        if key in PRESETS:
            p = user_plan(users, uid)
            _edit_or_send(
                chat_id, mid,
                f"<b>{LABELS[key]}</b>\n\nСейчас: <b>{fmt_val(key, float(p[key]))}</b>\n\n"
                "Выбери значение кнопкой ниже или впиши своё командой.",
                presets_markup(key), uid)
        return None

    if data.startswith("val:"):
        _, key, raw = data.split(":", 2)
        if key not in PRESETS:
            return None
        plan = user_plan(users, uid)
        plan[key] = float(raw)
        users[uid] = plan
        save_json(USERS_PATH, users)
        note = ""
        if plan["total"] <= plan["daily"]:
            note = "\n\n⚠️ Общий стоп должен быть больше дневного — поправь «🧱 Всего»."
        _edit_or_send(chat_id, mid, settings_text(users, uid) + note,
                      settings_markup(users, uid), uid)
        return plan
    return None


def handle(users: dict, uid: str, raw: str, name: str = "") -> dict | None:
    """Разбор сообщения: кнопки нижнего меню или текстовая команда."""
    text = raw.strip()

    # --- нижнее меню ---
    if text == BTN_PREMIUM:
        send_premium(uid, users)
        return None
    if text == BTN_ACCOUNT:
        if is_paid(users, uid):
            send_card(uid, users, name)
        else:
            send_gate(uid, users)
        return None
    if text == BTN_SETTINGS:
        if is_paid(users, uid):
            send_settings(uid, users)
        else:
            send_gate(uid, users)
        return None
    if text == BTN_HELP:
        reply(uid, HELP)
        return None
    if text == BTN_LESSONS:
        edu_posts.send_index(uid, TOKEN)
        return None

    parts = text.split()
    cmd = parts[0].lstrip("/").split("@")[0].lower()
    args = parts[1:]
    plan = user_plan(users, uid)

    if cmd == "id":
        reply(uid, f"Твой Telegram ID: <code>{uid}</code>")
        return None

    if cmd == "threads":
        cur = str(load_json(OWNER_PATH).get("chat_id", "") or "")
        if cur and cur != uid:
            reply(uid, "Напоминания о постах для Threads уже привязаны к другому чату.")
            return None
        save_json(OWNER_PATH, {"chat_id": uid})
        reply(uid, "Готово. Сюда будут приходить посты для Threads — "
                   "по расписанию, чтобы ты не забывал публиковать.\n\n"
                   "В любой момент: /post — следующий пост, /post signal — "
                   "последний сигнал, /post week — итог недели, /post result — "
                   "последняя закрытая сделка.\n"
                   "Telegram: /tgpost — сразу опубликовать следующий пост, "
                   "/tgpost preview — только превью.\n"
                   "TikTok: /tt — карусель из карточек и текст для публикации.")
        return None

    if cmd in ("start", "menu"):
        if args:
            bind_referrer(users, uid, args[0])
            if args[0].startswith("src_"):
                src = args[0][4:].strip()[:32]
                if src:
                    u = users.setdefault(uid, {})
                    if not u.get("source"):
                        u["source"] = src
                        save_json(USERS_PATH, users)
        send_welcome(users, uid, name)
        return None

    if cmd == "help":
        reply(uid, HELP)
        return None

    if cmd in ("stats", "stat", "statistics"):
        send_stats(uid, users)
        return None

    if cmd == "faq":
        reply(uid, FAQ)
        return None

    if cmd == "trial":
        if trial_available(users, uid):
            grant_trial(users, uid)
        else:
            reply(uid, "Пробный доступ уже использован. Оформить подписку — /buy.")
        return None

    if cmd in ("promo", "promocode"):
        if not args or not cfg.PROMO_CODES:
            reply(uid, "Промокоды сейчас не активны.")
            return None
        code = args[0].strip().upper()
        days = cfg.PROMO_CODES.get(code)
        if not days:
            reply(uid, "Такого промокода нет. Проверь написание.")
            return None
        u = users.setdefault(uid, {})
        used = [str(x) for x in (u.get("promo_used") or [])]
        if code in used:
            reply(uid, "Этот промокод уже применён.")
            return None
        new = store.record_payment(provider="promo", external_id=f"{code}:{uid}",
                                   uid=uid, amount=0.0, days=days,
                                   raw={"code": code})
        if not new:
            reply(uid, "Этот промокод уже применён.")
            return None
        until = max(paid_until(users, uid), time.time()) + days * 86400
        u["paid_until"] = until
        u["promo_used"] = sorted(set(used) | {code})
        save_json(USERS_PATH, users)
        store.cancel_nurture(uid)
        reply(uid, f"🎁 Промокод применён: <b>+{days} дн.</b>\n"
                   f"Доступ активен до <b>{_date(until)}</b>.")
        return None

    if cmd in ("buy", "subscribe"):
        send_buy(uid, users)
        return None

    if cmd == "premium":
        send_premium(uid, users)
        return None

    if cmd == "status":
        send_status(uid, users)
        return None

    # методички бесплатны для всех — это вход в продукт, а не premium-фича
    if cmd in ("lesson", "methodics", "method"):
        u = users.setdefault(uid, {})
        if args and args[0].isdigit():
            n = int(args[0])
        else:
            n = int(u.get("lesson_next", 1) or 1)
        if not 1 <= n <= edu_posts.total():
            reply(uid, f"Нет методички #{n}. Всего {edu_posts.total()} — /lessons.")
            return None
        if edu_posts.send_to_user(uid, TOKEN, n):
            u["lesson_next"] = edu_posts.next_after(n)
            return u
        return None

    if cmd in ("lessons", "methods"):
        edu_posts.send_index(uid, TOKEN)
        return None

    # owner-only: деньги, бэкап и редактор Threads
    if str(uid) == str(cfg.OWNER_CHAT_ID):
        if cmd in ("ext", "consensus"):
            # ручной ввод без пересылки: /ext @канал XAUUSD BUY 4020 SL 4005
            if len(parts) < 3:
                reply(uid, "Формат: /ext @канал XAUUSD BUY 4020 SL 4005\n"
                           "Или просто перешли пост из канала.")
                return None
            source = parts[1]
            body = text.split(None, 2)[2]
            _intake_save(uid, source, body)
            return None
        if cmd in ("revenue", "money"):
            reply(uid, revenue.text_report(30))
            return None
        if cmd in ("guard", "killswitch"):
            arg = args[0].lower() if args else "status"
            if arg in ("resume", "off", "clear"):
                key = risk_guard.resume()
                reply(uid, f"🟢 Kill-switch снят до конца недели {key}. "
                           "Новые сигналы снова публикуются.")
            else:
                reply(uid, risk_guard.status_text())
            return None
        if cmd == "refund":
            if not args:
                reply(uid, "Формат: /refund <id> — возврат Stars по номеру платежа "
                           "(номера видны в /revenue). Крипта возвращается вручную "
                           "через @CryptoBot.")
                return None
            _refund_stars(uid, args[0])
            return None
        if cmd == "backup":
            try:
                path = backup.snapshot()
                reply(uid, f"💾 Бэкап готов: <code>{path}</code>")
            except Exception as e:
                reply(uid, f"Бэкап не удался: {e}")
            return None
        if cmd == "post":
            arg = args[0].lower() if args else "next"
            if arg in ("mix", "today"):
                threads_editor.send_mix(uid, TOKEN)
            elif arg == "signal":
                threads_editor.send_signal(uid, TOKEN)
            elif arg in ("week", "stats"):
                threads_editor.send_week(uid, TOKEN)
            elif arg in ("result", "trade"):
                threads_editor.send_result(uid, TOKEN)
            elif arg.isdigit():
                threads_editor.send_next(uid, TOKEN, int(arg))
            else:
                threads_editor.send_next(uid, TOKEN)
            return None
        if cmd == "tgpost":
            arg = args[0].lower() if args else "publish"
            if arg in ("preview", "show"):
                if len(args) > 1 and args[1].isdigit():
                    telegram_editor.send_next(uid, TOKEN, int(args[1]))
                else:
                    telegram_editor.send_next(uid, TOKEN)
            elif arg in ("now", "publish"):
                telegram_editor.publish_next(uid, TOKEN)
            elif arg.isdigit():
                telegram_editor.publish(int(arg), uid, TOKEN)
            else:
                telegram_editor.publish_next(uid, TOKEN)
            return None
        if cmd in ("tt", "tiktok"):
            variant = args[0].lower() if args else "story"
            if variant not in ("story", "shipped"):
                variant = "story"
            tiktok_editor.send(uid, TOKEN, variant=variant)
            return None

    # фидбек доступен всем — это канал связи с автором для тестеров
    if cmd == "feedback":
        _send_feedback(users, uid, raw)
        return None

    # остальное — только по активной подписке
    if not is_paid(users, uid):
        send_gate(uid, users)
        return None

    if cmd in ("mycard", "card", "account"):
        send_card(uid, users, name)
        return None

    if cmd in ("settings", "plan"):
        send_settings(uid, users)
        return None

    if cmd == "deposit":
        if not args:
            reply(uid, f"Депозит: <b>{usd(float(plan['deposit']))}</b>\n"
                       "Изменить: /deposit 10000")
            return None
        v = _num(args[0], DEP_MIN, DEP_MAX)
        if v is None:
            reply(uid, f"Нужно число от {DEP_MIN:.0f} до {DEP_MAX:.0f}. Пример: /deposit 10000")
            return None
        plan["deposit"] = v
        reply(uid, f"Депозит сохранён: <b>{usd(v)}</b>")
        return plan

    if cmd in ("risk", "daily", "total"):
        titles = {"risk": "Риск на сделку", "daily": "Дневной стоп", "total": "Общий стоп"}
        bounds = {"risk": (RISK_MIN, RISK_MAX), "daily": (DAILY_MIN, DAILY_MAX),
                  "total": (TOTAL_MIN, TOTAL_MAX)}
        lo, hi = bounds[cmd]
        if not args:
            reply(uid, f"{titles[cmd]}: <b>{float(plan[cmd]):.2f}%</b>\n"
                       f"Изменить: /{cmd} {plan[cmd]}")
            return None
        v = _num(args[0], lo, hi)
        if v is None:
            reply(uid, f"Нужно число от {lo} до {hi}. Пример: /{cmd} {plan[cmd]}")
            return None
        plan[cmd] = v
        if plan["total"] <= plan["daily"]:
            reply(uid, f"{titles[cmd]} сохранён: <b>{v:.2f}%</b>\n"
                       "Но общий стоп должен быть больше дневного — поправь /total")
        else:
            reply(uid, f"{titles[cmd]} сохранён: <b>{v:.2f}%</b>")
        return plan

    if cmd == "limits":
        if len(args) < 3:
            reply(uid, "Формат: /limits 0.5 5 10  (риск, дневной, общий — в % от депозита)")
            return None
        r = _num(args[0], RISK_MIN, RISK_MAX)
        d = _num(args[1], DAILY_MIN, DAILY_MAX)
        t = _num(args[2], TOTAL_MIN, TOTAL_MAX)
        if r is None or d is None or t is None:
            reply(uid, "Не понял значения. Диапазоны: риск 0.05–5, день 0.5–50, всего 1–80.")
            return None
        if t <= d:
            reply(uid, "Общий стоп должен быть больше дневного. Пример: /limits 0.5 5 10")
            return None
        plan.update({"risk": r, "daily": d, "total": t})
        reply(uid, f"План сохранён: риск {r:.2f}% · день {d:.1f}% · всего {t:.1f}%")
        return plan

    if cmd in ("symbols", "symbol"):
        u = users.setdefault(uid, {})
        raw = " ".join(args).strip().upper().replace(",", " ")
        if not raw or raw in ("ALL", "ВСЕ", "ЛЮБЫЕ"):
            u["symbols"] = []
            save_json(USERS_PATH, users)
            reply(uid, "Фильтр снят: приходят сигналы по всем инструментам.")
            return None
        syms = [s for s in raw.split() if s]
        u["symbols"] = syms[:20]
        save_json(USERS_PATH, users)
        reply(uid, "Оставляю только: <b>" + ", ".join(u["symbols"]) + "</b>\n"
                   "Снять фильтр — /symbols all")
        return None

    if cmd in ("maxday", "maxsignals"):
        u = users.setdefault(uid, {})
        if not args:
            cur = int(u.get("maxday", 0) or 0)
            reply(uid, f"Лимит входов за 24 ч: <b>{cur or 'без лимита'}</b>\n"
                       "Изменить: /maxday 3, выключить: /maxday 0")
            return None
        try:
            n = max(0, min(50, int(float(args[0].replace(",", ".")))))
        except ValueError:
            reply(uid, "Нужно целое число от 0 до 50. Пример: /maxday 3")
            return None
        u["maxday"] = n
        save_json(USERS_PATH, users)
        reply(uid, f"Лимит входов за 24 ч: <b>{n or 'без лимита'}</b>")
        return None

    if str(uid) == str(cfg.OWNER_CHAT_ID):
        reply(uid, "Не понял. Владельцу: перешли пост из канала текстом или "
                   "скриншотом — разберу как сигнал или разбор (для разбора "
                   "добавь в подпись «разбор» или #trade). Остальное — /help.")
    else:
        reply(uid, "Не понял. Пользуйся кнопками внизу или глянь /help.")
    return None


# ------------------------------------------------------------------ доставка сигналов

def _read_queue(path: str, last_ts: float) -> list[dict]:
    """Строки jsonl новее last_ts. Битые строки пропускаем."""
    rows: list[dict] = []
    if not os.path.exists(path):
        return rows
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts = float(rec.get("ts", 0) or 0)
                except (ValueError, TypeError):
                    continue
                # >=, а не >: два процесса могут записать в одну и ту же
                # секунду, и запись с ts == last_ts иначе теряется навсегда.
                if ts >= last_ts:
                    rows.append(rec)
    except OSError as e:
        print("очередь недоступна:", e)
    return sorted(rows, key=lambda r: float(r.get("ts", 0) or 0))


def poll_signals(users: dict, state: dict) -> None:
    """Разложить новые сигналы bot.py по outbox каждого подписчика."""
    fresh = _read_queue(QUEUE_PATH, float(state.get("last_signal_ts", 0) or 0))
    if not fresh:
        return
    paid = [uid for uid in users
            if is_paid(users, uid) and not (users.get(uid) or {}).get("blocked")]
    added = 0
    for rec in fresh:
        tid = str(rec.get("trade_id") or
                  f"{rec.get('symbol', '')}_{int(float(rec.get('ts', 0) or 0))}")
        caption = str(rec.get("caption", ""))
        path = str(rec.get("path", "") or "")
        for uid in paid:
            line = size_line(users, uid, rec)
            personal = caption + ("\n\n" + line if line else "")
            if store.enqueue_delivery(
                uid=uid, trade_id=tid, kind="entry", chat_id=uid,
                caption=personal, symbol=str(rec.get("symbol", "")),
                side=str(rec.get("side", "")), image_path=path or None,
            ):
                added += 1
    state["last_signal_ts"] = max(float(r.get("ts", 0) or 0) for r in fresh)
    save_json(STATE_PATH, state)
    print(f"outbox: +{added} входов · подписчиков {len(paid)}")


def poll_results(users: dict, state: dict) -> None:
    """Результаты сделок → обновление подписи карточки у тех, кто её получил."""
    fresh = _read_queue(RESULTS_PATH, float(state.get("last_result_ts", 0) or 0))
    if not fresh:
        return
    added = 0
    processed: list[float] = []
    for rec in fresh:
        tid = str(rec.get("trade_id") or "")
        rec_ts = float(rec.get("ts", 0) or 0)
        if not tid:
            processed.append(rec_ts)
            continue
        targets = store.entry_targets(tid)
        if not targets:
            # вход мог ещё не дойти до poll_signals; не теряем результат.
            if time.time() - rec_ts < 3600:
                break
            processed.append(rec_ts)   # старый результат без входов — пропускаем
            continue
        status = result_status(rec)
        event = str(rec.get("event", "result")).lower()
        for uid in targets:
            if store.enqueue_delivery(
                uid=uid, trade_id=tid, kind=event, chat_id=uid, caption=status,
                symbol=str(rec.get("symbol", "")), side=str(rec.get("side", "")),
            ):
                added += 1
        processed.append(rec_ts)
    if processed:
        state["last_result_ts"] = max(float(state.get("last_result_ts", 0) or 0),
                                      max(processed))
    save_json(STATE_PATH, state)
    print(f"outbox: +{added} результатов")


def remind_expiring(users: dict) -> None:
    """Напомнить о продлении за REMIND_BEFORE_SEC до конца — один раз на период."""
    now = time.time()
    changed = False
    for uid in list(users):
        until = paid_until(users, uid)
        if until <= now or until - now > REMIND_BEFORE_SEC:
            continue
        # Триал ведёт nurture-цепочка (день 1 и день 3), а не общий reminder:
        # иначе сообщение об окончании приходит сразу после выдачи триала.
        if users[uid].get("trial_used") and not store.has_paid_subscription(uid):
            continue
        if users[uid].get("reminded_for") == until:
            continue
        res = tg.send_message_result(
            remind_text(until), token=TOKEN, chat_id=uid,
            markup={"inline_keyboard": [[
                {"text": f"🔄 Продлить — {cfg.SUB_PRICE_STARS} ⭐",
                 "callback_data": "buy"}]]})
        if res.ok:
            users[uid]["reminded_for"] = until
            changed = True
        elif res.blocked:
            users[uid]["blocked"] = 1
            changed = True
        # 429/сеть: напомним в следующий проход, reminded_for не ставим
    if changed:
        save_json(USERS_PATH, users)


def migrate_crypto_seen() -> None:
    """Перенести старый crypto_paid.json в ledger, чтобы не выдать счета повторно."""
    seen = load_json(CRYPTO_STATE_PATH)
    if not isinstance(seen, dict) or not seen:
        return
    n = 0
    for iid, rec in seen.items():
        rec = rec if isinstance(rec, dict) else {}
        uid = str(rec.get("uid", "") or "")
        if not uid:
            continue
        if store.record_payment(provider="crypto", external_id=str(iid), uid=uid,
                                amount=0.0, days=30, raw={"migrated": True, **rec}):
            # старые счета уже были выданы через crypto_paid.json — не выдаём повторно
            store.mark_payment_granted("crypto", str(iid), time.time())
            n += 1
    print(f"crypto: старых счетов перенесено в ledger: {n}")


def poll_crypto(users: dict) -> bool:
    """Оплаченные счета Crypto Pay → подписка автору счёта.

    Идемпотентность по invoice_id в payments: один счёт не продлит доступ
    дважды, даже если getInvoices вернёт его несколько раз.
    """
    if not crypto_pay.enabled():
        return True
    ok, items = crypto_pay.paid_invoices()
    if not ok:
        print("crypto getInvoices:", items)
        return False
    got = 0
    for inv in items:
        iid = str(inv.get("invoice_id") or "")
        payload = str(inv.get("payload") or "")
        if not iid or not payload.startswith("sub:"):
            continue
        parts = payload.split(":")
        uid = parts[1].strip() if len(parts) > 1 else ""
        if not uid:
            continue
        try:
            days = int(parts[2]) if len(parts) > 2 and parts[2].strip() else 30
        except ValueError:
            days = 30
        days = max(1, min(days, 3650))
        new = apply_payment(
            users, provider="crypto", external_id=iid, uid=uid,
            amount=float(inv.get("amount", 0) or 0),
            currency=str(inv.get("asset", "") or ""), days=days,
            raw=inv,
        )
        if not new:
            continue
        until = paid_until(users, uid)
        got += 1
        print(f"крипта: счёт {iid} · {uid} · +{days} дн. до {_date(until)}")
        tg.send_message(onboard_text(until), token=TOKEN, chat_id=uid,
                        markup=menu_markup(users, uid))
        reward_referrer(users, uid)
    if got:
        print(f"крипта: выдано подписок {got}")
    return True


# ------------------------------------------------------------------ запуск

def forward_source(msg: dict) -> str:
    """Источник пересланного поста. Пусто — источник скрыт или это не пересылка."""
    origin = msg.get("forward_origin") or {}
    kind = origin.get("type")
    if kind == "channel":
        return _chan_label(origin.get("chat") or {})
    if kind == "chat":
        return _chan_label(origin.get("sender_chat") or {})
    if kind == "user":
        user = origin.get("sender_user") or {}
        if user.get("username"):
            return "@" + user["username"]
        return f"user:{user.get('id')}" if user.get("id") else ""
    if kind == "hidden_user":
        return ""
    # старый формат Bot API (до 7.0)
    if msg.get("forward_from_chat"):
        return _chan_label(msg["forward_from_chat"])
    user = msg.get("forward_from") or {}
    if user.get("username"):
        return "@" + user["username"]
    if user.get("id"):
        return f"user:{user['id']}"
    return ""


def _chan_label(chat: dict) -> str:
    if chat.get("username"):
        return "@" + chat["username"]
    return str(chat.get("title") or "")


def _is_forward(msg: dict) -> bool:
    return bool(msg.get("forward_origin") or msg.get("forward_from_chat")
                or msg.get("forward_from") or msg.get("forward_sender_name")
                or msg.get("forward_date"))


def _intake_save(uid: str, source: str, text: str) -> bool:
    try:
        row = consensus.add_payload({"source": source, "text": text})
    except Exception as e:                  # noqa: BLE001 — чат не должен падать
        print(f"intake: ошибка сохранения ({source}): {e!r}")
        reply(uid, f"⚠️ Не сохранил: {e}")
        return False
    if row is None:
        print(f"intake: не распознан или дубликат ({source}): {text[:120]!r}")
        reply(uid, f"⚠️ {source}: сигнал не распознан (нужны тикер и сторона — "
                   "buy/sell, лонг/шорт) или это дубликат.")
        return False
    print(f"intake: записан {row['symbol']} {row['side']} · {source}")
    reply(uid, f"✅ {row['symbol']} {row['side']} · {source} — записано. "
               "Учитывается как голос консенсуса.")
    return True


def _has_media(msg: dict) -> bool:
    if msg.get("photo"):
        return True
    doc = msg.get("document") or {}
    return str(doc.get("mime_type", "")).startswith("image/")


def _image_file_id(msg: dict) -> str:
    """Файл вместо сжатого фото: скриншот с текстом так читается лучше."""
    doc = msg.get("document") or {}
    if str(doc.get("mime_type", "")).startswith("image/"):
        return str(doc.get("file_id", ""))
    photos = msg.get("photo") or []
    return str(photos[-1].get("file_id", "")) if photos else ""


def _caption_source(msg: dict) -> str:
    """Источник из подписи к скриншоту: «#trades» или «@channel»."""
    caption = str(msg.get("caption") or "")
    m = re.search(r"([@#][A-Za-zА-Яа-яЁё0-9_]{2,})", caption)
    return m.group(1) if m else ""


def download_telegram_image(msg: dict, name: str) -> str:
    """Скачивает фото/файл из сообщения в tg_screenshots/. Пусто — не вышло."""
    file_id = _image_file_id(msg)
    if not file_id:
        return ""
    res = tg.api_call("getFile", {"file_id": file_id}, token=TOKEN)
    path = str((res.result or {}).get("file_path", "")) if res.ok else ""
    if not path:
        return ""
    folder = os.path.join(HERE, "tg_screenshots")
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, name + os.path.splitext(path)[1])
    try:
        url = f"https://api.telegram.org/file/bot{TOKEN}/{path}"
        with requests.get(url, timeout=20) as resp:
            if resp.status_code != 200:
                return ""
            with open(target, "wb") as f:
                f.write(resp.content)
        return target
    except Exception:                       # noqa: BLE001
        return ""


# «#trades» (канал сделок) не должен ловиться как «#trade»: нужен \b
BREAKDOWN_RE = re.compile(
    r"(разбор\w*|#trade\b|#сделка\w*|#итог\w*|итог сделки)", re.IGNORECASE)


def _is_breakdown(msg: dict) -> bool:
    return bool(BREAKDOWN_RE.search(str(msg.get("caption") or "")))


def intake_media(msg: dict, uid: str) -> bool:
    """Скриншот владельца (пересланный или свой) → OCR → голос или очередь.

    Если в подписи есть «разбор» / #trade / #сделка — это закрытая сделка:
    она идёт в архив разборов, а не в голоса консенсуса.
    """
    if not cfg.OWNER_CHAT_ID or str(uid) != str(cfg.OWNER_CHAT_ID):
        return False
    if not _has_media(msg):
        return False
    source = forward_source(msg) or _caption_source(msg) or "tg:скриншот"
    key = f"tg_{msg.get('message_id', '')}"
    path = download_telegram_image(msg, key)
    if not path:
        reply(uid, "Не смог скачать картинку — пришли её файлом (не сжатым фото).")
        return True
    text = ocr_text.image_text(path)
    print(f"intake: скриншот {key} · источник {source} · OCR "
          f"{'ок' if text else 'пусто'}")
    if not text:
        reply(uid, "OCR недоступен или не прочитал текст. "
                   "Поставь rapidocr (см. docs/EXTERNAL_SOURCES.md) "
                   "или добавь вручную: /ext @канал <текст>.")
        return True
    if _is_breakdown(msg):
        row = discord_intake.build_archive_row(
            source=source, message_id=key, url="",
            author="owner", text=str(msg.get("caption") or ""),
            images=[path], ocr=text)
        added = discord_intake.archive_rows([row])
        reply(uid, f"🗂 Разбор в архив ({'новый' if added else 'уже был'}): "
                   f"{row.get('symbol') or '?'} · "
                   f"{row.get('outcome') or 'без метки'} · R {row.get('r')}")
        return True
    parsed = consensus.parse_external(text, source)
    if parsed is not None:
        _intake_save(uid, source, text)
        return True
    row = discord_intake.make_review_row(
        source, message_id=key, ocr=text, images=[path],
        symbol=consensus.find_symbol(text) or "")
    discord_intake.add_review(row)
    tg.send_message(discord_intake.review_caption(row), token=TOKEN,
                    chat_id=uid, markup=discord_intake.review_markup(key))
    return True


def intake_forward(msg: dict, uid: str) -> bool:
    """Владелец переслал пост — сохраняем как голос. True = сообщение обработано."""
    if not cfg.OWNER_CHAT_ID or str(uid) != str(cfg.OWNER_CHAT_ID):
        return False
    if not _is_forward(msg):
        return False
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if not text:
        if _has_media(msg):
            return False        # картинку разберёт intake_media
        reply(uid, "В пересланном посте нет текста — перешли текст или скриншот.")
        return True
    source = forward_source(msg)
    if not source:
        reply(uid, "Источник скрыт: канал запрещает пересылку с автором. "
                   "Такой сигнал не учитываю — нужна атрибуция. "
                   "Можно вручную: /ext @канал <текст>.")
        return True
    _intake_save(uid, source, text)
    return True


def handle_update(users: dict, state: dict, up: dict) -> None:
    query = up.get("pre_checkout_query")
    if query:
        ok, info = tg.answer_pre_checkout(query.get("id", ""), token=TOKEN)
        if not ok:
            print("pre_checkout:", info)
        return

    cb = up.get("callback_query")
    if cb:
        tg.answer_callback(cb.get("id", ""), token=TOKEN)
        cmsg = cb.get("message") or {}
        cchat = (cmsg.get("chat") or {}).get("id")
        cuid = str((cb.get("from") or {}).get("id") or "")
        if not cuid or cchat is None:
            return
        try:
            changed = handle_callback(users, cuid, str(cchat),
                                      cmsg.get("message_id", 0), str(cb.get("data", "")))
        except Exception as e:
            print("ошибка callback:", repr(e))
            return
        if changed is not None:
            users[cuid] = changed
            save_json(USERS_PATH, users)
        return

    msg = up.get("message") or up.get("edited_message")
    if not msg:
        return
    chat = msg.get("chat", {})
    if chat.get("type") != "private":
        return
    uid = str(chat.get("id"))

    # Пользователь написал боту — значит, он его разблокировал. Снимаем метку,
    # иначе он навсегда останется вне рассылки.
    if (users.get(uid) or {}).get("blocked"):
        users[uid]["blocked"] = 0
        save_json(USERS_PATH, users)

    # владелец переслал пост из чужого канала — это голос консенсуса, не команда
    if intake_forward(msg, uid):
        return
    # скриншот (свой или пересланный) — OCR и либо голос, либо очередь с кнопками
    if intake_media(msg, uid):
        return

    refund = msg.get("refunded_payment")
    if refund:
        charge = str(refund.get("telegram_payment_charge_id")
                     or refund.get("provider_payment_charge_id") or "")
        if not charge:
            alert_owner(f"💸 Возврат Stars без charge_id · <code>{uid}</code>")
            return
        row = store.payment_row("stars", charge)
        if not row or not store.mark_payment_refunded(provider="stars", external_id=charge):
            alert_owner(f"💸 Возврат Stars по неизвестному платежу "
                        f"<code>{charge}</code> · <code>{uid}</code>")
            return
        days = int(row.get("days") or 30)
        now = time.time()
        until = paid_until(users, uid)
        cut = days * 86400.0
        # вернули уже истёкший период — текущую подписку не трогаем
        if float(row.get("created_at") or 0) + cut <= now:
            cut = 0.0
        # и никогда не уводим доступ в прошлое
        cut = min(cut, max(0.0, until - now))
        if cut > 0:
            u = users.setdefault(uid, {})
            u["paid_until"] = max(now, until - cut)
            save_json(USERS_PATH, users)
        alert_owner(f"💸 Возврат Stars: <code>{uid}</code> · charge <code>{charge}</code>")
        reply(uid, "Возврат получен. Срок подписки скорректирован.")
        return

    payment = msg.get("successful_payment")
    if payment:
        payload = str(payment.get("invoice_payload") or "")
        days = 30
        if payload.startswith("sub:"):
            parts = payload.split(":")
            if len(parts) > 2 and parts[2].strip().isdigit():
                days = max(1, min(int(parts[2]), 3650))
        exp = float(payment.get("subscription_expiration_date") or 0)
        # абсолютный срок диктует только подписка Telegram; годовой пакет — разовый
        until_ts = exp if exp > time.time() else None
        charge = str(payment.get("telegram_payment_charge_id")
                     or payment.get("provider_payment_charge_id") or "")
        if not charge:
            # Стабильный ключ из самого апдейта: повтор не продлит подписку дважды.
            stamp = int(msg.get("date") or 0)
            charge = f"{uid}:{payment.get('total_amount')}:{payload}:{stamp}"
            alert_owner(f"⚠️ Stars без charge_id — использую ключ <code>{charge}</code>")
        new = apply_payment(
            users, provider="stars", external_id=charge, uid=uid,
            amount=float(payment.get("total_amount", 0) or 0), currency="XTR",
            days=days, until_ts=until_ts, raw=payment,
        )
        granted = paid_until(users, uid)
        print(f"оплата: {uid} · +{days} дн. до {_date(granted)} · "
              f"recurring={payment.get('is_recurring')} · "
              f"{'новая' if new else 'повтор, пропущено'}")
        if new:
            tg.send_message(onboard_text(granted), token=TOKEN, chat_id=uid,
                            markup=menu_markup(users, uid))
            reward_referrer(users, uid)
        return

    text = (msg.get("text") or "").strip()
    if not text:
        return
    name = (msg.get("from") or {}).get("first_name", "")
    try:
        changed = handle(users, uid, text, name)
    except Exception as e:
        print("ошибка обработки:", repr(e))
        reply(uid, "Что-то пошло не так. Попробуй ещё раз или /help")
        return
    if changed is not None:
        users[uid] = changed
        save_json(USERS_PATH, users)


def main() -> None:
    if not TOKEN:
        print("не задан SUB_BOT_TOKEN в .env — premium-бот не запущен")
        return

    store.init_db()
    migrate_crypto_seen()
    users = load_json(USERS_PATH)
    reconcile_payments(users)
    state = load_json(STATE_PATH)
    ASSETS.update(brand.ensure_brand_assets(BRAND_DIR))
    paid = sum(1 for uid in users if is_paid(users, uid))
    pay_note = (f"{cfg.SUB_PRICE_STARS} Stars / {cfg.CRYPTO_PRICE_USDT:g} "
                f"{cfg.CRYPTO_ASSET}" if crypto_pay.enabled()
                else f"{cfg.SUB_PRICE_STARS} Stars")
    print(f"premium-бот запущен · подписчиков: {paid} из {len(users)} · "
          f"цена {pay_note} · задержка группы {cfg.FREE_DELAY_SEC} с"
          + (" · крипта: вкл" if crypto_pay.enabled() else " · крипта: выкл"))
    ok, info = tg.set_commands([
        ("start", "Меню"),
        ("buy", f"Подписка — {pay_note} / месяц"),
        ("status", "Статус подписки"),
        ("promo", "Активировать промокод"),
        ("stats", "Статистика сигналов"),
        ("lesson", "Следующая методичка"),
        ("lessons", "Все методички"),
        ("mycard", "Карточка счёта"),
        ("symbols", "Фильтр инструментов"),
        ("maxday", "Лимит сигналов в сутки"),
        ("feedback", "Отзыв автору бота"),
        ("help", "Помощь"),
    ], token=TOKEN)
    if not ok:
        print("setMyCommands:", info)
    offset = None
    next_remind = 0.0
    next_crypto = 0.0
    crypto_fail = 0
    update_fails: dict[int, int] = {}
    step_fails: dict[str, int] = {}

    def run_step(name: str, fn) -> bool:
        """Периодический шаг не должен ронять весь premium-бот."""
        try:
            fn()
            step_fails.pop(name, None)
            return True
        except Exception as e:
            n = step_fails.get(name, 0) + 1
            step_fails[name] = n
            print(f"ошибка шага {name}: {e!r}")
            if n in (1, 10) or n % 50 == 0:
                alert_owner(f"⚠️ Premium-бот: шаг <b>{name}</b> падает {n} раз подряд.\n"
                            f"<code>{e!r}</code>")
            return False

    try:
        while True:
            updates = tg.get_updates(offset=offset, timeout=POLL_TIMEOUT, token=TOKEN)
            for up in updates:
                upd_id = int(up.get("update_id", 0))
                try:
                    handle_update(users, state, up)
                    offset = upd_id + 1
                    update_fails.pop(upd_id, None)
                except Exception as e:
                    # апдейт не подтверждаем: повторная попытка не потеряет оплату
                    n = update_fails.get(upd_id, 0) + 1
                    update_fails[upd_id] = n
                    print("ошибка обработки апдейта:", repr(e))
                    if n in (1, 3):
                        alert_owner(f"⚠️ Ошибка обработки апдейта {upd_id} "
                                    f"(попытка {n}): <code>{e!r}</code>")
                    if n >= 5:
                        offset = upd_id + 1
                        update_fails.pop(upd_id, None)
                        alert_owner(f"❗ Апдейт {upd_id} пропущен после 5 попыток.")
                    else:
                        break
            run_step("signals", lambda: poll_signals(users, state))
            run_step("results", lambda: poll_results(users, state))
            run_step("deliveries", lambda: process_deliveries(users))
            run_step("nurture", lambda: process_nurture(users))
            if time.time() >= next_remind:
                next_remind = time.time() + (
                    REMIND_EVERY_SEC if run_step("remind", lambda: remind_expiring(users))
                    else 300)
            if time.time() >= next_crypto:
                try:
                    crypto_ok = poll_crypto(users)
                except Exception as e:
                    print("ошибка crypto:", repr(e))
                    crypto_ok = False
                if crypto_ok:
                    crypto_fail = 0
                    next_crypto = time.time() + CRYPTO_POLL_SEC
                else:
                    crypto_fail += 1
                    delay = min(600, CRYPTO_POLL_SEC * (2 ** min(crypto_fail, 5)))
                    next_crypto = time.time() + delay
                    if crypto_fail in (3, 10) or crypto_fail % 20 == 0:
                        alert_owner(
                            f"⚠️ Crypto Pay недоступен {crypto_fail} раз подряд. "
                            f"Оплаты могут не зачисляться. Следующая попытка через {delay} с.")
            if not updates:
                time.sleep(1)
    except KeyboardInterrupt:
        print("остановлено вручную")


if __name__ == "__main__":
    main()
