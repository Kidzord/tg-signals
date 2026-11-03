"""Консенсус внешних источников: внутренний фильтр, а не ретрансляция.

Бот не читает чужие каналы: Bot API видит только каналы, где бот админ, а
userbot по MTProto — нарушение правил Telegram. Поэтому сигналы попадают сюда
только авторизованно и вручную:

    python consensus.py add --source "@channel" --text "XAUUSD BUY 4020 SL 4005"
    python consensus.py ingest signals.json       # список или {"signals": [...]}

или POST /consensus/{secret} в app.py. Внутрь сохраняется только структура
(символ, сторона, уровни, источник) — чужие тексты не публикуются и не
пересылаются ни в канал, ни подписчикам.

bot.py перед публикацией своего сигнала спрашивает precheck(): если в окне
CONSENSUS_WINDOW_MIN достаточно независимых источников голосуют против,
вход пропускается (CONSENSUS_MODE=filter). Модуль fail-open: любая ошибка
чтения/разбора возвращает «пропускать», бот не должен замолчать из-за фильтра.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, "consensus_signals.jsonl")
DEDUP_SEC = 300.0

# --- нормализация символа -------------------------------------------------

CCY = {
    "USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "CNY",
    "SEK", "NOK", "DKK", "MXN", "ZAR", "TRY", "RUB",
    "XAU", "XAG", "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE",
}

ALIASES = {
    "GOLD": "XAUUSD", "ЗОЛОТО": "XAUUSD", "XAU": "XAUUSD", "XAUUSD": "XAUUSD",
    "SILVER": "XAGUSD", "СЕРЕБРО": "XAGUSD", "XAG": "XAGUSD", "XAGUSD": "XAGUSD",
    "BITCOIN": "BTCUSD", "БИТКОИН": "BTCUSD", "BTC": "BTCUSD", "BTCUSD": "BTCUSD",
    "ETHEREUM": "ETHUSD", "ЭФИРИУМ": "ETHUSD", "ETH": "ETHUSD", "ETHUSD": "ETHUSD",
    "SP500": "US500", "S&P500": "US500", "US500": "US500",
    "US30": "US30", "DOW": "US30", "DJI": "US30",
    "NAS100": "NAS100", "USTEC": "NAS100", "NASDAQ": "NAS100", "US100": "NAS100",
    "DE40": "DE40", "GER40": "DE40", "DAX": "DE40",
    "UK100": "UK100", "FTSE": "UK100",
    "JPN225": "JPN225", "NIKKEI": "JPN225",
}

SIX_RE = re.compile(r"\b([A-Z]{6})\b")
PAIR_RE = re.compile(r"\b([A-Z]{3})\s*[/\-_.]\s*([A-Z]{3})\b")


def normalize_symbol(raw: str | None) -> str | None:
    """Приводит «золото», «XAU/USD», «BTCUSDT» к единому тикеру."""
    if not raw:
        return None
    text = str(raw).strip().upper().replace(" ", "")
    if not text:
        return None
    if text in ALIASES:
        return ALIASES[text]
    m = PAIR_RE.fullmatch(text)
    if m:
        text = m.group(1) + m.group(2)
    if text in ALIASES:
        return ALIASES[text]
    # крипто-пары с USDT торгуются как USD-инструмент
    if text.endswith("USDT") and text[:-4] in CCY:
        return text[:-4] + "USD"
    if len(text) == 6 and text[:3] in CCY and text[3:] in CCY:
        return text
    return None


# --- разбор текста --------------------------------------------------------

LONG_RE = re.compile(
    r"(?:\b(?:BUY|LONG|BULLISH?|ПОКУП\w*|КУПИ\w*|ЛОНГ\w*|ВВЕРХ|РОСТ)\b|🟢|🔵|⬆|↗)",
    re.IGNORECASE,
)
SHORT_RE = re.compile(
    r"(?:\b(?:SELL|SHORT|BEARISH?|ПРОДА\w*|ПРОДАЖ\w*|ШОРТ\w*|СЕЛЛ\w*|ВНИЗ)\b|🔴|🔻|⬇|↘)",
    re.IGNORECASE,
)
NUM = r"[-+]?\d+(?:[.,]\d+)?"


def normalize_side(raw: str | None) -> str | None:
    if not raw:
        return None
    up = str(raw).strip().upper()
    if up in ("LONG", "BUY", "B", "ЛОНГ", "ПОКУПКА", "ПОКУПАТЬ"):
        return "LONG"
    if up in ("SHORT", "SELL", "S", "ШОРТ", "ПРОДАЖА", "ПРОДАВАТЬ"):
        return "SHORT"
    if LONG_RE.search(up) and not SHORT_RE.search(up):
        return "LONG"
    if SHORT_RE.search(up) and not LONG_RE.search(up):
        return "SHORT"
    return None


def _number(text: str) -> float | None:
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def _labelled(text: str, keys: tuple[str, ...], *, all_matches: bool = False):
    """Ищет число после метки: «SL 4005», «стоп-лосс: 4005», «TP1 4050»."""
    alt = "|".join(keys)
    pat = re.compile(
        rf"(?:{alt})\s*(?:[:=№\-])?\s*({NUM})", re.IGNORECASE
    )
    found = pattern_all(pat, text) if all_matches else None
    if all_matches:
        return found or []
    m = pat.search(text)
    return _number(m.group(1)) if m else None


def pattern_all(pat: re.Pattern, text: str) -> list[float]:
    out = []
    for m in pat.finditer(text):
        val = _number(m.group(1))
        if val is not None:
            out.append(val)
    return out


SL_KEYS = ("SL", "STOP", "STOPLOSS", "STOP-LOSS", "СТОП", "СТОП-ЛОСС", "СТОПЛОСС")
TP_KEYS = ("TP1", "TP2", "TP3", "TP", "TAKE", "TAKEPROFIT", "ТЕЙК", "ТЕЙК-ПРОФИТ", "ЦЕЛЬ")
ENTRY_KEYS = ("ENTRY", "ВХОД", "ОТКРЫТИЕ", "ЦЕНА ВХОДА", "PRICE")


def find_symbol(text: str) -> str | None:
    """Публичная обёртка: тикер из произвольного текста (OCR, заметка)."""
    return _find_symbol(str(text)) if text else None


def parse_external(text: str, source: str, ts: float | None = None,
                   side_override: str | None = None) -> dict | None:
    """Разбирает присланный текст в структуру. Возвращает None, если не сигнал.

    side_override нужен, когда сторону подтвердил человек, а в тексте её нет
    (например, OCR скриншота прочитал тикер, но направление не распознал).
    """
    if not text or not source:
        return None
    raw = str(text)
    symbol = _find_symbol(raw)
    side = normalize_side(side_override) or normalize_side(raw)
    if not symbol or not side:
        return None
    tps = _labelled(raw, TP_KEYS, all_matches=True)
    entry = _labelled(raw, ENTRY_KEYS) or _entry_after_side(raw)
    return {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "sl": _labelled(raw, SL_KEYS),
        "tp": tps[0] if tps else None,
        "tps": tps,
        "source": str(source).strip(),
        "url": "",
        "ts": float(ts if ts is not None else time.time()),
        "added": time.time(),
        "raw": re.sub(r"\s+", " ", raw).strip()[:300],
    }


SIDE_PRICE_RE = re.compile(
    rf"(?:BUY|SELL|LONG|SHORT|ПОКУП\w*|ПРОДА\w*|ЛОНГ\w*|ШОРТ\w*)"
    rf"(?:\s*(?:LIMIT|STOP|MARKET|AT|ПО|@))*\s*[:=]?\s*({NUM})",
    re.IGNORECASE,
)


def _entry_after_side(text: str) -> float | None:
    """«BUY 4020» / «продаём по 3980» — цена сразу за стороной сделки."""
    m = SIDE_PRICE_RE.search(text)
    return _number(m.group(1)) if m else None


def _symbol_from_word(word: str) -> str | None:
    """Алиас слова с учётом русских окончаний: «золоту» → XAUUSD."""
    up = word.upper()
    if up in ALIASES:
        return ALIASES[up]
    if any("а" <= ch <= "я" or ch == "ё" for ch in word.lower()):
        for alias, sym in ALIASES.items():
            if alias.isascii() or len(alias) < 4:
                continue
            if up.startswith(alias[:5]):
                return sym
    return None


def _find_symbol(text: str) -> str | None:
    # 1) явные алиасы словом (золото, gold, bitcoin, dax) — самый надёжный путь
    for word in re.findall(r"[A-Za-zА-Яа-яЁё&]{2,}", text):
        sym = _symbol_from_word(word)
        if sym:
            return sym
    # 2) пары через разделитель: EUR/USD, XAU-USD
    for m in PAIR_RE.finditer(text.upper()):
        sym = normalize_symbol(m.group(1) + m.group(2))
        if sym:
            return sym
    # 3) слитные тикеры из двух валют: EURUSD, XAUUSD (6 букв, а не «SIGNAL»)
    for m in SIX_RE.finditer(text.upper()):
        token = m.group(1)
        if token[:3] in CCY and token[3:] in CCY:
            return token
    return None


# --- хранилище ------------------------------------------------------------

def path() -> str:
    return getattr(cfg, "CONSENSUS_PATH", "") or DEFAULT_PATH


def _load(since_ts: float = 0.0, *, file_path: str | None = None) -> list[dict]:
    target = file_path or path()
    rows: list[dict] = []
    if not os.path.exists(target):
        return rows
    with open(target, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                ts = float(row.get("ts", 0.0))
            except (ValueError, TypeError):
                continue
            if ts >= since_ts:
                rows.append(row)
    return rows


def _append(rows: list[dict], *, file_path: str | None = None) -> int:
    if not rows:
        return 0
    target = file_path or path()
    os.makedirs(os.path.dirname(os.path.abspath(target)) or ".", exist_ok=True)
    with open(target, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def _recent_duplicate(row: dict, existing: list[dict]) -> bool:
    for old in existing:
        if (old.get("symbol") != row.get("symbol")
                or old.get("side") != row.get("side")
                or str(old.get("source", "")).lower() != str(row.get("source", "")).lower()):
            continue
        if abs(float(old.get("ts", 0.0)) - float(row.get("ts", 0.0))) <= DEDUP_SEC:
            return True
    return False


def add_payload(item: dict, now_ts: float | None = None) -> dict | None:
    """Принимает {source, text} или структурированный {source, symbol, side, ...}.

    Возвращает сохранённую запись или None, если это не сигнал/дубликат.
    Ошибка формата — ValueError: тихо проглатывать ввод оператора нельзя.
    """
    if not isinstance(item, dict):
        raise ValueError("элемент должен быть объектом")
    source = str(item.get("source", "")).strip()
    if not source:
        raise ValueError("source обязателен: без источника сигнал не учитывается")
    now = time.time() if now_ts is None else float(now_ts)

    text = item.get("text") or item.get("raw") or ""
    if text:
        row = parse_external(str(text), source, ts=item.get("ts"))
    else:
        symbol = normalize_symbol(item.get("symbol"))
        side = normalize_side(item.get("side"))
        if not symbol or not side:
            raise ValueError("нужны symbol и side (или text для разбора)")
        row = {
            "symbol": symbol,
            "side": side,
            "entry": _as_float(item.get("entry")),
            "sl": _as_float(item.get("sl")),
            "tp": _as_float(item.get("tp")),
            "tps": [],
            "source": source,
            "ts": float(item.get("ts") or now),
            "added": now,
            "raw": "",
        }
    if row is None:
        return None
    row["url"] = str(item.get("url", "") or "")
    row["added"] = now
    target = path()
    if _recent_duplicate(row, _load(since_ts=row["ts"] - DEDUP_SEC * 10, file_path=target)):
        return None
    _append([row], file_path=target)
    return row


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def ingest(items: list[dict], now_ts: float | None = None) -> dict:
    """Пакетный приём. Возвращает счётчики, не падая на одной плохой записи."""
    stored, skipped, errors = 0, 0, []
    for idx, item in enumerate(items):
        try:
            row = add_payload(item, now_ts=now_ts)
        except ValueError as e:
            errors.append({"index": idx, "error": str(e)})
            continue
        if row is None:
            skipped += 1
        else:
            stored += 1
    return {"stored": stored, "skipped": skipped, "errors": errors}


# --- оценка ---------------------------------------------------------------

def evaluate(symbol: str, side: str, now_ts: float | None = None,
             *, window_min: int | None = None, min_agree: int | None = None,
             sources: tuple[str, ...] | None = None) -> dict:
    """Голоса независимых источников по символу и стороне.

    Один источник = один голос: повторные посты в окне не усиливают сигнал.
    """
    now = time.time() if now_ts is None else float(now_ts)
    window = int(cfg.CONSENSUS_WINDOW_MIN if window_min is None else window_min) * 60
    need = int(cfg.CONSENSUS_MIN_AGREE if min_agree is None else min_agree)
    allow = cfg.CONSENSUS_SOURCES if sources is None else sources
    max_age = int(getattr(cfg, "CONSENSUS_MAX_AGE_MIN", 240)) * 60

    sym = normalize_symbol(symbol) or str(symbol).upper()
    want = normalize_side(side)
    if not want:
        return {"decision": "neutral", "for": 0, "against": 0,
                "sources_for": [], "sources_against": [], "window_min": window // 60}

    latest: dict[str, dict] = {}
    for row in _load(since_ts=now - max_age):
        if row.get("symbol") != sym:
            continue
        if abs(now - float(row.get("ts", 0.0))) > window:
            continue
        src = str(row.get("source", "")).strip().lower()
        if not src:
            continue
        if allow and src not in allow:
            continue
        if src not in latest or float(row["ts"]) > float(latest[src].get("ts", 0)):
            latest[src] = row

    sources_for = sorted(s for s, r in latest.items() if r.get("side") == want)
    sources_against = sorted(s for s, r in latest.items() if r.get("side") != want)
    if len(sources_against) >= need:
        decision = "conflict"
    elif len(sources_for) >= need:
        decision = "confirm"
    else:
        decision = "neutral"
    return {
        "decision": decision,
        "for": len(sources_for),
        "against": len(sources_against),
        "sources_for": sources_for,
        "sources_against": sources_against,
        "window_min": window // 60,
        "min_agree": need,
    }


def summary_text(hours: float = 24, now_ts: float | None = None) -> str:
    """Одна строка для heartbeat: сколько голосов и по каким символам."""
    now = time.time() if now_ts is None else float(now_ts)
    rows = _load(since_ts=now - float(hours) * 3600)
    if not rows:
        return "Консенсус: новых голосов нет"
    sources = {str(r.get("source", "")).lower() for r in rows if r.get("source")}
    by_symbol: dict[str, int] = {}
    for row in rows:
        sym = str(row.get("symbol", "?"))
        by_symbol[sym] = by_symbol.get(sym, 0) + 1
    top = ", ".join(f"{s} {n}" for s, n in
                    sorted(by_symbol.items(), key=lambda kv: -kv[1])[:3])
    return (f"Консенсус: {len(rows)} голосов от {len(sources)} источников"
            + (f" · {top}" if top else ""))


def precheck(symbol: str, side: str, now_ts: float | None = None) -> tuple[bool, dict]:
    """Разрешение на публикацию своего сигнала. Fail-open при любой ошибке."""
    if not getattr(cfg, "CONSENSUS_ENABLED", False):
        return True, {"decision": "off"}
    mode = str(getattr(cfg, "CONSENSUS_MODE", "warn")).lower()
    if mode not in ("warn", "filter"):
        return True, {"decision": "off"}
    try:
        verdict = evaluate(symbol, side, now_ts)
    except Exception as e:                     # noqa: BLE001 — фильтр не должен ломать бота
        return True, {"decision": "error", "error": repr(e)}
    block = (mode == "filter"
             and verdict["decision"] == "conflict"
             and bool(getattr(cfg, "CONSENSUS_BLOCK_CONFLICT", True)))
    return (not block), verdict


# --- CLI ------------------------------------------------------------------

def describe(row: dict) -> str:
    when = dt.datetime.fromtimestamp(float(row["ts"]), dt.timezone.utc)
    levels = []
    if row.get("entry") is not None:
        levels.append(f"вход {row['entry']}")
    if row.get("sl") is not None:
        levels.append(f"SL {row['sl']}")
    if row.get("tp") is not None:
        levels.append(f"TP {row['tp']}")
    tail = " · " + " · ".join(levels) if levels else ""
    return (f"{when:%d.%m %H:%M} UTC  {row['symbol']:8} {row['side']:5} "
            f"[{row['source']}]{tail}")


def status_text(symbol: str | None, side: str | None) -> str:
    lines = [f"консенсус: {'вкл' if cfg.CONSENSUS_ENABLED else 'выкл'} · "
             f"режим {cfg.CONSENSUS_MODE} · порог {cfg.CONSENSUS_MIN_AGREE} · "
             f"окно {cfg.CONSENSUS_WINDOW_MIN} мин"]
    if symbol and side:
        v = evaluate(symbol, side)
        lines.append(f"{normalize_symbol(symbol)} {normalize_side(side)} → {v['decision']} "
                     f"(за {v['for']}, против {v['against']})")
        if v["sources_for"]:
            lines.append("  за: " + ", ".join(v["sources_for"]))
        if v["sources_against"]:
            lines.append("  против: " + ", ".join(v["sources_against"]))
    rows = _load(since_ts=time.time() - 7 * 86400)
    lines.append(f"записей за 7 дней: {len(rows)}")
    for row in rows[-15:]:
        lines.append("  " + describe(row))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Консенсус внешних источников (внутренний фильтр)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="добавить сигнал из текста")
    p_add.add_argument("--source", required=True)
    p_add.add_argument("--text", required=True)
    p_add.add_argument("--url", default="")

    p_ing = sub.add_parser("ingest", help="загрузить JSON-файл со списком сигналов")
    p_ing.add_argument("file")

    sub.add_parser("list", help="последние записи")
    p_st = sub.add_parser("status", help="текущее голосование")
    p_st.add_argument("symbol", nargs="?")
    p_st.add_argument("side", nargs="?")

    args = ap.parse_args()
    if args.cmd == "add":
        row = add_payload({"source": args.source, "text": args.text, "url": args.url})
        print("сохранено: " + describe(row) if row else "не сигнал или дубликат — пропущено")
        return 0
    if args.cmd == "ingest":
        with open(args.file, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("signals", []) if isinstance(data, dict) else data
        res = ingest(list(items))
        print(f"сохранено {res['stored']}, пропущено {res['skipped']}, "
              f"ошибок {len(res['errors'])}")
        for err in res["errors"]:
            print(f"  #{err['index']}: {err['error']}")
        return 0 if not res["errors"] else 1
    if args.cmd == "list":
        for row in _load(since_ts=time.time() - 30 * 86400)[-50:]:
            print(describe(row))
        return 0
    print(status_text(args.symbol, args.side))
    return 0


if __name__ == "__main__":
    sys.exit(main())
