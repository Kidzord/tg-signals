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
    /id                твой Telegram ID
    /threads           привязать этот чат владельцу (напоминания о постах для Threads)
    /help              список команд
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import brand
import config as cfg
import crypto_pay
import render as card
import tg
import ui_components
import subbot_ui

HERE = os.path.dirname(os.path.abspath(__file__))
USERS_PATH = os.path.join(HERE, "users.json")
STATE_PATH = os.path.join(HERE, "sub_state.json")
QUEUE_PATH = os.path.join(HERE, "signals_queue.jsonl")
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
    if crypto_pay.enabled():
        return (f"{cfg.SUB_PRICE_STARS} ⭐ или {cfg.CRYPTO_PRICE_USDT:g} "
                f"{cfg.CRYPTO_ASSET} в месяц")
    return f"{cfg.SUB_PRICE_STARS} ⭐ в месяц"


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
    "/stats — статистика сигналов (7 и 30 дней)\n"
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
        f"Цена — {price_line()}." + trial + tail
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
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%d.%m.%Y")


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


def grant(users: dict, uid: str, until_ts: float) -> None:
    """Продлить подписку: оплата могла прийти заново до истечения срока."""
    u = users.setdefault(uid, {})
    u["paid_until"] = max(paid_until(users, uid), until_ts)
    save_json(USERS_PATH, users)


def stops_today() -> int:
    """Стопы за сегодня (UTC) — из общего журнала сигнального бота."""
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    try:
        with open(RISK_PATH, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip() == day)
    except FileNotFoundError:
        return 0


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
    cut = time.time() - days * 86400
    rs = [t["r"] for t in load_trades() if t["ts"] >= cut]
    if not rs:
        return f"<b>{label}</b>: сделок не было."
    wins = sum(1 for r in rs if r > 0)
    return (f"<b>{label}</b>: сделок {len(rs)} · прибыльных {wins} "
            f"({wins / len(rs) * 100:.0f}%) · итог <b>{sum(rs):+.1f}R</b>")


def send_stats(uid: str) -> None:
    """Публичный послужной список канала: неделя и месяц."""
    tail = ""
    if channel_link():
        tail = (f'\n\nВсе сигналы публикуем открыто — вход, стоп и результат. '
                f'Полный поток: <a href="{channel_link()}">{cfg.CHANNEL_HANDLE or "группа"}</a>.')
    reply(uid,
          "📈 <b>Статистика сигналов</b>\n\n"
          + _stats_line(7, "7 дней") + "\n"
          + _stats_line(30, "30 дней") +
          "\n\n<i>R — размер результата в рисках: +1R — заработали один риск, "
          "−1R — отдали. Публикуем и плюсы, и стопы.</i>" + tail)


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
    """После оплаты: начислить пригласившему +REF_DAYS. Один раз на пользователя."""
    u = users.get(uid) or {}
    ref = str(u.get("ref_by", "") or "")
    if not cfg.REF_ENABLED or not ref or u.get("ref_rewarded"):
        return
    u["ref_rewarded"] = True
    if ref != uid and ref in users:
        r = users[ref]
        until = max(paid_until(users, ref), time.time()) + cfg.REF_DAYS * 86400
        r["paid_until"] = until
        r["refs"] = int(r.get("refs", 0) or 0) + 1
        save_json(USERS_PATH, users)
        tg.send_message(
            f"🎁 По твоей ссылке оформили Premium — начислил <b>+{cfg.REF_DAYS} дн.</b>\n"
            f"Доступ активен до <b>{_date(until)}</b>. Спасибо, что приводишь своих.",
            token=TOKEN, chat_id=ref)
        return
    save_json(USERS_PATH, users)


def trial_available(users: dict, uid: str) -> bool:
    """Пробный доступ: доступен один раз тому, кто ещё не платил и не пробовал."""
    if not cfg.TRIAL_ENABLED or cfg.TRIAL_DAYS <= 0:
        return False
    u = users.get(uid) or {}
    return not u.get("trial_used") and paid_until(users, uid) <= time.time()


def grant_trial(users: dict, uid: str) -> None:
    """Выдать пробные дни один раз: отмечаем trial_used и включаем подписку."""
    u = users.setdefault(uid, {})
    u["trial_used"] = True
    until = time.time() + cfg.TRIAL_DAYS * 86400
    users[uid]["paid_until"] = max(paid_until(users, uid), until)
    save_json(USERS_PATH, users)
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


def _num(raw: str, lo: float, hi: float) -> float | None:
    try:
        v = float(raw.replace(",", ".").strip())
    except (ValueError, AttributeError):
        return None
    return v if lo <= v <= hi else None


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
        f"Депозит {usd(plan.deposit)} · риск {plan.risk_pct:.2f}% = "
        f"{usd(plan.risk_money)} на сделку\n"
        f"Дневной стоп {plan.daily_pct:.1f}% · общий стоп {plan.total_pct:.1f}%\n"
        f"<i>{cfg.DISCLAIMER}</i>"
    )
    ok, info = tg.send_photo(path, caption, token=TOKEN, chat_id=uid,
                             markup=menu_markup(users, uid))
    if not ok:
        reply(uid, "Не удалось отправить карточку. Попробуй позже.")


def send_buy(uid: str) -> None:
    """Оплата подписки: карточка выгод и выбор способа (Stars / крипта)."""
    if not crypto_pay.enabled():
        send_stars(uid)
        return
    rows = [
        [{"text": f"⭐ Telegram Stars — {cfg.SUB_PRICE_STARS} ⭐",
          "callback_data": "pay:stars"}],
        [{"text": f"💠 Крипта — {cfg.CRYPTO_PRICE_USDT:g} {cfg.CRYPTO_ASSET}",
          "callback_data": "pay:crypto"}],
    ]
    cb = channel_buttons()
    if cb:
        rows.append(cb)
    markup = {"inline_keyboard": rows}
    caption = (
        f"<b>{cfg.SUB_TITLE}</b>\n\n{cfg.SUB_DESCRIPTION}\n\n"
        "Выбери, как удобнее оплатить:\n"
        f"• <b>Stars</b> — {cfg.SUB_PRICE_STARS} ⭐, продление автоматическое\n"
        f"• <b>Крипта</b> — {cfg.CRYPTO_PRICE_USDT:g} {cfg.CRYPTO_ASSET}, оплата разовая"
    )
    offer = asset("offer")
    if offer and os.path.exists(offer):
        sent, _ = tg.send_photo(offer, caption, token=TOKEN, chat_id=uid, markup=markup)
        if sent:
            return
    tg.send_message(caption, token=TOKEN, chat_id=uid, markup=markup)


def send_stars(uid: str) -> None:
    """Карточка выгод + счёт в Stars: кнопка на оплату подписки."""
    ok, res = tg.create_invoice_link(
        title=cfg.SUB_TITLE, description=cfg.SUB_DESCRIPTION,
        payload=f"sub:{uid}", stars=cfg.SUB_PRICE_STARS, token=TOKEN)
    if not ok:
        print("createInvoiceLink:", res)
        reply(uid, "Не получилось создать счёт. Попробуй позже.")
        return
    rows = [[{"text": f"Оплатить {cfg.SUB_PRICE_STARS} ⭐", "url": res}]]
    cb = channel_buttons()
    if cb:
        rows.append(cb)
    markup = {"inline_keyboard": rows}
    caption = (
        f"<b>{cfg.SUB_TITLE}</b>\n\n{cfg.SUB_DESCRIPTION}\n\n"
        f"Подписка {cfg.SUB_PRICE_STARS} ⭐ в месяц: продлевается автоматически, "
        "отменить можно в любой момент в «Мои звёзды»."
    )
    offer = asset("offer")
    if offer and os.path.exists(offer):
        sent, _ = tg.send_photo(offer, caption, token=TOKEN, chat_id=uid, markup=markup)
        if sent:
            return
    tg.send_message(caption, token=TOKEN, chat_id=uid, markup=markup)


def send_crypto(uid: str) -> None:
    """Счёт в крипте через Crypto Pay: ссылка на оплату."""
    ok, inv = crypto_pay.create_invoice(
        cfg.CRYPTO_PRICE_USDT, f"{cfg.SUB_TITLE} — 30 дней", f"sub:{uid}")
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
    rows = [[{"text": f"💠 Оплатить {cfg.CRYPTO_PRICE_USDT:g} {cfg.CRYPTO_ASSET}",
              "url": url}],
            [{"text": "← Другой способ", "callback_data": "buy"}]]
    cb = channel_buttons()
    if cb:
        rows.append(cb)
    tg.send_message(
        f"<b>Оплата криптой</b>\n\n"
        f"Счёт на <b>{cfg.CRYPTO_PRICE_USDT:g} {cfg.CRYPTO_ASSET}</b> "
        f"(сеть {cfg.CRYPTO_ASSET}).\n"
        "Открой счёт кнопкой ниже и оплати — подписка включится сама, "
        "как только платёж подтвердится (обычно 1–3 мин).\n\n"
        "Оплата разовая: продление нужно повторить, бот напомнит заранее.",
        token=TOKEN, chat_id=uid, markup={"inline_keyboard": rows})


def send_status(uid: str, users: dict) -> None:
    ts = paid_until(users, uid)
    if ts <= time.time():
        send_gate(uid)
        return
    left = int((ts - time.time()) // 86400) + 1
    text = f"Подписка активна до <b>{_date(ts)}</b> (осталось {left} дн.)."
    rows: list[list[dict]] = []
    link = ref_link(uid)
    if link:
        refs = int((users.get(uid) or {}).get("refs", 0) or 0)
        text += (f"\n\n🎁 <b>Пригласи друга</b>\n"
                 f"За каждого, кто оформит Premium по твоей ссылке, — "
                 f"+{cfg.REF_DAYS} дн. бесплатно. Уже приглашено: {refs}.\n{link}")
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
                     [{"text": BTN_SETTINGS}, {"text": BTN_HELP}]],
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
        rows.append([{"text": f"⚡ Оформить подписку — {cfg.SUB_PRICE_STARS} ⭐",
                      "callback_data": "buy"}])
        if trial_available(users, uid):
            rows.append([{"text": f"🎁 Пробный доступ — {cfg.TRIAL_DAYS} дн.",
                          "callback_data": "trial"}])
    rows.append([{"text": "📈 Статистика", "callback_data": "stats"},
                 {"text": "❓ FAQ", "callback_data": "faq"}])
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
        "• бот считает объём под твой депозит\n"
        "• следит за твоим дневным и общим лимитом\n\n"
    )
    if is_paid(users, uid):
        body += (f"✅ Подписка активна до <b>{_date(paid_until(users, uid))}</b>.\n"
                 "Открой «📊 Мой счёт» внизу.")
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
        send_buy(uid)
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


def send_gate(uid: str) -> None:
    """Отказ без подписки — бренд-карточка и кнопка оплаты."""
    text = not_paid_text()
    markup = menu_markup({}, uid)
    gate = asset("gate")
    if gate and os.path.exists(gate):
        sent, _ = tg.send_photo(gate, text, token=TOKEN, chat_id=uid, markup=markup)
        if sent:
            return
    tg.send_message(text, token=TOKEN, chat_id=uid, markup=markup)


def settings_text(users: dict, uid: str) -> str:
    p = user_plan(users, uid)
    risk_money = float(p["deposit"]) * float(p["risk"]) / 100.0
    return (
        "<b>Настрой свой план</b>\n\n"
        f"Депозит <b>{usd(float(p['deposit']))}</b>\n"
        f"Риск на сделку <b>{float(p['risk']):.2f}%</b> = <b>{usd(risk_money)}</b>\n"
        f"Дневной стоп <b>{float(p['daily']):.1f}%</b> · "
        f"общий стоп <b>{float(p['total']):.1f}%</b>\n\n"
        "Нажми на строку, чтобы изменить."
    )


def settings_markup(users: dict, uid: str) -> dict:
    p = user_plan(users, uid)
    risk_money = float(p["deposit"]) * float(p["risk"]) / 100.0
    return {"inline_keyboard": [
        [{"text": f"💰 Депозит: {usd(float(p['deposit']))}", "callback_data": "set:deposit"}],
        [{"text": f"🎯 Риск: {float(p['risk']):.2f}% (={usd(risk_money)})",
          "callback_data": "set:risk"}],
        [{"text": f"🛑 День: {float(p['daily']):.1f}%", "callback_data": "set:daily"}],
        [{"text": f"🧱 Всего: {float(p['total']):.1f}%", "callback_data": "set:total"}],
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

    # Новые кнопки из ui_components
    new_result = subbot_ui.handle_callback(data, uid, users)
    if new_result is not None:
        return new_result

    if data == "buy":
        send_buy(uid)
        return None

    if data == "pay:stars":
        send_stars(uid)
        return None

    if data == "pay:crypto":
        send_crypto(uid)
        return None

    if data == "card":
        if is_paid(users, uid):
            send_card(uid, users)
        else:
            _edit_or_send(chat_id, mid, not_paid_text(),
                          menu_markup(users, uid), uid)
        return None

    if data == "stats":
        send_stats(uid)
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
            send_gate(uid)
        return None
    if text == BTN_SETTINGS:
        if is_paid(users, uid):
            send_settings(uid, users)
        else:
            send_gate(uid)
        return None
    if text == BTN_HELP:
        reply(uid, HELP)
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
                   "по расписанию, чтобы ты не забывал публиковать.")
        return None

    if cmd in ("start", "menu"):
        if args:
            bind_referrer(users, uid, args[0])
        send_welcome(users, uid, name)
        return None

    if cmd == "help":
        reply(uid, HELP)
        return None

    if cmd in ("stats", "stat", "statistics"):
        send_stats(uid)
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

    if cmd in ("buy", "subscribe"):
        send_buy(uid)
        return None

    if cmd == "premium":
        send_premium(uid, users)
        return None

    if cmd == "status":
        send_status(uid, users)
        return None

    # остальное — только по активной подписке
    if not is_paid(users, uid):
        send_gate(uid)
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

    reply(uid, "Не понял. Пользуйся кнопками внизу или глянь /help.")
    return None


# ------------------------------------------------------------------ доставка сигналов

def poll_signals(users: dict, state: dict) -> None:
    """Разослать подписчикам сигналы, появившиеся в очереди bot.py."""
    if not os.path.exists(QUEUE_PATH):
        return
    last = float(state.get("last_signal_ts", 0) or 0)
    fresh: list[tuple[float, str, str]] = []
    try:
        with open(QUEUE_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                ts = float(rec.get("ts", 0) or 0)
                path = str(rec.get("path", ""))
                if ts <= last or not path or not os.path.exists(path):
                    continue
                fresh.append((ts, path, str(rec.get("caption", ""))))
    except OSError as e:
        print("очередь недоступна:", e)
        return
    if not fresh:
        return
    paid = [uid for uid in users if is_paid(users, uid)]
    for ts, path, cap in sorted(fresh):
        for uid in paid:
            tg.send_photo(path, cap, token=TOKEN, chat_id=uid,
                          markup=signal_markup(users, uid))
    state["last_signal_ts"] = max(ts for ts, _, _ in fresh)
    save_json(STATE_PATH, state)
    print(f"сигналов: {len(fresh)} · подписчиков: {len(paid)}")


def remind_expiring(users: dict) -> None:
    """Напомнить о продлении за REMIND_BEFORE_SEC до конца — один раз на период."""
    now = time.time()
    changed = False
    for uid in list(users):
        until = paid_until(users, uid)
        if until <= now or until - now > REMIND_BEFORE_SEC:
            continue
        if users[uid].get("reminded_for") == until:
            continue
        tg.send_message(
            remind_text(until), token=TOKEN, chat_id=uid,
            markup={"inline_keyboard": [[
                {"text": f"🔄 Продлить — {cfg.SUB_PRICE_STARS} ⭐",
                 "callback_data": "buy"}]]})
        users[uid]["reminded_for"] = until
        changed = True
    if changed:
        save_json(USERS_PATH, users)


def poll_crypto(users: dict) -> None:
    """Оплаченные счета Crypto Pay → выдать подписку автору счёта.

    Счёт помечается в crypto_paid.json по invoice_id, чтобы один платёж
    не продлил подписку дважды.
    """
    if not crypto_pay.enabled():
        return
    ok, items = crypto_pay.paid_invoices()
    if not ok:
        print("crypto getInvoices:", items)
        return
    seen = load_json(CRYPTO_STATE_PATH)
    got = False
    for inv in items:
        iid = str(inv.get("invoice_id") or "")
        payload = str(inv.get("payload") or "")
        if not iid or iid in seen or not payload.startswith("sub:"):
            continue
        uid = payload[4:].strip()
        if not uid:
            continue
        until = time.time() + SUB_FALLBACK_SEC
        grant(users, uid, until)
        seen[iid] = {"uid": uid, "ts": time.time(), "amount": inv.get("amount", "")}
        got = True
        print(f"крипта: счёт {iid} · {uid} · до {_date(until)}")
        tg.send_message(onboard_text(until), token=TOKEN, chat_id=uid,
                        markup=menu_markup(users, uid))
        reward_referrer(users, uid)
    if got:
        save_json(CRYPTO_STATE_PATH, seen)


# ------------------------------------------------------------------ запуск

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

    payment = msg.get("successful_payment")
    if payment:
        until = float(payment.get("subscription_expiration_date") or 0) \
            or time.time() + SUB_FALLBACK_SEC
        grant(users, uid, until)
        print(f"оплата: {uid} · до {_date(until)} · recurring={payment.get('is_recurring')}")
        tg.send_message(onboard_text(until), token=TOKEN, chat_id=uid,
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

    # Инициализация UI компонент
    subbot_ui.init(USERS_PATH, TOKEN)

    users = load_json(USERS_PATH)
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
        ("stats", "Статистика сигналов"),
        ("mycard", "Карточка счёта"),
        ("help", "Помощь"),
    ], token=TOKEN)
    if not ok:
        print("setMyCommands:", info)
    offset = None
    next_remind = 0.0
    next_crypto = 0.0
    try:
        while True:
            updates = tg.get_updates(offset=offset, timeout=POLL_TIMEOUT, token=TOKEN)
            for up in updates:
                offset = up["update_id"] + 1
                try:
                    handle_update(users, state, up)
                except Exception as e:
                    print("ошибка обработки апдейта:", repr(e))
            poll_signals(users, state)
            if time.time() >= next_remind:
                remind_expiring(users)
                next_remind = time.time() + REMIND_EVERY_SEC
            if time.time() >= next_crypto:
                poll_crypto(users)
                next_crypto = time.time() + CRYPTO_POLL_SEC
            if not updates:
                time.sleep(1)
    except KeyboardInterrupt:
        print("остановлено вручную")


if __name__ == "__main__":
    main()
