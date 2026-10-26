"""Автопосты в группу и напоминания владельцу про посты для соцсетей.

Запуск:  python autoposts.py            — планировщик на весь день
         python autoposts.py now plan   — отправить план прямо сейчас (тест)
         python autoposts.py now day    — итог дня
         python autoposts.py now week   — итог недели
         python autoposts.py now promo  — продающий пост
         python autoposts.py now threads— напоминание про пост для Threads
         python autoposts.py now x      — напоминание про пост для X
         python autoposts.py now vk     — напоминание про пост для VK
         python autoposts.py now dzen   — напоминание про пост для Дзена

Что делает:
  * план на день — карточкой в группу (PLAN_TIME);
  * итог дня — карточкой в группу (DAY_SUMMARY_TIME), считается по trades.jsonl,
    который ведёт bot.py при закрытии каждой сделки;
  * итог недели — карточкой в группу по пятницам (WEEK_SUMMARY_TIME);
  * продающий пост — в группу (PROMO_TIME), кнопка на PREMIUM_URL;
  * напоминания о постах для Threads — в личку владельцу (THREADS_REMIND_TIMES);
  * напоминания о постах для X (англ.) — в личку (X_REMIND_TIMES);
  * напоминания о постах для VK и Дзена (RU) — в личку (VK_REMIND_TIMES,
    DZEN_REMIND_TIMES), банк текстов общий с Threads.
    Владелец привязывается командой /threads в premium-боте (owner.json).

Время — в часовом поясе PROMO_TZ. Сигналы сюда не входят: их постит bot.py.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import os
import sys
import time
import urllib.parse
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg
import render as card
import ru_post
import tg
import threads_post
import x_post

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("autoposts")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "renders", "autoposts")
TRADES_PATH = os.path.join(HERE, "trades.jsonl")
OWNER_PATH = os.path.join(HERE, "owner.json")
STATE_PATH = os.path.join(HERE, "autoposts_state.json")

TZ = ZoneInfo(cfg.PROMO_TZ)
# Сколько $ стоит один R: по эталонному счёту из конфига (депозит × риск).
PER_R = cfg.PLAN_DEPOSIT * cfg.PLAN_RISK_PCT / 100.0

MONTHS_GEN = ("января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря")

GREETINGS = ("Доброе утро.", "Утро доброе.", "Снова на связи.")

# Что показываем в утреннем плане (ротация по дню года, чтобы дни не повторялись).
FOCUS = (
    ("золото", "XAUUSD"),
    ("австралиец", "AUDUSD"),
    ("канадец", "USDCAD"),
    ("фунт", "GBPUSD"),
    ("индекс США", "US500"),
    ("европейский индекс", "EUSTX50"),
    ("британский индекс", "UK100"),
    ("немецкий индекс", "DE40"),
    ("японец", "JPN225"),
)


# ------------------------------------------------------------- вспомогательное

def load_json(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    return {}


def save_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def owner_chat() -> str:
    """Куда слать напоминания: OWNER_CHAT_ID из .env или owner.json (команда /threads)."""
    if cfg.OWNER_CHAT_ID:
        return cfg.OWNER_CHAT_ID
    return str(load_json(OWNER_PATH).get("chat_id", "") or "")


def load_trades() -> list[dict]:
    """Журнал закрытых сделок (пишет bot.py): время, R, символ, исход."""
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
    except OSError as e:
        log.error("журнал сделок недоступен: %s", e)
    rows.sort(key=lambda x: x["ts"])
    return rows


def _usd(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")


def period_label(start: dt.date, end: dt.date) -> str:
    if start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS_GEN[start.month - 1]}"
    return (f"{start.day} {MONTHS_GEN[start.month - 1]} – "
            f"{end.day} {MONTHS_GEN[end.month - 1]}")


def _stats(rows: list[dict]) -> tuple[int, int, float]:
    rs = [t["r"] for t in rows]
    return len(rs), sum(1 for x in rs if x > 0), sum(rs)


def _note_for(sum_r: float, n: int) -> str:
    if n == 0:
        return "Сделок не было — день прошёл без входов. Нет реакции — нет сделки."
    if sum_r > 0.001:
        return ("День в плюс. Риск на каждой сделке был ограничен, работал строго по правилам.")
    if sum_r < -0.001:
        return ("Минус, но в пределах лимита. Размер позиции и стоп сделали своё дело — "
                "день не выбил из колеи.")
    return "Ровно в ноль. Такие дни тоже нужны: главное — риск был ограничен."


# ------------------------------------------------------------- посты в группу

def post_plan(now: dt.datetime | None = None) -> bool:
    """Утренний план на день — карточкой в группу."""
    now = now or dt.datetime.now(TZ)
    y = now.timetuple().tm_yday
    a, b = FOCUS[y % len(FOCUS)], FOCUS[(y + 4) % len(FOCUS)]
    cap = os.getenv("MAX_DAILY_STOPS", "3").strip() or "3"
    risk = f"{cfg.PLAN_RISK_PCT:g}".replace(".", ",")
    body = [
        GREETINGS[y % len(GREETINGS)],
        f"Сегодня под наблюдением {a[0]} и {b[0]}: смотрю, где цена у границы "
        f"диапазона и есть ли реакция. Вход — только от уровня.",
        f"Правила на день без изменений: риск {risk}% на сделку, "
        f"не больше {cap} стопов в день. Нет реакции — нет сделки.",
        "Рынок вялый — пропущу день целиком. Пропущенная сделка не стоит ничего, "
        "а сделка без причины — стоит.",
    ]
    os.makedirs(OUT_DIR, exist_ok=True)
    day = now.strftime("%d.%m")
    path = os.path.join(OUT_DIR, f"plan_{now:%Y%m%d}.png")
    try:
        card.render_plan_card(title="План на день", subtitle=day, body=body,
                              out_path=path, when=now)
        ok, info = tg.send_photo(path, f"🌅 <b>План на день · {day}</b>")
    except Exception as e:
        log.error("план: карточка не отрисовалась (%s), шлю текстом", e)
        ok, info = tg.send_message(
            f"🌅 <b>План на день · {day}</b>\n\n" + "\n".join(body))
    log.info("план -> %s (%s)", ok, info)
    return ok


def post_day_summary(now: dt.datetime | None = None) -> bool:
    """Итог дня по журналу закрытых сделок."""
    now = now or dt.datetime.now(TZ)
    start = dt.datetime.combine(now.date(), dt.time.min, TZ)
    end = start + dt.timedelta(days=1)
    rows = load_trades()
    before = [t for t in rows if t["ts"] < start.timestamp()]
    day = [t for t in rows if start.timestamp() <= t["ts"] < end.timestamp()]
    n, wins, sum_r = _stats(day)
    note = _note_for(sum_r, n)
    label = now.strftime("%d.%m")

    if n == 0:
        ok, info = tg.send_message(f"📋 <b>Итог дня · {label}</b>\n\n{note}")
        log.info("итог дня (без сделок) -> %s (%s)", ok, info)
        return ok

    bal_start = cfg.PLAN_DEPOSIT + PER_R * sum(t["r"] for t in before)
    bal_end = bal_start + PER_R * sum_r
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"day_{now:%Y%m%d}.png")
    try:
        card.render_summary_card(card.SummaryPlan(
            title="Итог дня", period=label, trades=n, wins=wins, sum_r=sum_r,
            bal_start=bal_start, bal_end=bal_end, note=note, when=now), path)
        ok, info = tg.send_photo(path, f"📋 <b>Итог дня · {label}</b>\n\n{note}")
    except Exception as e:
        log.error("итог дня: карточка не отрисовалась (%s), шлю текстом", e)
        ok, info = tg.send_message(f"📋 <b>Итог дня · {label}</b>\n\n{note}")
    log.info("итог дня -> %s (%s)", ok, info)
    return ok


def post_week_summary(now: dt.datetime | None = None) -> bool:
    """Итог недели — по пятницам."""
    now = now or dt.datetime.now(TZ)
    monday = dt.datetime.combine(now.date() - dt.timedelta(days=now.weekday()),
                                 dt.time.min, TZ)
    end = monday + dt.timedelta(days=7)
    rows = load_trades()
    before = [t for t in rows if t["ts"] < monday.timestamp()]
    week = [t for t in rows if monday.timestamp() <= t["ts"] < end.timestamp()]
    n, wins, sum_r = _stats(week)
    label = period_label(monday.date(), (monday + dt.timedelta(days=4)).date())

    if n == 0:
        ok, info = tg.send_message(
            f"🏁 <b>Итог недели · {label}</b>\n\nНеделя без сделок — рынок не дал "
            f"входов по правилам. Жду сигнала, а не повода зайти.")
        log.info("итог недели (без сделок) -> %s (%s)", ok, info)
        return ok

    bal_start = cfg.PLAN_DEPOSIT + PER_R * sum(t["r"] for t in before)
    bal_end = bal_start + PER_R * sum_r
    note = (f"С начала ведения счёта (с {_usd(cfg.PLAN_DEPOSIT)} $): "
            f"{(bal_end / cfg.PLAN_DEPOSIT - 1) * 100:+.1f}%")
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"week_{now:%Y%m%d}.png")
    try:
        card.render_summary_card(card.SummaryPlan(
            title="Итог недели", period=label, trades=n, wins=wins, sum_r=sum_r,
            bal_start=bal_start, bal_end=bal_end, note=note, when=now), path)
        ok, info = tg.send_photo(path, f"🏁 <b>Итог недели · {label}</b>\n\n{note}")
    except Exception as e:
        log.error("итог недели: карточка не отрисовалась (%s), шлю текстом", e)
        ok, info = tg.send_message(f"🏁 <b>Итог недели · {label}</b>\n\n{note}")
    log.info("итог недели -> %s (%s)", ok, info)
    return ok


def post_promo() -> bool:
    """Продающий пост с кнопкой на premium-бота."""
    markup = None
    if cfg.PREMIUM_URL:
        markup = {"inline_keyboard": [[{"text": "⚡ Оформить Premium",
                                        "url": cfg.PREMIUM_URL}]]}
    ok, info = tg.send_message(cfg.PROMO_TEXT, markup=markup)
    log.info("промо -> %s (%s)", ok, info)
    return ok


# ------------------------------------------------------- напоминания о Threads

def remind_threads() -> bool:
    """Прислать владельцу в личку следующий неопубликованный пост для Threads."""
    chat = owner_chat()
    if not chat:
        log.warning("нет чата владельца: напиши /threads premium-боту (или задай OWNER_CHAT_ID)")
        return False

    n = threads_post.next_unused()
    if n is None:
        st = load_json(STATE_PATH)
        if not st.get("threads_exhausted"):
            st["threads_exhausted"] = True
            save_json(STATE_PATH, st)
            tg.send_message(
                "📝 Банк постов для Threads закончился — все 28 опубликованы.\n"
                "Пора пополнить: скажи мне, и я добавлю новые.",
                token=cfg.SUB_BOT_TOKEN, chat_id=chat)
        else:
            log.info("банк Threads исчерпан, напоминание уже отправлялось")
        return False

    post = threads_post.POSTS[n - 1]
    text = post["text"]
    url = threads_post.INTENT + urllib.parse.quote(text, safe="")
    cap = (f"📝 <b>Пост для Threads #{n}</b> · {post['title']}\n\n"
           f"{html.escape(text)}\n\n"
           f"— скопируй текст или открой Threads с готовым постом 👇")
    markup = {"inline_keyboard": [[{"text": "Открыть Threads",
                                    "url": url}]]}
    img = threads_post.IMG_DIR / f"{n:02d}.png"
    if img.exists():
        ok, info = tg.send_photo(str(img), cap, token=cfg.SUB_BOT_TOKEN,
                                 chat_id=chat, markup=markup)
    else:
        ok, info = tg.send_message(cap, token=cfg.SUB_BOT_TOKEN, chat_id=chat,
                                   markup=markup)
    if ok:
        threads_post.mark_used(n)
        log.info("напоминание Threads #%d отправлено", n)
    else:
        log.error("напоминание Threads: %s", info)
    return ok


def remind_x() -> bool:
    """Прислать владельцу в личку следующий неопубликованный пост для X (EN)."""
    chat = owner_chat()
    if not chat:
        log.warning("нет чата владельца: напиши /threads premium-боту (или задай OWNER_CHAT_ID)")
        return False

    n = x_post.next_unused()
    if n is None:
        st = load_json(STATE_PATH)
        if not st.get("x_exhausted"):
            st["x_exhausted"] = True
            save_json(STATE_PATH, st)
            tg.send_message(
                "🐦 Банк постов для X закончился — все 28 опубликованы.\n"
                "Пора пополнить: скажи мне, и я добавлю новые.",
                token=cfg.SUB_BOT_TOKEN, chat_id=chat)
        else:
            log.info("банк X исчерпан, напоминание уже отправлялось")
        return False

    post = x_post.POSTS[n - 1]
    text = x_post.tweet_text(n)
    url = x_post.INTENT + urllib.parse.quote(text, safe="")
    cap = (f"🐦 <b>Пост для X #{n}</b> · {post['title']}\n\n"
           f"{html.escape(text)}\n\n"
           f"— скопируй текст или открой X с готовым постом 👇")
    markup = {"inline_keyboard": [[{"text": "Открыть X", "url": url}]]}
    img = x_post.IMG_DIR / f"{n:02d}.png"
    if img.exists():
        ok, info = tg.send_photo(str(img), cap, token=cfg.SUB_BOT_TOKEN,
                                 chat_id=chat, markup=markup)
    else:
        ok, info = tg.send_message(cap, token=cfg.SUB_BOT_TOKEN, chat_id=chat,
                                   markup=markup)
    if ok:
        x_post.mark_used(n)
        log.info("напоминание X #%d отправлено", n)
    else:
        log.error("напоминание X: %s", info)
    return ok


def remind_ru(net: str) -> bool:
    """Прислать владельцу следующий пост для VK/Дзена (RU, банк общий с Threads)."""
    chat = owner_chat()
    if not chat:
        log.warning("нет чата владельца: напиши /threads premium-боту (или задай OWNER_CHAT_ID)")
        return False

    title = ru_post.NETS[net]["title"]
    n = ru_post.next_unused(net)
    if n is None:
        st = load_json(STATE_PATH)
        key = f"{net}_exhausted"
        if not st.get(key):
            st[key] = True
            save_json(STATE_PATH, st)
            tg.send_message(
                f"📮 Банк постов для {title} закончился — все опубликованы.\n"
                f"Пора пополнить: скажи мне, и я добавлю новые.",
                token=cfg.SUB_BOT_TOKEN, chat_id=chat)
        else:
            log.info("банк %s исчерпан, напоминание уже отправлялось", net)
        return False

    post = ru_post.POSTS[n - 1]
    text = post["text"]
    cap = (f"📮 <b>Пост для {title} #{n}</b> · {post['title']}\n\n"
           f"{html.escape(text)}\n\n"
           f"— скопируй текст или открой {title} и вставь из буфера 👇")
    markup = {"inline_keyboard": [[{"text": f"Открыть {title}",
                                    "url": ru_post.NETS[net]["url"]}]]}
    img = ru_post.IMG_DIR / f"{n:02d}.png"
    if img.exists():
        ok, info = tg.send_photo(str(img), cap, token=cfg.SUB_BOT_TOKEN,
                                 chat_id=chat, markup=markup)
    else:
        ok, info = tg.send_message(cap, token=cfg.SUB_BOT_TOKEN, chat_id=chat,
                                   markup=markup)
    if ok:
        ru_post.mark_used(net, n)
        log.info("напоминание %s #%d отправлено", title, n)
    else:
        log.error("напоминание %s: %s", title, info)
    return ok


# ------------------------------------------------------------- планировщик

def _hhmm(raw: str) -> tuple[int, int] | None:
    try:
        hh, mm = (int(x) for x in raw.split(":"))
    except ValueError:
        return None
    if not (0 <= hh < 24 and 0 <= mm < 60):
        return None
    return hh, mm


def _times(raw: str) -> list[str]:
    """«09:00, 14:00» -> ['09:00', '14:00']."""
    return [t.strip() for t in raw.split(",") if t.strip()]


def build_scheduler() -> BackgroundScheduler:
    sch = BackgroundScheduler(timezone=TZ)
    plan = [
        ("plan", cfg.PLAN_TIME, "mon-fri", post_plan),
        ("day", cfg.DAY_SUMMARY_TIME, "mon-fri", post_day_summary),
        ("week", cfg.WEEK_SUMMARY_TIME, "fri", post_week_summary),
    ]
    if cfg.PROMO_ENABLED:
        plan.append(("promo", cfg.PROMO_TIME, "*", post_promo))
    for name, when, dow, job in plan:
        hm = _hhmm(when)
        if not hm:
            log.error("%s: неверное время %r (нужно ЧЧ:ММ)", name, when)
            continue
        sch.add_job(job, CronTrigger(hour=hm[0], minute=hm[1], day_of_week=dow, timezone=TZ),
                    id=name, replace_existing=True)

    if cfg.THREADS_REMIND_ENABLED:
        times = [t.strip() for t in cfg.THREADS_REMIND_TIMES.split(",") if t.strip()]
        for i, when in enumerate(times, 1):
            hm = _hhmm(when)
            if not hm:
                log.error("напоминание Threads: неверное время %r", when)
                continue
            sch.add_job(remind_threads,
                        CronTrigger(hour=hm[0], minute=hm[1], timezone=TZ),
                        id=f"threads_{i}", replace_existing=True)

    if cfg.X_REMIND_ENABLED:
        for i, when in enumerate(_times(cfg.X_REMIND_TIMES), 1):
            hm = _hhmm(when)
            if not hm:
                log.error("напоминание X: неверное время %r", when)
                continue
            sch.add_job(remind_x,
                        CronTrigger(hour=hm[0], minute=hm[1], timezone=TZ),
                        id=f"x_{i}", replace_existing=True)

    if cfg.RU_REMIND_ENABLED:
        for net, raw in (("vk", cfg.VK_REMIND_TIMES), ("dzen", cfg.DZEN_REMIND_TIMES)):
            for i, when in enumerate(_times(raw), 1):
                hm = _hhmm(when)
                if not hm:
                    log.error("напоминание %s: неверное время %r", net, when)
                    continue
                sch.add_job(lambda n=net: remind_ru(n),
                            CronTrigger(hour=hm[0], minute=hm[1], timezone=TZ),
                            id=f"{net}_{i}", replace_existing=True)
    return sch


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "now":
        what = sys.argv[2] if len(sys.argv) > 2 else "plan"
        jobs = {"plan": post_plan, "day": post_day_summary, "week": post_week_summary,
                "promo": post_promo, "threads": remind_threads, "x": remind_x,
                "vk": lambda: remind_ru("vk"), "dzen": lambda: remind_ru("dzen")}
        job = jobs.get(what)
        if not job:
            print("Формат: python autoposts.py now plan|day|week|promo|threads|x|vk|dzen")
            return
        print(f"{what}: {'ок' if job() else 'не отправлено'}")
        return

    sch = build_scheduler()
    jobs = sch.get_jobs()
    if not jobs:
        print("Нечего планировать: проверь PLAN_TIME, PROMO_TIME и THREADS_REMIND_TIMES в .env")
        return
    sch.start()
    print(f"autoposts запущен · пояс {cfg.PROMO_TZ}")
    for j in sorted(jobs, key=lambda x: x.id):
        print(f"  {j.id:<10} {j.trigger}")
    if not owner_chat():
        print("! нет чата владельца — напиши /threads premium-боту, иначе напоминания "
              "о постах для Threads, X, VK и Дзена не придут")
    try:
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        sch.shutdown(wait=False)
        print("остановлено вручную")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
