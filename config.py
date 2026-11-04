"""Конфигурация сервиса. Все значения — из .env (см. .env.example)."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


# --- Telegram (сигнальный бот: постит в канал/группу) ---
BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()

# --- Telegram (premium-бот: подписчики задают свой депозит и риск-правила) ---
# Отдельный бот от сигнального — свой токен от @BotFather, свой процесс subbot.py.
SUB_BOT_TOKEN = os.getenv("SUB_BOT_TOKEN", "").strip()
# Подпись канала на карточке-сводке (например @kidzordfx)
CHANNEL_HANDLE = os.getenv("CHANNEL_HANDLE", "").strip()
# Ссылка на бесплатную группу (инвайт-ссылка или https://t.me/<username>) —
# показывается тем, у кого нет премиум-подписки
CHANNEL_INVITE = os.getenv("CHANNEL_INVITE", "").strip()
# Значения по умолчанию, пока подписчик не задал свои через бота
PLAN_DEPOSIT = _float("PLAN_DEPOSIT", 10000.0)
PLAN_RISK_PCT = _float("PLAN_RISK_PCT", 0.5)
PLAN_DAILY_PCT = _float("PLAN_DAILY_PCT", 5.0)
PLAN_TOTAL_PCT = _float("PLAN_TOTAL_PCT", 10.0)

# --- Premium-подписка (Telegram Stars) ---
# Цена подписки в Stars (1–10000). У Telegram период подписки только 30 дней.
SUB_PRICE_STARS = int(_float("SUB_PRICE_STARS", 500.0))
# Годовой план: разовый счёт Stars (подписка Telegram умеет только 30 дней).
# 0 — годовой план выключен.
SUB_PRICE_STARS_YEAR = int(_float("SUB_PRICE_STARS_YEAR", "0"))
SUB_TITLE = os.getenv("SUB_TITLE", "KidzordFX Premium").strip()
SUB_DESCRIPTION = os.getenv(
    "SUB_DESCRIPTION",
    "Быстрые сигналы по золоту, форексу и индексам в боте: вход, стоп и три цели приходят сразу, "
    "без задержки. Плюс карточка твоего счёта под твой депозит и риск.",
).strip()
# Задержка публикации сигнала в бесплатную группу, секунд (0 — без задержки).
# Premium-подписчики получают сигнал сразу.
FREE_DELAY_SEC = int(_float("FREE_DELAY_SEC", 600.0))

# --- Premium-подписка (крипта через Crypto Pay / @CryptoBot) ---
# Токен приложения: @CryptoBot → Create App → API token.
# Пусто — приём крипты выключен, бот работает только на Stars.
CRYPTO_PAY_TOKEN = os.getenv("CRYPTO_PAY_TOKEN", "").strip()
# 1 — тестовая сеть Crypto Pay (проверка потока оплаты без реальных денег)
CRYPTO_PAY_TESTNET = _flag("CRYPTO_PAY_TESTNET", "0")
# Цена подписки в крипте (0 — способ оплаты выключен). ~500 ⭐ ≈ 8 USDT.
CRYPTO_PRICE_USDT = _float("CRYPTO_PRICE_USDT", 8.0)
# Годовая цена в крипте (0 — годовой план выключен).
CRYPTO_PRICE_USDT_YEAR = _float("CRYPTO_PRICE_USDT_YEAR", "0.0")
# Валюта счёта: USDT, TON, BTC, ETH и т.п.
CRYPTO_ASSET = os.getenv("CRYPTO_ASSET", "USDT").strip().upper() or "USDT"

# --- Вебхук ---
# Алерт шлётся на /tv/<WEBHOOK_SECRET> — секрет в пути, чтобы URL нельзя было угадать.
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip() or "change-me"
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))

# --- Фильтр символов ---
# Пусто = принимать всё. Иначе подстрока в тикере: "XAU" ловит XAUUSD, GOLD, XAUUSD.r и т.п.
# Список — те же инструменты, что гоняет multi.bat (золото, форекс, индексы).
SYMBOL_FILTER = tuple(
    s.strip().upper() for s in os.getenv(
        "SYMBOL_FILTER",
        "XAU,GOLD,AUDUSD,USDCAD,EURUSD,GBPUSD,US500,EUSTX50,UK100,DE40,JPN225",
    ).split(",") if s.strip()
)

# --- График ---
BARS_COUNT = int(os.getenv("BARS_COUNT", "120"))
MT5_SUFFIX = os.getenv("MT5_SUFFIX", "").strip()      # напр. ".r" для брокеров с суффиксом
MT5_TERMINAL_PATH = os.getenv("MT5_TERMINAL_PATH", "").strip()
DATA_CSV = os.getenv("DATA_CSV", "").strip()          # резервный источник, если нет MT5
CARD_TITLE = os.getenv("CARD_TITLE", "KIDZORDFX").strip()


def mt5_init(**kwargs):
    """Подключение к терминалу MT5.

    С двумя установленными терминалами пакет MetaTrader5 выбирает последний
    запущенный, поэтому live-путь задаётся явно через MT5_TERMINAL_PATH.
    """
    import MetaTrader5 as mt5

    if MT5_TERMINAL_PATH:
        kwargs.setdefault("path", MT5_TERMINAL_PATH)
    return mt5.initialize(**kwargs)

# --- Дневной пост ---
PROMO_ENABLED = _flag("PROMO_ENABLED", "1")
PROMO_TIME = os.getenv("PROMO_TIME", "10:00").strip()  # ЧЧ:ММ
PROMO_TZ = os.getenv("PROMO_TZ", "Europe/Moscow").strip()
PROMO_TEXT = os.getenv(
    "PROMO_TEXT",
    "📊 <b>KidzordFx V1</b> — индикатор для золота, форекса и индексов на MetaTrader 5\n\n"
    "Свип ликвидности + BOS + зона EMA200 старшего ТФ, чек-лист из 12 пунктов, "
    "готовые уровни входа, стопа и трёх целей.\n\n"
    "Сигналы по золоту, форексу и индексам публикуются здесь.\n"
    "Доступ к индикатору — @your_username",
).replace("\\n", "\n")

# --- Автопосты (autoposts.py) ---
# Личный чат владельца канала: сюда уходят напоминания о постах Threads.
# Заполняется командой /threads в premium-боте (owner.json) или вручную тут.
OWNER_CHAT_ID = os.getenv("OWNER_CHAT_ID", "").strip()
# Ссылка на premium-бота — кнопка в продающем посте
PREMIUM_URL = os.getenv("PREMIUM_URL", "https://t.me/KidzordPremim_bot").strip()
# Расписание постов в группу (в PROMO_TZ, ЧЧ:ММ)
PLAN_TIME = os.getenv("PLAN_TIME", "08:00").strip()
DAY_SUMMARY_TIME = os.getenv("DAY_SUMMARY_TIME", "23:30").strip()
WEEK_SUMMARY_TIME = os.getenv("WEEK_SUMMARY_TIME", "23:50").strip()
# Пост-превью для Telegram: раз в день владельцу, публикация — по кнопке.
TG_CONTENT_ENABLED = _flag("TG_CONTENT_ENABLED", "1")
TG_CONTENT_TIME = os.getenv("TG_CONTENT_TIME", "12:00").strip()
# Напоминания о постах Threads в личку: список времён через запятую
THREADS_REMIND_ENABLED = _flag("THREADS_REMIND_ENABLED", "1")
THREADS_REMIND_TIMES = os.getenv("THREADS_REMIND_TIMES", "09:00,14:00,19:00").strip()
# Отдельный слот под реальную сделку и недельный трек-рекорд для Threads.
THREADS_RESULT_ENABLED = _flag("THREADS_RESULT_ENABLED", "1")
THREADS_RESULT_TIME = os.getenv("THREADS_RESULT_TIME", "19:00").strip()
THREADS_WEEK_ENABLED = _flag("THREADS_WEEK_ENABLED", "1")
THREADS_WEEK_TIME = os.getenv("THREADS_WEEK_TIME", "20:00").strip()
# Лендинг собирается локально из trades.jsonl (деплой — вручную/CI).
LANDING_ENABLED = _flag("LANDING_ENABLED", "1")
LANDING_TIME = os.getenv("LANDING_TIME", "23:55").strip()
# Напоминания о постах для X (отдельный англ. аккаунт) в личку
X_REMIND_ENABLED = _flag("X_REMIND_ENABLED", "1")
X_REMIND_TIMES = os.getenv("X_REMIND_TIMES", "10:30,16:30").strip()
# TikTok: карусель из карточек и текста в личку владельцу (API требует аудита).
# Два слота в день: днём — «что сделал в боте», вечером — дневниковая история.
TIKTOK_REMIND_ENABLED = _flag("TIKTOK_REMIND_ENABLED", "1")
TIKTOK_REMIND_TIMES = os.getenv("TIKTOK_REMIND_TIMES", "12:30,19:30").strip()

# --- Пробный доступ и рефералы (premium-бот) ---
# Пробный доступ: сколько дней даём один раз новому пользователю (0/выкл — не даём)
TRIAL_ENABLED = _flag("TRIAL_ENABLED", "1")
TRIAL_DAYS = int(_float("TRIAL_DAYS", 3.0))
# Реферальная программа: сколько дней получает пригласивший за оплату друга
REF_ENABLED = _flag("REF_ENABLED", "1")
REF_DAYS = int(_float("REF_DAYS", 7.0))
# Бонус приглашённому после его первой оплаты (двусторонняя рефералка).
REF_DAYS_FRIEND = int(_float("REF_DAYS_FRIEND", 3.0))
# Промокоды: CODE:DAYS через запятую, например NEW20:20,OLD30:30.
# Один код — один раз на пользователя.
PROMO_CODES: dict[str, int] = {}
for _item in os.getenv("PROMO_CODES", "").split(","):
    _item = _item.strip()
    if ":" in _item:
        _code, _days = _item.split(":", 1)
        try:
            PROMO_CODES[_code.strip().upper()] = int(float(_days))
        except ValueError:
            continue
# username premium-бота без @ — из него собирается реферальная ссылка ?start=ref_<id>
SUB_BOT_USERNAME = os.getenv("SUB_BOT_USERNAME", "").strip().lstrip("@")
# ссылка «Поддержка» (t.me/username или инвайт) — кнопка в меню бота
SUPPORT_URL = os.getenv("SUPPORT_URL", "").strip()

# --- Месячный отчёт и мониторинг (autoposts.py) ---
# публичный отчёт по истории: 1-е число месяца, время в PROMO_TZ
MONTH_SUMMARY_ENABLED = _flag("MONTH_SUMMARY_ENABLED", "1")
MONTH_SUMMARY_TIME = os.getenv("MONTH_SUMMARY_TIME", "12:00").strip()
# мониторинг: раз в N минут проверяем «пульс» сигнального бота (bot.py)
MONITOR_ENABLED = _flag("MONITOR_ENABLED", "1")
MONITOR_EVERY_MIN = int(_float("MONITOR_EVERY_MIN", 15.0))
# если пульса нет дольше M минут — алерт владельцу в личку
MONITOR_STALE_MIN = int(_float("MONITOR_STALE_MIN", 15.0))
# сколько процессов bot.py должно работать (range AUDUSD+USDCAD+EURUSD + classic XAUUSD)
EXPECTED_BOTS = int(_float("HEARTBEAT_EXPECT_BOTS", 3.0))
# Внешний dead-man switch: URL healthchecks.io (или аналога). Пока ПК жив,
# monitor.py раз в цикл пингует его; если ПК упал — сервис шлёт алерт.
HEALTHCHECK_URL = os.getenv("HEALTHCHECK_URL", "").strip()
# Компонент (subbot/autoposts/heartbeat/monitor/watchdog) отсутствует дольше
# COMPONENT_ALERT_MIN минут — алерт владельцу. Watchdog обычно поднимает
# раньше, поэтому короткие перезапуски не спамят.
COMPONENT_ALERT_MIN = int(_float("COMPONENT_ALERT_MIN", 10.0))

# --- Надёжность: kill-switch, форвард-контроль, restore-drill, новости ---
# Портфельный kill-switch: сумма R закрытых сделок с начала недели ниже
# PORTFOLIO_WEEK_LIMIT_R (и сделок не меньше PORTFOLIO_WEEK_MIN_TRADES)
# останавливает новые сигналы до следующего понедельника.
PORTFOLIO_WEEK_LIMIT_R = _float("PORTFOLIO_WEEK_LIMIT_R", -6.0)
PORTFOLIO_WEEK_MIN_TRADES = int(_float("PORTFOLIO_WEEK_MIN_TRADES", 4.0))
# Форвард-контроль: последние FORWARD_WINDOW сделок символа дали
# <= FORWARD_DEGRADE_R — кандидат на отключение (алерт в вс 18:00).
FORWARD_WINDOW = int(_float("FORWARD_WINDOW", 20.0))
FORWARD_DEGRADE_R = _float("FORWARD_DEGRADE_R", -3.0)
FORWARD_CONTROL_ENABLED = _flag("FORWARD_CONTROL_ENABLED", "1")
FORWARD_CONTROL_TIME = os.getenv("FORWARD_CONTROL_TIME", "18:00").strip()
# Тренировочное восстановление из последнего бэкапа в песочницу.
RESTORE_DRILL_ENABLED = _flag("RESTORE_DRILL_ENABLED", "1")
RESTORE_DRILL_TIME = os.getenv("RESTORE_DRILL_TIME", "18:10").strip()
# Новостной фильтр: красные события экономического календаря ForexFactory.
NEWS_FILTER_ENABLED = _flag("NEWS_FILTER_ENABLED", "1")
NEWS_BLOCK_BEFORE_MIN = int(_float("NEWS_BLOCK_BEFORE_MIN", 30.0))
NEWS_BLOCK_AFTER_MIN = int(_float("NEWS_BLOCK_AFTER_MIN", 15.0))
NEWS_CACHE_TTL_H = _float("NEWS_CACHE_TTL_H", 6.0)

# --- Консенсус внешних источников (только внутренний фильтр) ---
# Бот не читает чужие каналы сам: сигналы попадают в consensus_signals.jsonl
# только авторизованно (CLI, /consensus/{secret} в app.py) и не публикуются.
# warn — писать в консоль, filter — пропускать вход при конфликте.
CONSENSUS_ENABLED = _flag("CONSENSUS_ENABLED", "0")
CONSENSUS_MODE = os.getenv("CONSENSUS_MODE", "warn").strip().lower()
CONSENSUS_MIN_AGREE = int(_float("CONSENSUS_MIN_AGREE", 2.0))
CONSENSUS_WINDOW_MIN = int(_float("CONSENSUS_WINDOW_MIN", 60.0))
CONSENSUS_MAX_AGE_MIN = int(_float("CONSENSUS_MAX_AGE_MIN", 240.0))
CONSENSUS_SOURCES = tuple(
    s.strip().lower()
    for s in os.getenv("CONSENSUS_SOURCES", "").split(",")
    if s.strip()
)
CONSENSUS_BLOCK_CONFLICT = _flag("CONSENSUS_BLOCK_CONFLICT", "1")
CONSENSUS_PATH = os.getenv("CONSENSUS_PATH", "").strip()

# --- Research-гейт для идей из внешних источников ---
# Порог, ниже которого WFA-результат не считается основанием для включения.
RESEARCH_MIN_TRADES = int(_float("RESEARCH_MIN_TRADES", 20.0))
RESEARCH_MIN_POS_FOLDS = _float("RESEARCH_MIN_POS_FOLDS", 0.6)
RESEARCH_PATH = os.getenv("RESEARCH_PATH", "").strip()

# --- Discord-приём (официальный Bot API, см. discord_intake.py) ---
# Бот слушает только каналы из DISCORD_CHANNELS: пусто — не слушает ничего.
# Текст разбирается сразу, картинки — через OCR; нераспознанное идёт в очередь
# на проверку, а не в голоса консенсуса.
DISCORD_INTAKE_ENABLED = _flag("DISCORD_INTAKE_ENABLED", "0")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DISCORD_CHANNELS = tuple(
    s.strip() for s in os.getenv("DISCORD_CHANNELS", "").split(",") if s.strip()
)
DISCORD_REVIEW_ALERT = _flag("DISCORD_REVIEW_ALERT", "1")
# Каналы с разборами сделок (профит/убыток) — это не голоса, а корпус для
# research: сообщения оттуда архивируются в discord_trades.jsonl.
DISCORD_ARCHIVE_CHANNELS = tuple(
    s.strip() for s in os.getenv("DISCORD_ARCHIVE_CHANNELS", "").split(",") if s.strip()
)

# --- Ретеншн файлов (tools/prune.py) ---
# Чистятся только renders/, tg_screenshots/, discord_inbox/, preview/ —
# обычные файлы старше PRUNE_DAYS. Запуск по расписанию в autoposts.py.
PRUNE_ENABLED = _flag("PRUNE_ENABLED", "1")
PRUNE_TIME = os.getenv("PRUNE_TIME", "04:00").strip()
PRUNE_DAYS = int(_float("PRUNE_DAYS", 30.0))

# Доставка: сколько попыток и максимальная пауза между ними.
DELIVERY_MAX_ATTEMPTS = int(_float("DELIVERY_MAX_ATTEMPTS", 6.0))
DELIVERY_ALERT = _flag("DELIVERY_ALERT", "1")

# --- Прочее ---
DEDUP_TTL_SEC = int(os.getenv("DEDUP_TTL_SEC", "900"))
DISCLAIMER = os.getenv(
    "DISCLAIMER", "Не является инвестиционной рекомендацией."
).strip()

# Часовой пояс, в котором показывается время сигнала, и его подпись
DISPLAY_TZ = os.getenv("DISPLAY_TZ", "Europe/Moscow").strip()
TZ_LABEL = os.getenv("TZ_LABEL", "MSK").strip()

# --- Логирование и мониторинг ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip()
LOG_JSON = _flag("LOG_JSON", "0")
LOG_FILE = os.getenv("LOG_FILE", "").strip()  # пусто = только console
