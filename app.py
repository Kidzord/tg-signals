"""Сервис сигналов: вебхук с алертом → карточка → Telegram + дневной пост.

Свечи для карточки берутся из MetaTrader 5 (см. feeds.py).

Запуск:
    python app.py            — поднять сервер
    python app.py demo       — отрисовать пример карточки без сети
    python app.py selftest   — проверить разбор алертов
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

# Скрипт может запускаться из произвольной папки: добавляем свою в путь импорта,
# иначе локальные модули (config, feeds, render, tg) не находятся.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cache
import config as cfg
import feeds
import health
import logger_config
import rate_limit
import render as card
import tg
import voice

# Инициализируем логирование
logger_config.setup_logging(
    level=cfg.LOG_LEVEL,
    json_logs=cfg.LOG_JSON,
    log_file=cfg.LOG_FILE if cfg.LOG_FILE else None
)

log = logger_config.get_logger("app")

MSK = ZoneInfo(cfg.DISPLAY_TZ)
NUM = r"[-+]?\d+(?:[.,]\d+)?"

_seen: dict[str, float] = {}


# ------------------------------------------------------------------ разбор алерта

def _numbers(text: str) -> list[float]:
    return [float(x.replace(",", ".")) for x in re.findall(NUM, text)]


def _labelled(text: str, keys: tuple[str, ...]) -> float | None:
    for k in keys:
        m = re.search(rf"{k}\s*[:=]?\s*({NUM})", text, re.IGNORECASE)
        if m:
            return float(m.group(1).replace(",", "."))
    return None


def parse_alert(raw: str) -> card.Signal | None:
    """Понимает и текст из alert(), и JSON. Формат текста — как в индикаторе."""
    raw = (raw or "").strip()
    if not raw:
        return None

    if raw.startswith("{"):
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            d = None
        if isinstance(d, dict):
            side = str(d.get("side") or d.get("action") or "").upper()
            if side in ("LONG", "SHORT"):
                try:
                    return card.Signal(
                        side=side,
                        symbol=str(d.get("symbol") or d.get("ticker") or "XAUUSD"),
                        tf=str(d.get("tf") or d.get("timeframe") or "15"),
                        entry=float(d["entry"]), sl=float(d["sl"]),
                        tp1=float(d["tp1"]), tp2=float(d["tp2"]), tp3=float(d["tp3"]),
                        when=dt.datetime.now(MSK),
                    )
                except (KeyError, TypeError, ValueError):
                    return None
            return None

    m = re.match(r"^\s*(LONG|SHORT)\s+(\S+)\s+(\S+)\s+(.*)$", raw, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    side = m.group(1).upper()
    symbol, tf, rest = m.group(2), m.group(3), m.group(4)

    entry = _labelled(rest, ("вход", "entry", "цена"))
    sl = _labelled(rest, ("sl", "стоп", "stop"))
    tp1 = _labelled(rest, ("tp1", "тп1", "тейк 1"))
    tp2 = _labelled(rest, ("tp2", "тп2", "тейк 2"))
    tp3 = _labelled(rest, ("tp3", "тп3", "тейк 3"))

    if None in (entry, sl, tp1, tp2, tp3):
        nums = _numbers(rest)
        if len(nums) < 5:
            return None
        entry, sl, tp1, tp2, tp3 = nums[:5]

    if not entry or entry <= 0 or abs(entry - sl) <= 0:
        return None

    return card.Signal(side=side, symbol=symbol.upper(), tf=tf.upper(),
                       entry=entry, sl=sl, tp1=tp1, tp2=tp2, tp3=tp3,
                       when=dt.datetime.now(MSK))


def symbol_allowed(symbol: str) -> bool:
    if not cfg.SYMBOL_FILTER:
        return True
    up = symbol.upper()
    return any(f in up for f in cfg.SYMBOL_FILTER)


# ------------------------------------------------------------------ отправка

def publish(sig: card.Signal) -> tuple[bool, str]:
    bars, source, is_demo = feeds.load_bars(sig.symbol, sig.tf)
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "renders")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"signal_{sig.when:%Y%m%d_%H%M%S}.png")
    card.render_card(sig, bars, source, is_demo, path)
    ok, info = tg.send_photo(path, voice.signal_caption(sig, source=source))
    log.info("отправка %s %s %s -> %s (%s)", sig.side, sig.symbol, sig.entry, ok, info)
    return ok, path


def _dedup_key(sig: card.Signal) -> str:
    return f"{sig.symbol}|{sig.side}|{sig.entry:.5f}|{sig.tp1:.5f}"


def _is_duplicate(key: str) -> bool:
    now = time.time()
    for k, ts in list(_seen.items()):
        if now - ts > cfg.DEDUP_TTL_SEC:
            _seen.pop(k, None)
    if key in _seen:
        return True
    _seen[key] = now
    return False


# ------------------------------------------------------------------ планировщик

def promo_job() -> None:
    log.info("дневной пост")
    ok, info = tg.send_message(cfg.PROMO_TEXT)
    log.info("дневной пост -> %s (%s)", ok, info)


def start_scheduler() -> BackgroundScheduler | None:
    sch = BackgroundScheduler(timezone=ZoneInfo(cfg.PROMO_TZ))

    # Дневной пост
    if cfg.PROMO_ENABLED:
        try:
            hh, mm = (int(x) for x in cfg.PROMO_TIME.split(":"))
        except ValueError:
            log.error("PROMO_TIME должен быть в формате ЧЧ:ММ, получено %r", cfg.PROMO_TIME)
        else:
            sch.add_job(promo_job, CronTrigger(hour=hh, minute=mm, timezone=ZoneInfo(cfg.PROMO_TZ)),
                        id="promo", replace_existing=True)
            log.info("дневной пост запланирован на %02d:%02d %s", hh, mm, cfg.PROMO_TZ)

    # Периодическая очистка кеша и rate limit (каждые 5 минут)
    def cleanup_job():
        cache_cleaned = cache.get_cache().cleanup()
        rate_cleaned = rate_limit.cleanup()
        log.debug(f"cleanup: cache={cache_cleaned}, rate_limit={rate_cleaned}")

    sch.add_job(cleanup_job, "interval", minutes=5, id="cleanup", replace_existing=True)

    # Периодический health check (каждую минуту)
    def health_check_job():
        summary = health.monitor.run_all()
        if summary["status"] == "unhealthy":
            log.error(f"Health check failed: {summary}")
        elif summary["status"] == "degraded":
            log.warning(f"Health check degraded: {summary}")

    sch.add_job(health_check_job, "interval", minutes=1, id="health", replace_existing=True)

    sch.start()
    log.info("scheduler запущен")
    return sch


# ------------------------------------------------------------------ FastAPI

_scheduler: BackgroundScheduler | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _scheduler
    _scheduler = start_scheduler()
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)


app = FastAPI(title="KidzordFX signals", lifespan=lifespan)


@app.get("/health")
async def health():
    """Базовый health check для совместимости."""
    return {"ok": True, "symbols": cfg.SYMBOL_FILTER or "all",
            "promo": cfg.PROMO_ENABLED, "tg": bool(cfg.BOT_TOKEN and cfg.CHAT_ID)}


@app.get("/health/detailed")
async def health_detailed():
    """Детальный health check с информацией о всех компонентах."""
    summary = health.monitor.run_all()
    return summary


@app.get("/metrics")
async def metrics():
    """Метрики приложения."""
    return logger_config.metrics.get_all_metrics()


@app.post("/tv/{secret}")
async def tradingview(secret: str, request: Request):
    if secret != cfg.WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")

    # Rate limiting по IP
    client_ip = request.client.host if request.client else "unknown"
    allowed, retry_after = rate_limit.check_webhook(client_ip)
    if not allowed:
        logger_config.metrics.increment("webhook_rate_limited")
        log.warning(f"Rate limited for {client_ip}, retry after {retry_after}s")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limited, retry after {retry_after}s",
            headers={"Retry-After": str(retry_after)}
        )

    raw = (await request.body()).decode("utf-8", "replace")
    log.info("вебхук: %s", raw[:300].replace("\n", " "))

    sig = parse_alert(raw)
    if not sig:
        logger_config.metrics.increment("webhook_parse_failed")
        return {"ok": False, "reason": "не разобран payload"}
    if not symbol_allowed(sig.symbol):
        logger_config.metrics.increment("webhook_symbol_filtered")
        return {"ok": False, "reason": f"символ {sig.symbol} отфильтрован"}
    key = _dedup_key(sig)
    if _is_duplicate(key):
        logger_config.metrics.increment("webhook_duplicate")
        return {"ok": False, "reason": "дубликат"}

    logger_config.metrics.increment("webhook_processed")
    start = time.time()
    ok, path = await run_in_threadpool(publish, sig)
    duration = time.time() - start
    logger_config.metrics.observe("webhook_duration", duration)

    if ok:
        logger_config.metrics.increment("webhook_success")
    else:
        logger_config.metrics.increment("webhook_failed")

    return {"ok": ok, "file": os.path.basename(path)}


# ------------------------------------------------------------------ локальные режимы

def make_demo() -> str:
    bars = feeds.demo_bars(120, "15")
    entry = round(bars[-1].c, 2)
    risk = round(entry * 0.0028, 2)
    sig = card.Signal(side="LONG", symbol="XAUUSD", tf="15",
                      entry=entry, sl=round(entry - risk, 2),
                      tp1=round(entry + risk, 2), tp2=round(entry + 2 * risk, 2),
                      tp3=round(entry + 3 * risk, 2),
                      when=dt.datetime.now(MSK))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples", "sample_signal.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    card.render_card(sig, bars, "демо-данные", True, out)
    return out


def selftest() -> None:
    samples = [
        "LONG XAUUSD 15 | вход 4000.12 | SL 3988.72 | TP1 4011.52 | TP2 4022.92 | TP3 4034.32",
        "SHORT GOLD.r 60 | вход 4012,50 | SL 4024,10 | TP1 4000,90 | TP2 3989,30 | TP3 3977,70",
        '{"side":"LONG","symbol":"XAUUSD","tf":"240","entry":3995.5,"sl":3980.0,"tp1":4011.0,"tp2":4026.5,"tp3":4042.0}',
        "мусор без структуры",
    ]
    for s in samples:
        sig = parse_alert(s)
        if sig:
            print(f"OK   {sig.side:5} {sig.symbol:8} {sig.tf:4} entry={sig.entry} sl={sig.sl} "
                  f"tp=({sig.tp1},{sig.tp2},{sig.tp3}) risk={sig.risk_pct:.2f}%")
        else:
            print(f"SKIP {s[:60]!r}")


def promo_now() -> None:
    ok, info = tg.send_message(cfg.PROMO_TEXT)
    print(f"отправлено={ok} ответ={info}")


def preview_caption() -> None:
    bars = feeds.demo_bars(120, "15")
    entry = round(bars[-1].c, 2)
    risk = round(entry * 0.0028, 2)
    sig = card.Signal(side="LONG", symbol="XAUUSD", tf="15",
                      entry=entry, sl=round(entry - risk, 2),
                      tp1=round(entry + risk, 2), tp2=round(entry + 2 * risk, 2),
                      tp3=round(entry + 3 * risk, 2), when=dt.datetime.now(MSK))
    cap = voice.signal_caption(sig, source="MT5")
    print(cap)
    print("\nдлина:", len(cap), "символов (лимит Telegram — 1024)")


def _write_env(key: str, value: str) -> None:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    lines = open(path, encoding="utf-8").read().splitlines()
    out, found = [], False
    for ln in lines:
        if ln.startswith(key + "="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"{key}={value}")
    open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")


def find_chat() -> None:
    """Ищет чаты, где уже есть бот, и записывает единственную группу в .env."""
    import requests

    if not cfg.BOT_TOKEN:
        print("в .env нет TG_BOT_TOKEN")
        return
    try:
        r = requests.get(f"https://api.telegram.org/bot{cfg.BOT_TOKEN}/getUpdates", timeout=30).json()
    except Exception as e:
        print("нет связи с Telegram:", e)
        return

    chats: dict[int, tuple[str, str]] = {}
    for u in r.get("result", []):
        for key in ("message", "channel_post", "my_chat_member", "edited_message"):
            m = u.get(key)
            if m and "chat" in m:
                c = m["chat"]
                chats[c["id"]] = (c.get("type", "?"),
                                  c.get("title") or c.get("username") or c.get("first_name") or "")

    if not chats:
        print("чатов не видно. Добавь бота в группу админом и напиши в группе любое сообщение.")
        return

    for cid, (typ, name) in chats.items():
        print(f"{cid:>16}  {typ:<11} {name}")

    groups = {k: v for k, v in chats.items() if v[0] in ("group", "supergroup", "channel")}
    if len(groups) == 1:
        cid = next(iter(groups))
        _write_env("TG_CHAT_ID", str(cid))
        print(f"\nзаписал TG_CHAT_ID={cid} в .env")
    elif len(groups) > 1:
        print("\nгрупп несколько — впиши нужный id в TG_CHAT_ID вручную")
    else:
        print("\nгрупп/каналов нет, есть только личный чат » можно тестировать в нём")


def send_test() -> None:
    ok, info = tg.send_message("✅ <b>KidzordFx</b>: сервис сигналов подключён.")
    print(f"отправлено={ok} ответ={info}")


def print_url() -> None:
    host = sys.argv[2] if len(sys.argv) > 2 else f"http://localhost:{cfg.PORT}"
    print(f"{host.rstrip('/')}/tv/{cfg.WEBHOOK_SECRET}")


def mt5_check() -> None:
    """Проверка связки Python ↔ терминал MT5. Только чтение, без торговых операций."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("пакет не установлен. Выполни:  python -m pip install MetaTrader5")
        return

    if not mt5.initialize(timeout=15000):
        print("терминал не отвечает:", mt5.last_error())
        print("проверь: MT5 запущен, залогинен, Python и терминал на одной машине")
        return

    try:
        ti = mt5.terminal_info()
        ai = mt5.account_info()
        print(f"терминал: {ti.name}  build {ti.build}  connected={ti.connected}  "
              f"trade_allowed={ti.trade_allowed}")
        if ai:
            print(f"счёт: ****{str(ai.login)[-4:]}  сервер {ai.server}  {ai.currency}  "
                  f"баланс {ai.balance:.2f}  тип {'демо' if ai.trade_mode == 0 else 'реальный'}")
        else:
            print("счёт: терминал не залогинен")

        names = [s.name for s in (mt5.symbols_get() or [])]
        gold = [n for n in names if "XAU" in n.upper() or "GOLD" in n.upper()]
        print(f"символов всего: {len(names)}, из них золото: {gold[:8] or 'не найдено'}")

        probe = (gold or ["XAUUSD"])[0]
        mt5.symbol_select(probe, True)
        rates = mt5.copy_rates_from_pos(probe, mt5.TIMEFRAME_M15, 0, 5)
        if rates is None or len(rates) == 0:
            print(f"{probe}: бары не получены", mt5.last_error())
            return
        print(f"{probe} M15, последние {len(rates)} баров:")
        for r in rates[-3:]:
            print(f"  {dt.datetime.fromtimestamp(int(r['time']))}  "
                  f"O{r['open']} H{r['high']} L{r['low']} C{r['close']}")
        print("\nвсё работает: карточки будут на реальных данных брокера")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "demo":
        print(make_demo())
    elif cmd == "selftest":
        selftest()
    elif cmd == "preview":
        preview_caption()
    elif cmd == "promo":
        promo_now()
    elif cmd == "findchat":
        find_chat()
    elif cmd == "test":
        send_test()
    elif cmd == "url":
        print_url()
    elif cmd == "mt5test":
        mt5_check()
    else:
        import uvicorn
        uvicorn.run("app:app", host=cfg.HOST, port=cfg.PORT, reload=False)
