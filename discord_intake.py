"""Discord → консенсус: бот читает разрешённые каналы официальным Bot API.

Telegram-каналы читать нельзя (бот видит только свои каналы, userbot нарушает
правила), а в Discord официальный API для ботов — штатный способ. Поэтому
идеи и сделки из Discord можно принимать автоматически.

Что делает:
    * слушает только каналы из DISCORD_CHANNELS (без списка не слушает ничего);
    * текст сообщения разбирает consensus.parse_external;
    * если текста нет или он не разобрался — OCR скриншота (ocr_text.py);
    * распознанный сигнал пишет в consensus_signals.jsonl как обычный голос;
    * нераспознанный — в discord_review.jsonl и (по флагу) алертом владельцу.

Чужие тексты и картинки никуда не публикуются: это только внутренний фильтр.
Бот должен быть приглашён на сервер с правами Read Messages/History и
включённым Message Content Intent в Developer Portal.

Два режима каналов:
    DISCORD_CHANNELS          — свежие идеи/сигналы → голоса консенсуса;
    DISCORD_ARCHIVE_CHANNELS  — ветки с разборами сделок → корпус для research.

Разбор — это закрытая сделка, поэтому он не голосует задним числом, а
складывается в discord_trades.jsonl: символ, сторона, outcome (win/loss),
R и ссылка. Ветки подхватываются по parent_id: достаточно id родительского
канала, отдельные id веток не нужны.

Запуск:
    python discord_intake.py                    # слушать каналы
    python discord_intake.py --check            # конфиг и доступный OCR
    python discord_intake.py backfill --channel 123 --threads
    python discord_intake.py link https://discord.com/channels/<g>/<c>/<m>
    python discord_intake.py stats

Пакеты: discord.py; для скриншотов — rapidocr-onnxruntime (или tesseract).
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg
import consensus
import ocr_text

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "discord_state.json")
REVIEW_PATH = os.path.join(HERE, "discord_review.jsonl")
ARCHIVE_PATH = os.path.join(HERE, "discord_trades.jsonl")
INBOX = os.path.join(HERE, "discord_inbox")

# Разбор сделки — не сигнал: он уже закрыт, поэтому в голоса консенсуса не идёт.
WIN_WORDS = ("профит", "прибыл", "плюс", "win", "profit", "тейк", "took profit")
LOSS_WORDS = ("убыт", "минус", "лосс", "loss", "стоп", "stop out", "ликвидац")
R_RE = re.compile(r"([+-]?\d+(?:[.,]\d+)?)\s*R\b", re.IGNORECASE)
LINK_RE = re.compile(r"discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)")

MAX_IMAGES = 3
MAX_BYTES = 10 * 1024 * 1024
SEEN_LIMIT = 5000
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _now_text() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%d.%m %H:%M UTC")


# --- состояние ------------------------------------------------------------

def _load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {"seen": []}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"seen": []}
    if not isinstance(data.get("seen"), list):
        data["seen"] = []
    return data


def _save_state(state: dict) -> None:
    seen = state.get("seen", [])[-SEEN_LIMIT:]
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"seen": seen}, f)
    os.replace(tmp, STATE_PATH)


def _review_row(source: str, *, message_id: str, url: str, text: str,
                ocr: str, images: list[str], symbol: str = "") -> dict:
    return {
        "ts": time.time(),
        "source": source,
        "message_id": message_id,
        "url": url,
        "text": text[:300],
        "ocr": ocr[:500],
        "symbol": symbol,
        "images": images,
    }


def _load_review() -> list[dict]:
    if not os.path.exists(REVIEW_PATH):
        return []
    rows: list[dict] = []
    with open(REVIEW_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _save_review(rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(REVIEW_PATH)) or ".", exist_ok=True)
    tmp = REVIEW_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, REVIEW_PATH)


def make_review_row(source: str, *, message_id: str, url: str = "",
                    text: str = "", ocr: str = "", images: list[str] | None = None,
                    symbol: str = "") -> dict:
    """Публичная обёртка: очередь пополняют и Telegram-скриншоты."""
    return _review_row(source, message_id=message_id, url=url, text=text,
                       ocr=ocr, images=images or [], symbol=symbol)


def add_review(row: dict) -> None:
    _save_review(_load_review() + [row])


def review_count() -> int:
    try:
        return len(_load_review())
    except OSError:
        return 0


def review_caption(row: dict, result: str = "") -> str:
    """Подпись к алерту; тем же текстом обновляем сообщение после нажатия."""
    lines = [f"📥 Discord · {row.get('source', '?')}"]
    symbol = row.get("symbol") or "тикер не распознан"
    lines.append(f"Тикер: {symbol}")
    hint = (row.get("ocr") or row.get("text") or "").strip()
    if hint:
        lines.append("Текст: " + hint[:200])
    if row.get("url"):
        lines.append(str(row["url"]))
    if result:
        lines.append("")
        lines.append(result)
    else:
        lines.append("")
        lines.append("Сторона не распознана — подтверди кнопкой.")
    return "\n".join(lines)


def resolve_review(message_id: str, side: str | None) -> dict:
    """Закрывает элемент очереди. side=None — пропустить без записи голоса."""
    rows = _load_review()
    found = None
    keep: list[dict] = []
    for row in rows:
        if found is None and str(row.get("message_id")) == str(message_id):
            found = row
        else:
            keep.append(row)
    if found is None:
        return {"ok": False, "error": "пост уже разобран или не в очереди"}

    stored = None
    if side:
        text = str(found.get("ocr") or found.get("text") or "")
        parsed = consensus.parse_external(
            text, str(found.get("source", "")), side_override=side)
        if parsed is None:
            return {"ok": False, "error": "тикер не распознан — добавь вручную /ext",
                    "row": found}
        try:
            stored = consensus.add_payload({
                "source": parsed["source"],
                "symbol": parsed["symbol"],
                "side": side.upper(),
                "entry": parsed.get("entry"),
                "sl": parsed.get("sl"),
                "tp": parsed.get("tp"),
                "url": found.get("url", ""),
                "ts": time.time(),
            })
        except Exception as e:              # noqa: BLE001
            return {"ok": False, "error": f"не сохранил: {e}", "row": found}
    _save_review(keep)
    return {"ok": True, "row": found, "stored": stored,
            "symbol": (stored or found).get("symbol", "")}


# --- обработка сообщения (без discord, чтобы тестировалось) ---------------

def process_message(source: str, text: str, images: list[dict], *,
                    message_id: str = "", url: str = "",
                    now_ts: float | None = None, ocr=None) -> dict:
    """Разбирает одно сообщение. images — [{"path": ..., "url": ...}].

    Возвращает {"status": "stored|duplicate|review", ...}. Никогда не бросает:
    ошибка приёма не должна ронять бота.
    """
    now = time.time() if now_ts is None else float(now_ts)
    ocr_fn = ocr or ocr_text.image_text
    text = (text or "").strip()
    used_text = ""
    via = "text"
    row = consensus.parse_external(text, source, ts=now) if text else None
    if row:
        used_text = text

    ocr_used = ""
    if row is None:
        for img in images[:MAX_IMAGES]:
            path = str(img.get("path") or "")
            if not path or not os.path.exists(path):
                continue
            try:
                got = ocr_fn(path)
            except Exception:               # noqa: BLE001 — OCR не должен ронять приём
                got = ""
            if not got:
                continue
            ocr_used = (ocr_used + " " + got).strip()
            row = consensus.parse_external(got, source, ts=now)
            if row:
                used_text = got
                via = "ocr"
                break

    if row is not None:
        try:
            stored = consensus.add_payload(
                {"source": source, "text": used_text, "url": url, "ts": now},
                now_ts=now,
            )
        except Exception as e:              # noqa: BLE001 — fail-open
            return {"status": "error", "error": repr(e)}
        if stored is None:
            return {"status": "duplicate", "symbol": row["symbol"], "side": row["side"]}
        return {"status": "stored", "symbol": stored["symbol"], "side": stored["side"],
                "via": via}

    images_paths = [str(i.get("path")) for i in images if i.get("path")]
    row = _review_row(
        source, message_id=message_id, url=url, text=text,
        ocr=ocr_used, images=images_paths,
        symbol=consensus.find_symbol(ocr_used or text) or "")
    try:
        _save_review(_load_review() + [row])
    except OSError:
        pass
    return {"status": "review", "ocr": bool(ocr_used),
            "images": len(images_paths), "row": row}


def review_markup(message_id: str) -> dict:
    return {"inline_keyboard": [[
        {"text": "🟢 LONG", "callback_data": f"drev:{message_id}:LONG"},
        {"text": "🔴 SHORT", "callback_data": f"drev:{message_id}:SHORT"},
        {"text": "Пропустить", "callback_data": f"drev:{message_id}:SKIP"},
    ]]}


def notify_review(res: dict, source: str, url: str) -> None:
    """Алерт владельцу: картинка + кнопки. Callback принимает premium-бот."""
    if not getattr(cfg, "DISCORD_REVIEW_ALERT", True) or not cfg.OWNER_CHAT_ID:
        return
    import tg

    row = res.get("row") or {"source": source, "url": url}
    mid = str(row.get("message_id", ""))
    if not mid:
        return
    caption = review_caption(row)
    markup = review_markup(mid)
    # Отправляем от premium-бота: его getUpdates принимает callback от кнопок.
    token = cfg.SUB_BOT_TOKEN or None
    images = [p for p in row.get("images") or [] if os.path.exists(p)]
    try:
        if images:
            ok, _ = tg.send_photo(images[0], caption, token=token,
                                  chat_id=cfg.OWNER_CHAT_ID, markup=markup)
            if ok:
                return
        tg.send_message(caption, token=token, chat_id=cfg.OWNER_CHAT_ID,
                        markup=markup)
    except Exception:                       # noqa: BLE001
        pass


# --- загрузка вложений ----------------------------------------------------

def is_image(att) -> bool:
    ctype = str(getattr(att, "content_type", "") or "").lower()
    if ctype.startswith("image/"):
        return True
    name = str(getattr(att, "filename", "") or "").lower()
    return os.path.splitext(name)[1] in IMAGE_EXT


def download(url: str, path: str) -> bool:
    import requests

    try:
        with requests.get(url, timeout=20, stream=True) as resp:
            if resp.status_code != 200:
                return False
            size = 0
            with open(path, "wb") as f:
                for chunk in resp.iter_content(64 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        f.close()
                        os.remove(path)
                        return False
                    f.write(chunk)
        return True
    except Exception:                       # noqa: BLE001
        return False


def save_attachment(att, message_id: str, idx: int) -> dict | None:
    os.makedirs(INBOX, exist_ok=True)
    name = str(getattr(att, "filename", "") or f"img{idx}.png")
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "._-") or f"img{idx}.png"
    path = os.path.join(INBOX, f"{message_id}_{idx}_{safe}")
    url = str(getattr(att, "url", "") or "")
    if not url or not download(url, path):
        return None
    return {"path": path, "url": url}


# --- архив разборов сделок (корпус для research) --------------------------

def detect_outcome(*parts) -> str:
    """win / loss / '' по явному R и словам в заголовке ветки и тексте."""
    blob = " ".join(str(p or "") for p in parts).lower()
    m = re.search(r"([+-])\s*\d+(?:[.,]\d+)?\s*r\b", blob)
    if m:
        return "win" if m.group(1) == "+" else "loss"
    if any(word in blob for word in WIN_WORDS):
        return "win"
    if any(word in blob for word in LOSS_WORDS):
        return "loss"
    return ""


NEG_WORDS = ("минус", "убыт", "loss", "лосс", "стоп")


def detect_r(*parts) -> float | None:
    """Первое «+3R» / «-1.2R» / «минус 1,2R» из текста или заголовка."""
    for part in parts:
        text = str(part or "")
        m = R_RE.search(text)
        if not m:
            continue
        try:
            value = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        raw_sign = m.group(1)[0]
        if raw_sign not in "+-":
            before = text[:m.start()].lower()
            if any(word in before for word in NEG_WORDS):
                value = -abs(value)
        return value
    return None


def parse_link(url: str) -> tuple[str, str, str] | None:
    """https://discord.com/channels/<guild>/<channel>/<message> → три id."""
    m = LINK_RE.search(str(url or ""))
    return (m.group(1), m.group(2), m.group(3)) if m else None


def build_archive_row(*, source: str, message_id: str, url: str, author: str,
                      text: str, title: str = "", images: list[str] | None = None,
                      ocr: str = "", ts: float | None = None) -> dict:
    blob = " ".join(p for p in (title, text, ocr) if p)
    return {
        "ts": float(ts if ts is not None else time.time()),
        "source": source,
        "thread": title,
        "message_id": str(message_id),
        "url": url,
        "author": author,
        "text": text[:1000],
        "ocr": ocr[:1000],
        "symbol": consensus.find_symbol(blob) or "",
        "side": consensus.normalize_side(blob) or "",
        "outcome": detect_outcome(title, text, ocr),
        "r": detect_r(title, text, ocr),
        "images": images or [],
    }


def _load_archive() -> list[dict]:
    if not os.path.exists(ARCHIVE_PATH):
        return []
    out: list[dict] = []
    with open(ARCHIVE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def archive_rows(rows: list[dict]) -> int:
    """Пишет только новые message_id; возвращает число добавленных."""
    have = {str(r.get("message_id")) for r in _load_archive()}
    fresh = [r for r in rows if r and str(r.get("message_id")) not in have]
    if not fresh:
        return 0
    os.makedirs(os.path.dirname(os.path.abspath(ARCHIVE_PATH)) or ".", exist_ok=True)
    with open(ARCHIVE_PATH, "a", encoding="utf-8") as f:
        for row in fresh:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(fresh)


def archive_stats() -> str:
    rows = _load_archive()
    if not rows:
        return "Разборов в архиве нет"
    wins = sum(1 for r in rows if r.get("outcome") == "win")
    losses = sum(1 for r in rows if r.get("outcome") == "loss")
    unknown = len(rows) - wins - losses
    by_symbol: dict[str, list[int]] = {}
    for row in rows:
        sym = row.get("symbol") or "?"
        slot = by_symbol.setdefault(sym, [0, 0])
        if row.get("outcome") == "win":
            slot[0] += 1
        elif row.get("outcome") == "loss":
            slot[1] += 1
    top = ", ".join(f"{s} {w}+/{l}-" for s, (w, l) in
                    sorted(by_symbol.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:5])
    return (f"Разборов: {len(rows)} · профит {wins} · убыток {losses} · "
            f"без метки {unknown}" + (f"\n  {top}" if top else ""))


async def archive_discord_message(message, *, with_ocr: bool = True) -> dict:
    """Собирает строку архива из discord-сообщения (картинки скачиваются)."""
    guild = getattr(message.guild, "name", "") if message.guild else ""
    chan = getattr(message.channel, "name", "?")
    source = f"discord:{guild + '#' if guild else ''}{chan}"
    mid = str(message.id)

    parts = [str(message.content or "")]
    for embed in message.embeds or []:
        for attr in ("title", "description"):
            value = getattr(embed, attr, None)
            if value:
                parts.append(str(value))
    text = "\n".join(p for p in parts if p).strip()

    images = []
    if with_ocr:
        for idx, att in enumerate(message.attachments or []):
            if len(images) >= MAX_IMAGES:
                break
            if not is_image(att):
                continue
            saved = await asyncio.to_thread(save_attachment, att, mid, idx)
            if saved:
                images.append(saved["path"])

    ocr = ""
    if images:
        ocr = "\n".join(await asyncio.to_thread(ocr_text.image_text, p) for p in images)

    author = getattr(message.author, "display_name", "") or str(
        getattr(message.author, "name", ""))
    ts = getattr(message, "created_at", None)
    return build_archive_row(
        source=source, message_id=mid,
        url=str(getattr(message, "jump_url", "") or ""),
        author=author, text=text,
        title=str(chan) if getattr(message.channel, "parent_id", None) else "",
        images=images, ocr=ocr,
        ts=ts.timestamp() if ts else None)


# --- Discord --------------------------------------------------------------

def _id_set(raw) -> set[int]:
    out: set[int] = set()
    for item in raw or ():
        try:
            out.add(int(str(item).strip()))
        except ValueError:
            continue
    return out


def _channel_ids() -> set[int]:
    return _id_set(getattr(cfg, "DISCORD_CHANNELS", ()))


def _archive_ids() -> set[int]:
    return _id_set(getattr(cfg, "DISCORD_ARCHIVE_CHANNELS", ()))


def _matches(channel, ids: set[int]) -> bool:
    """Канал или его ветка: у сообщения в ветке channel.id — id ветки."""
    if not ids:
        return False
    if int(channel.id) in ids:
        return True
    parent = getattr(channel, "parent_id", None)
    return bool(parent and int(parent) in ids)


def _allowed_channel(channel) -> bool:
    return _matches(channel, _channel_ids() | _archive_ids())


def _in_archive_channel(channel) -> bool:
    return _matches(channel, _archive_ids())


async def _handle(message, seen: dict) -> None:
    if message.author.bot:
        return
    if not _allowed_channel(message.channel):
        return

    mid = str(message.id)
    if mid in seen["seen"]:
        return

    # Разбор закрытой сделки — не сигнал: он идёт в корпус для research,
    # а не в голоса консенсуса (иначе бот «голосовал» бы задним числом).
    if _in_archive_channel(message.channel):
        row = await archive_discord_message(message)
        written = archive_rows([row])
        seen["seen"].append(mid)
        _save_state(seen)
        print(f"{_now_text()} {row['source']}: разбор → архив "
              f"({row.get('symbol') or '?'} {row.get('outcome') or '?'}"
              f"{', новый' if written else ', дубликат'})")
        return

    guild = getattr(message.guild, "name", "") if message.guild else ""
    chan = getattr(message.channel, "name", "?")
    source = f"discord:{guild + '#' if guild else ''}{chan}"

    text_parts = [str(message.content or "")]
    for embed in message.embeds or []:
        for attr in ("title", "description"):
            value = getattr(embed, attr, None)
            if value:
                text_parts.append(str(value))
    text = "\n".join(p for p in text_parts if p).strip()

    images = []
    for idx, att in enumerate(message.attachments or []):
        if len(images) >= MAX_IMAGES:
            break
        if not is_image(att):
            continue
        saved = save_attachment(att, mid, idx)
        if saved:
            images.append(saved)
    for embed in message.embeds or []:
        if len(images) >= MAX_IMAGES:
            break
        image = getattr(embed, "image", None)
        url = str(getattr(image, "url", "") or "")
        if not url:
            continue
        path = os.path.join(INBOX, f"{mid}_embed{len(images)}.png")
        os.makedirs(INBOX, exist_ok=True)
        if download(url, path):
            images.append({"path": path, "url": url})

    url = str(getattr(message, "jump_url", "") or "")
    res = await asyncio.to_thread(process_message, source, text, images,
                                  message_id=mid, url=url)
    seen["seen"].append(mid)
    _save_state(seen)
    stamp = _now_text()
    if res["status"] == "stored":
        print(f"{stamp} {source}: {res['symbol']} {res['side']} ({res['via']})")
    elif res["status"] == "review":
        print(f"{stamp} {source}: в очередь на проверку")
        await asyncio.to_thread(notify_review, res, source, url)
    else:
        print(f"{stamp} {source}: {res['status']}")


def build_client():
    import discord

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    client = discord.Client(intents=intents)
    seen = _load_state()

    @client.event
    async def on_ready() -> None:
        print(f"Discord: {client.user} · сигналы {len(_channel_ids())} · "
              f"разборы {len(_archive_ids())} · "
              f"OCR {ocr_text.backend() or 'нет'}")

    @client.event
    async def on_message(message) -> None:
        try:
            await _handle(message, seen)
        except Exception as e:              # noqa: BLE001
            print("ошибка обработки сообщения:", repr(e))

    return client


async def _collect_history(channel, limit: int, with_ocr: bool,
                           rows: list[dict]) -> None:
    async for msg in channel.history(limit=limit, oldest_first=False):
        if msg.author.bot:
            continue
        rows.append(await archive_discord_message(msg, with_ocr=with_ocr))


async def _backfill(client, channel_id: int, limit: int, threads: bool,
                    with_ocr: bool) -> None:
    channel = client.get_channel(channel_id)
    if channel is None:
        channel = await client.fetch_channel(channel_id)
    name = getattr(channel, "name", channel_id)
    rows: list[dict] = []
    await _collect_history(channel, limit, with_ocr, rows)
    if threads:
        targets = list(getattr(channel, "threads", None) or [])
        try:
            async for thread in channel.archived_threads(limit=None):
                targets.append(thread)
        except Exception as e:              # noqa: BLE001 — архив может быть закрыт
            print("архивные ветки недоступны:", repr(e))
        for thread in targets:
            await _collect_history(thread, limit, with_ocr, rows)
    written = archive_rows(rows)
    print(f"{name}: собрано {len(rows)}, новых {written}")
    print(archive_stats())


async def _import_link(client, url: str, with_ocr: bool) -> None:
    ids = parse_link(url)
    if not ids:
        print("это не ссылка на сообщение Discord")
        return
    _guild, channel_id, message_id = ids
    channel = client.get_channel(int(channel_id))
    if channel is None:
        channel = await client.fetch_channel(int(channel_id))
    message = await channel.fetch_message(int(message_id))
    row = await archive_discord_message(message, with_ocr=with_ocr)
    written = archive_rows([row])
    print(f"{'новый' if written else 'уже был'}: {row['source']} · "
          f"{row['symbol'] or '?'} {row['side'] or '?'} · "
          f"{row['outcome'] or 'без метки'} · R {row['r']}")


def _run(coro_factory) -> int:
    client = build_client()

    @client.event
    async def on_ready() -> None:
        try:
            await coro_factory(client)
        except Exception as e:              # noqa: BLE001
            print("ошибка:", repr(e))
        finally:
            await client.close()

    client.run(cfg.DISCORD_TOKEN)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Discord: сигналы и архив разборов")
    ap.add_argument("--check", action="store_true", help="показать конфиг")
    sub = ap.add_subparsers(dest="cmd")

    p_back = sub.add_parser("backfill", help="собрать историю канала/веток")
    p_back.add_argument("--channel", required=True, help="id канала или ветки")
    p_back.add_argument("--limit", type=int, default=200,
                        help="сообщений на канал (по умолчанию 200)")
    p_back.add_argument("--threads", action="store_true",
                        help="включая активные и архивные ветки")
    p_back.add_argument("--no-ocr", action="store_true", help="без картинок и OCR")

    p_link = sub.add_parser("link", help="импортировать одно сообщение по ссылке")
    p_link.add_argument("url")
    p_link.add_argument("--no-ocr", action="store_true")

    sub.add_parser("stats", help="сводка архива разборов")
    args = ap.parse_args()

    if args.check:
        print(f"включено: {getattr(cfg, 'DISCORD_INTAKE_ENABLED', False)}")
        print(f"токен: {'задан' if cfg.DISCORD_TOKEN else 'нет'}")
        print(f"каналы сигналов: {sorted(_channel_ids()) or 'нет'}")
        print(f"каналы разборов: {sorted(_archive_ids()) or 'нет'}")
        print(f"OCR: {ocr_text.backend() or 'нет — pip install rapidocr-onnxruntime'}")
        print(f"хранилище голосов: {consensus.path()}")
        return 0
    if args.cmd == "stats":
        print(archive_stats())
        return 0
    if args.cmd in ("backfill", "link") and not cfg.DISCORD_TOKEN:
        print("нет DISCORD_TOKEN в .env — создай приложение в Developer Portal")
        return 1
    if args.cmd == "backfill":
        return _run(lambda client: _backfill(
            client, int(args.channel), args.limit, args.threads, not args.no_ocr))
    if args.cmd == "link":
        return _run(lambda client: _import_link(client, args.url, not args.no_ocr))

    if not getattr(cfg, "DISCORD_INTAKE_ENABLED", False):
        print("Discord-приём выключен: DISCORD_INTAKE_ENABLED=0")
        return 0
    if not cfg.DISCORD_TOKEN:
        print("нет DISCORD_TOKEN в .env — создай приложение в Developer Portal")
        return 1
    if not _channel_ids() and not _archive_ids():
        print("нет DISCORD_CHANNELS / DISCORD_ARCHIVE_CHANNELS — нечего слушать")
        return 1
    try:
        import discord  # noqa: F401
    except ImportError:
        print("нет discord.py: pip install discord.py")
        return 1
    client = build_client()
    client.run(cfg.DISCORD_TOKEN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
