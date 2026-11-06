"""Ежедневный heartbeat в Telegram владельцу: боты живы, сигналы за сутки.

Запуск:
    python heartbeat.py          # цикл: сообщение в HEARTBEAT_TIME (по умолч. 09:00 МСК)
    python heartbeat.py once     # отправить один отчёт сейчас (проверка)

Читает signals_queue.jsonl (попытки постов ботов) и trades.jsonl (закрытые
сделки с R) из папки проекта; процессы ботов считает через tasklist.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg
import consensus
import discord_intake
import logger_config
import tg

logger_config.setup_logging(level=cfg.LOG_LEVEL, json_logs=cfg.LOG_JSON)
log = logger_config.get_logger("heartbeat")


def _safe_stdout() -> None:
    """Консоль Windows в cp1251 не печатает эмодзи — вывод не должен ронять отчёт."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def say(*args: object) -> None:
    """print, который не падает на символах вне кодировки консоли."""
    msg = " ".join(str(a) for a in args) + "\n"
    try:
        sys.stdout.write(msg)
        sys.stdout.flush()
    except UnicodeEncodeError:
        try:
            sys.stdout.write(msg.encode("ascii", "replace").decode("ascii"))
            sys.stdout.flush()
        except (UnicodeEncodeError, OSError):
            pass
    except OSError:
        pass


_safe_stdout()

TZ = ZoneInfo(cfg.DISPLAY_TZ)
HEARTBEAT_TIME = os.getenv("HEARTBEAT_TIME", "09:00").strip()
DIGEST_TIME = os.getenv("DIGEST_TIME", "21:00").strip()
QUEUE_PATH = Path(__file__).parent / "signals_queue.jsonl"
TRADES_PATH = Path(__file__).parent / "trades.jsonl"
STATE_PATH = Path(__file__).parent / "heartbeat_state.json"
EXPECTED_BOTS = int(os.getenv("HEARTBEAT_EXPECT_BOTS", "4"))  # range AUDUSD+USDCAD+EURUSD + classic XAUUSD


def _num(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _hhmm(raw: str) -> tuple[int, int] | None:
    try:
        h, m = str(raw).strip().split(":")
        h, m = int(h), int(m)
        return (h, m) if 0 <= h < 24 and 0 <= m < 60 else None
    except (ValueError, AttributeError):
        return None


def load_sent() -> dict:
    """Даты последней успешной отправки по каждому отчёту (переживает рестарт)."""
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_sent(data: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def _dedup(rows: list[dict]) -> list[dict]:
    """Одна сделка — одна строка, даже если журнал продублировал запись."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for rec in rows:
        key = (str(rec.get("trade_id", "")), str(rec.get("kind", "")))
        if rec.get("trade_id") and key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


def read_jsonl(path: Path, since: float) -> list[dict]:
    """Строки jsonl новее `since`; битые строки молча пропускаем (файл пишут 9 процессов)."""
    out: list[dict] = []
    if not path.exists():
        return out
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if _num(rec.get("ts")) >= since:
                    out.append(rec)
    except OSError:
        pass
    return out


def count_bots() -> tuple[int, int]:
    """(запущено bot.py, всего python.exe) — по tasklist + CIM, без зависимостей."""
    try:
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -match '(?<![a-z])bot\\.py' }).Count"],
            capture_output=True, timeout=30)
        n = int(re.search(r"\d+", ps.stdout.decode("utf-8", errors="ignore")).group())
        return n, 0
    except Exception:
        return -1, 0


def build_report() -> str:
    now = time.time()
    since = now - 86400
    sigs = read_jsonl(QUEUE_PATH, since)
    trades = read_jsonl(TRADES_PATH, since)
    closed = _dedup([t for t in trades if t.get("kind") not in ("tp1", "tp2")])
    r_sum = sum(_num(t.get("r")) for t in closed)
    bots, _ = count_bots()

    lines = ["🫀 Heartbeat · боты сигналов"]
    if bots >= 0:
        ok = "✅" if bots >= EXPECTED_BOTS else "⚠️"
        lines.append(f"{ok} bot.py запущено: {bots} из {EXPECTED_BOTS}")
    else:
        lines.append("⚠️ не удалось посчитать процессы")
    lines.append(f"Сигналов за 24ч: {len(sigs)}")
    # Внешние голоса — только владельцу, в публичный дайджест они не попадают.
    try:
        lines.append(consensus.summary_text(24))
        queued = discord_intake.review_count()
        if queued:
            lines.append(f"Очередь Discord: {queued} — подтвердить в premium-боте")
    except Exception:                       # noqa: BLE001 — отчёт важнее статистики
        pass
    if closed:
        lines.append(f"Сделок закрыто за 24ч: {len(closed)} · сумма {r_sum:+.2f}R")
        by_sym: dict[str, float] = {}
        for t in closed:
            by_sym[t.get("symbol", "?")] = by_sym.get(t.get("symbol", "?"), 0) + _num(t.get("r"))
        top = sorted(by_sym.items(), key=lambda kv: -abs(kv[1]))[:5]
        lines.append("  " + " · ".join(f"{s} {r:+.1f}R" for s, r in top))
    if not sigs and not closed:
        lines.append("(тишина за сутки — ботов в это время могло не быть)")
    lines.append(f"отчёт: {dt.datetime.now(TZ):%d.%m %H:%M} {cfg.DISPLAY_TZ}")
    return "\n".join(lines)


def send_report() -> bool:
    text = build_report()
    if not cfg.OWNER_CHAT_ID:
        say("OWNER_CHAT_ID пуст — некому отправлять")
        return False
    ok, err = tg.send_message(text, token=cfg.SUB_BOT_TOKEN or cfg.BOT_TOKEN,
                             chat_id=cfg.OWNER_CHAT_ID)
    say(("отправлено" if ok else f"ошибка отправки: {err}"))
    say(text)
    return ok


def build_digest() -> str:
    """Публичный итог дня для канала: сигналы, закрытые сделки, открытые позиции."""
    now = time.time()
    since = now - 86400
    sigs = read_jsonl(QUEUE_PATH, since)
    closed = _dedup([t for t in read_jsonl(TRADES_PATH, since)
                     if t.get("kind") not in ("tp1", "tp2")])
    r_sum = sum(_num(t.get("r")) for t in closed)
    wins = sum(1 for t in closed if _num(t.get("r")) > 0)

    open_list: list[str] = []
    try:
        for name in sorted(os.listdir(Path(__file__).parent)):
            if not (name.startswith("bot_state_") and name.endswith(".json")):
                continue
            try:
                with open(Path(__file__).parent / name, encoding="utf-8") as f:
                    st = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            tr = st.get("trade") if isinstance(st, dict) else None
            if not tr:
                continue
            # state умершего бота не должен месяцами висеть как «открытая сделка»
            age_h = (now - _num(tr.get("ts_saved") or tr.get("bar_ts"))) / 3600.0
            if age_h > 36:
                continue
            sym = name[len("bot_state_"):-len(".json")].split("_")[0]
            side = "LONG" if int(_num(tr.get("dir"), 1)) == 1 else "SHORT"
            open_list.append(f"{sym} {side} @ {tr.get('entry', '?')}")
    except OSError:
        pass

    lines = [f"📓 Итог суток · {dt.datetime.now(TZ):%d.%m} (окно 24 ч)"]
    lines.append(f"Сигналов: {len(sigs)}")
    if closed:
        lines.append(f"Закрыто сделок: {len(closed)} · {r_sum:+.2f}R · прибыльных {wins}/{len(closed)}")
    else:
        lines.append("Закрытых сделок не было")
    if open_list:
        lines.append("Открыто сейчас: " + " · ".join(open_list))
    elif not sigs:
        lines.append("Сетапов по правилам не было — тишина это тоже результат.")
    lines.append("Не является инвестиционной рекомендацией.")
    return "\n".join(lines)


def send_digest() -> bool:
    text = build_digest()
    if not cfg.CHAT_ID:
        say("CHAT_ID пуст — дайджест некуда отправлять")
        return False
    ok, err = tg.send_message(text, chat_id=cfg.CHAT_ID)
    say(("дайджест отправлен" if ok else f"ошибка дайджеста: {err}"))
    say(text)
    return ok


def seconds_until(hhmm: str) -> float:
    hh, mm = divmod(int(hhmm[:2]) * 60 + int(hhmm[3:5]), 1)
    now = dt.datetime.now(TZ)
    target = now.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:5]),
                         second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()


def main() -> None:
    if len(sys.argv) > 1:
        if sys.argv[1] == "once":
            send_report()
            if "--digest" in sys.argv:
                send_digest()
            return
        # Любой другой аргумент раньше молча поднимал второй демон
        # (дубли отчётов и гонка heartbeat_state.tmp).
        print("использование: python heartbeat.py [once [--digest]]")
        return
    say(f"heartbeat: отчёт владельцу в {HEARTBEAT_TIME}, дайджест в канал в {DIGEST_TIME} "
          f"({cfg.DISPLAY_TZ}); проверка — python heartbeat.py once")
    tasks = [
        (HEARTBEAT_TIME, send_report, "hb"),
        (DIGEST_TIME, send_digest, "digest"),
    ]
    for hhmm, _fn, tag in tasks:
        if not _hhmm(hhmm):
            say(f"{tag}: неверное время {hhmm!r} — ожидается ЧЧ:ММ, отчёт выключен")
    last_sent = load_sent()
    retry_at: dict[str, float] = {}
    while True:
        now = dt.datetime.now(TZ)
        today = now.strftime("%Y-%m-%d")
        for hhmm, fn, tag in tasks:
            hm = _hhmm(hhmm)
            if not hm:
                continue
            target = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
            if now < target or last_sent.get(tag) == today:
                continue
            if time.time() < retry_at.get(tag, 0.0):
                continue
            try:
                ok = fn()
            except Exception as e:
                say("ошибка отчёта:", repr(e))
                ok = False
            if ok:
                last_sent[tag] = today
                save_sent(last_sent)
                retry_at.pop(tag, None)
            else:
                # не отмечаем как отправленный: повторим через 5 минут
                retry_at[tag] = time.time() + 300
        time.sleep(30)


if __name__ == "__main__":
    main()
