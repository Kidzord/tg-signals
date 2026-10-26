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
        "XAU,GOLD,AUDUSD,USDCAD,GBPUSD,US500,EUSTX50,UK100,DE40,JPN225",
    ).split(",") if s.strip()
)

# --- График ---
BARS_COUNT = int(os.getenv("BARS_COUNT", "120"))
MT5_SUFFIX = os.getenv("MT5_SUFFIX", "").strip()      # напр. ".r" для брокеров с суффиксом
DATA_CSV = os.getenv("DATA_CSV", "").strip()          # резервный источник, если нет MT5
CARD_TITLE = os.getenv("CARD_TITLE", "KIDZORDFX").strip()

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
# Напоминания о постах Threads в личку: список времён через запятую
THREADS_REMIND_ENABLED = _flag("THREADS_REMIND_ENABLED", "1")
THREADS_REMIND_TIMES = os.getenv("THREADS_REMIND_TIMES", "09:00,14:00,19:00").strip()
# Напоминания о постах для X (отдельный англ. аккаунт) в личку
X_REMIND_ENABLED = _flag("X_REMIND_ENABLED", "1")
X_REMIND_TIMES = os.getenv("X_REMIND_TIMES", "10:30,16:30").strip()
# Напоминания о постах для VK и Дзена (RU, тот же банк, что у Threads)
RU_REMIND_ENABLED = _flag("RU_REMIND_ENABLED", "1")
VK_REMIND_TIMES = os.getenv("VK_REMIND_TIMES", "11:00,17:00").strip()
DZEN_REMIND_TIMES = os.getenv("DZEN_REMIND_TIMES", "12:00,18:00").strip()

# --- Пробный доступ и рефералы (premium-бот) ---
# Пробный доступ: сколько дней даём один раз новому пользователю (0/выкл — не даём)
TRIAL_ENABLED = _flag("TRIAL_ENABLED", "1")
TRIAL_DAYS = int(_float("TRIAL_DAYS", 3.0))
# Реферальная программа: сколько дней получает пригласивший за оплату друга
REF_ENABLED = _flag("REF_ENABLED", "1")
REF_DAYS = int(_float("REF_DAYS", 7.0))
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
