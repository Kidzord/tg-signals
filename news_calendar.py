"""Экономический календарь: входы не открываются вокруг красных новостей.

Источник — публичный JSON ForexFactory (ff_calendar_thisweek/nextweek).
События кэшируются в news_cache.json: сеть дёргается не чаще раза в
NEWS_CACHE_TTL_H часов, при сбое используется прошлый кэш (fail-open:
бот не должен замолчать из-за недоступного календаря).

Запуск:  python news_calendar.py            # ближайшие красные события
         python news_calendar.py AUDUSD     # + проверка окна для символа
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import threading
import time
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "news_cache.json")
URLS = (
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
)
TTL_SEC = float(getattr(cfg, "NEWS_CACHE_TTL_H", 6.0)) * 3600.0
RETRY_SEC = 15 * 60          # после сетевой ошибки не долбим источник чаще
BEFORE_SEC = max(0, int(getattr(cfg, "NEWS_BLOCK_BEFORE_MIN", 30))) * 60
AFTER_SEC = max(0, int(getattr(cfg, "NEWS_BLOCK_AFTER_MIN", 15))) * 60
ENABLED = bool(getattr(cfg, "NEWS_FILTER_ENABLED", True))

CURRENCIES = {"USD", "EUR", "GBP", "AUD", "CAD", "JPY", "CNY", "CHF", "NZD"}
# Для индексов и металлов валюта события не всегда выводится из тикера.
FIXED = {
    "XAUUSD": {"USD"}, "XAGUSD": {"USD"},
    "US500": {"USD"}, "US30": {"USD"}, "NAS100": {"USD"}, "USTEC": {"USD"},
    "EUSTX50": {"EUR"}, "DE40": {"EUR"}, "UK100": {"GBP"}, "JPN225": {"JPY"},
    "AUDUSD": {"AUD", "USD"}, "USDCAD": {"USD", "CAD"},
    "GBPUSD": {"GBP", "USD"}, "USDJPY": {"USD", "JPY"},
}


def _tz() -> dt.tzinfo:
    try:
        return ZoneInfo(cfg.DISPLAY_TZ)
    except Exception:
        return dt.timezone.utc


def currencies_for(symbol: str) -> set[str]:
    # отрезаем брокерский суффикс (.r, _i, -ECN), но цифры индексов сохраняем
    raw = str(symbol).upper().strip()
    base = raw.split(".")[0].split("-")[0].split("_")[0]
    compact = "".join(ch for ch in base if ch.isalnum())
    if compact in FIXED:
        return set(FIXED[compact])
    if compact.startswith(("XAU", "GOLD", "XAG", "SILVER")):
        return {"USD"}
    if compact.startswith(("US500", "US30", "NAS100", "USTEC")):
        return {"USD"}
    letters = "".join(ch for ch in compact if ch.isalpha())
    if len(letters) >= 6:
        base_ccy, quote = letters[-6:-3], letters[-3:]
        found = {c for c in (base_ccy, quote) if c in CURRENCIES}
        if found:
            return found
    return set()


def _normalize(raw: dict) -> dict | None:
    title = str(raw.get("title") or "").strip()
    country = str(raw.get("country") or "").strip().upper()
    impact = str(raw.get("impact") or "").strip().lower()
    date_raw = str(raw.get("date") or "").strip()
    if not (title and country and date_raw):
        return None
    try:
        when = dt.datetime.fromisoformat(date_raw)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return {
        "title": title,
        "country": country,
        "impact": impact,
        "ts": when.timestamp(),
        "date_raw": date_raw,
        "forecast": str(raw.get("forecast") or ""),
        "previous": str(raw.get("previous") or ""),
    }


def fetch_events() -> list[dict]:
    """Скачать обе недели. Исключение — только если не ответил ни один URL."""
    out: list[dict] = []
    errors: list[str] = []
    for url in URLS:
        try:
            r = requests.get(url, timeout=20,
                             headers={"User-Agent": "tg-signals/1.0"})
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            errors.append(f"{url}: {e}")
            continue
        if not isinstance(data, list):
            errors.append(f"{url}: не список")
            continue
        for raw in data:
            if isinstance(raw, dict):
                ev = _normalize(raw)
                if ev:
                    out.append(ev)
    if not out and errors:
        raise RuntimeError("; ".join(errors[:2]))
    return out


def _read_cache() -> dict:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_cache(data: dict) -> None:
    # три bot.py могут обновлять кэш одновременно — у каждого свой tmp-файл
    tmp = CACHE_PATH + f".tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, CACHE_PATH)


def _dedupe(events: list[dict]) -> list[dict]:
    seen: set[tuple[str, float, str]] = set()
    out: list[dict] = []
    for ev in events:
        try:
            key = (str(ev.get("country")), float(ev.get("ts")),
                   str(ev.get("title")))
        except (TypeError, ValueError):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    out.sort(key=lambda e: float(e.get("ts") or 0))
    return out


def load_events(now_ts: float | None = None, force: bool = False,
                allow_fetch: bool = True) -> list[dict]:
    """Свежий кэш или сеть; при сбое — прошлый кэш (может быть пустым).

    allow_fetch=False — только кэш. Так bot.py проверяет окно в момент
    сигнала, не блокируя публикацию сетевым запросом; обновление кэша
    крутится в фоновом потоке.
    """
    if not ENABLED:
        return []
    now = time.time() if now_ts is None else now_ts
    cache = _read_cache()
    events = _dedupe([e for e in cache.get("events") or [] if isinstance(e, dict)])
    if not allow_fetch:
        return events
    fetched_at = float(cache.get("fetched_at") or 0)
    attempt_at = float(cache.get("attempt_at") or 0)
    if not force:
        if now - fetched_at < TTL_SEC:
            return events
        if now - attempt_at < RETRY_SEC:
            return events
    try:
        fresh = _dedupe(fetch_events())
    except Exception as e:
        cache["attempt_at"] = now
        cache["error"] = str(e)[:300]
        _write_cache(cache)
        return events
    _write_cache({"fetched_at": now, "attempt_at": now, "events": fresh})
    return fresh


def blackout(symbol: str, now_ts: float | None = None,
             events: list[dict] | None = None,
             allow_fetch: bool = True) -> tuple[bool, dict | None]:
    """(заблокировано, событие): красное событие валюты символа рядом с now."""
    if not ENABLED:
        return False, None
    now = time.time() if now_ts is None else now_ts
    if events is None:
        events = load_events(now, allow_fetch=allow_fetch)
    currencies = currencies_for(symbol)
    if not currencies or not events:
        return False, None
    nearest: dict | None = None
    for ev in events:
        if str(ev.get("impact") or "").lower() != "high":
            continue
        if str(ev.get("country") or "").upper() not in currencies:
            continue
        try:
            ts = float(ev.get("ts"))
        except (TypeError, ValueError):
            continue
        if ts - BEFORE_SEC <= now <= ts + AFTER_SEC:
            if nearest is None or abs(ts - now) < abs(float(nearest["ts"]) - now):
                nearest = ev
    return nearest is not None, nearest


_refresh_started = False


def start_background_refresh(interval_sec: float = 3600.0) -> None:
    """Обновлять кэш в фоне, чтобы сигнал не ждал сеть."""
    global _refresh_started
    if _refresh_started or not ENABLED:
        return
    _refresh_started = True

    def worker() -> None:
        while True:
            try:
                load_events(force=True)
            except Exception:
                pass
            time.sleep(max(300.0, interval_sec))

    threading.Thread(target=worker, name="news-refresh", daemon=True).start()


def describe(ev: dict) -> str:
    when = dt.datetime.fromtimestamp(float(ev["ts"]), _tz()).strftime("%d.%m %H:%M")
    return f"{ev['country']} {ev['title']} ({when} {cfg.TZ_LABEL})"


def status_text(now_ts: float | None = None, symbol: str | None = None) -> str:
    now = time.time() if now_ts is None else now_ts
    events = load_events(now)
    high = [e for e in events if str(e.get("impact")) == "high"
            and float(e.get("ts") or 0) >= now - 3600]
    lines = [f"Календарь: событий {len(events)}, красных впереди {len(high)}"
             f" ({'вкл' if ENABLED else 'выкл'}, окно −{BEFORE_SEC // 60}/"
             f"+{AFTER_SEC // 60} мин)"]
    for ev in high[:10]:
        lines.append(f"  {describe(ev)}")
    if symbol:
        blocked, ev = blackout(symbol, now, events)
        if blocked and ev:
            lines.append(f"⛔ {symbol}: сейчас новостное окно — {describe(ev)}")
        else:
            lines.append(f"✅ {symbol}: новостного окна сейчас нет "
                         f"(валюты: {', '.join(sorted(currencies_for(symbol))) or '?'})")
    return "\n".join(lines)


def main() -> int:
    symbol = sys.argv[1].strip().upper() if len(sys.argv) > 1 else None
    print(status_text(symbol=symbol))
    return 0


if __name__ == "__main__":
    sys.exit(main())
