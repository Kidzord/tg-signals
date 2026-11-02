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
import logger_config
import tg

logger_config.setup_logging(level=cfg.LOG_LEVEL, json_logs=cfg.LOG_JSON)
log = logger_config.get_logger("heartbeat")

TZ = ZoneInfo(cfg.DISPLAY_TZ)
HEARTBEAT_TIME = os.getenv("HEARTBEAT_TIME", "09:00").strip()
DIGEST_TIME = os.getenv("DIGEST_TIME", "21:00").strip()
QUEUE_PATH = Path(__file__).parent / "signals_queue.jsonl"
TRADES_PATH = Path(__file__).parent / "trades.jsonl"
EXPECTED_BOTS = int(os.getenv("HEARTBEAT_EXPECT_BOTS", "3"))  # range AUDUSD+USDCAD + classic XAUUSD


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
                if float(rec.get("ts", 0)) >= since:
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
    closed = [t for t in trades if t.get("kind") not in ("tp1", "tp2")]
    r_sum = sum(float(t.get("r", 0)) for t in closed)
    bots, _ = count_bots()

    lines = ["🫀 Heartbeat · боты сигналов"]
    if bots >= 0:
        ok = "✅" if bots >= EXPECTED_BOTS else "⚠️"
        lines.append(f"{ok} bot.py запущено: {bots} из {EXPECTED_BOTS}")
    else:
        lines.append("⚠️ не удалось посчитать процессы")
    lines.append(f"Сигналов за 24ч: {len(sigs)}")
    if closed:
        lines.append(f"Сделок закрыто за 24ч: {len(closed)} · сумма {r_sum:+.2f}R")
        by_sym: dict[str, float] = {}
        for t in closed:
            by_sym[t.get("symbol", "?")] = by_sym.get(t.get("symbol", "?"), 0) + float(t.get("r", 0))
        top = sorted(by_sym.items(), key=lambda kv: -abs(kv[1]))[:5]
        lines.append("  " + " · ".join(f"{s} {r:+.1f}R" for s, r in top))
    if not sigs and not closed:
        lines.append("(тишина за сутки — ботов в это время могло не быть)")
    lines.append(f"отчёт: {dt.datetime.now(TZ):%d.%m %H:%M} {cfg.DISPLAY_TZ}")
    return "\n".join(lines)


def send_report() -> bool:
    text = build_report()
    if not cfg.OWNER_CHAT_ID:
        print("OWNER_CHAT_ID пуст — некому отправлять")
        return False
    ok, err = tg.send_message(text, token=cfg.SUB_BOT_TOKEN or cfg.BOT_TOKEN,
                             chat_id=cfg.OWNER_CHAT_ID)
    print(("отправлено" if ok else f"ошибка отправки: {err}"))
    print(text)
    return ok


def build_digest() -> str:
    """Публичный итог дня для канала: сигналы, закрытые сделки, открытые позиции."""
    now = time.time()
    since = now - 86400
    sigs = read_jsonl(QUEUE_PATH, since)
    closed = [t for t in read_jsonl(TRADES_PATH, since) if t.get("kind") not in ("tp1", "tp2")]
    r_sum = sum(float(t.get("r", 0)) for t in closed)
    wins = sum(1 for t in closed if float(t.get("r", 0)) > 0)

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
            sym = name[len("bot_state_"):-len(".json")].split("_")[0]
            side = "LONG" if int(tr.get("dir", 1)) == 1 else "SHORT"
            open_list.append(f"{sym} {side} @ {tr.get('entry', '?')}")
    except OSError:
        pass

    lines = [f"📓 Итог суток · {dt.datetime.now(TZ):%d.%m}"]
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
        print("CHAT_ID пуст — дайджест некуда отправлять")
        return False
    ok, err = tg.send_message(text, chat_id=cfg.CHAT_ID)
    print(("дайджест отправлен" if ok else f"ошибка дайджеста: {err}"))
    print(text)
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
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        send_report()
        if "--digest" in sys.argv:
            send_digest()
        return
    print(f"heartbeat: отчёт владельцу в {HEARTBEAT_TIME}, дайджест в канал в {DIGEST_TIME} "
          f"({cfg.DISPLAY_TZ}); проверка — python heartbeat.py once")
    tasks = [
        (HEARTBEAT_TIME, send_report, "hb"),
        (DIGEST_TIME, send_digest, "digest"),
    ]
    last_sent: dict[str, str] = {}
    while True:
        now = dt.datetime.now(TZ)
        for hhmm, fn, tag in tasks:
            hh, mm = int(hhmm[:2]), int(hhmm[3:5])
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            today = now.strftime("%Y-%m-%d")
            if now >= target and last_sent.get(tag) != today:
                try:
                    fn()
                except Exception as e:
                    print("ошибка отчёта:", repr(e))
                last_sent[tag] = today
        time.sleep(30)


if __name__ == "__main__":
    main()
