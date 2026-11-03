"""Портфельный kill-switch: недельный лимит убытка по всем ботам.

Правило: если сумма R закрытых сделок с начала недели (понедельник 00:00
в DISPLAY_TZ) ниже PORTFOLIO_WEEK_LIMIT_R и сделок достаточно, monitor.py
пишет паузу до следующего понедельника и шлёт алерт владельцу. bot.py перед
публикацией нового сигнала проверяет pause_state() и пропускает вход —
открытые сделки при этом продолжают вестись.

Ручное снятие: /guard resume в premium-боте или `python risk_guard.py resume`.
Снятие действует до конца текущей недели: те же убытки повторно не ставят
паузу, иначе после ручного resume бот замолчал бы через 15 минут снова.

Запуск:  python risk_guard.py status
         python risk_guard.py check    # применить правило (как monitor.py)
         python risk_guard.py resume   # снять паузу на текущую неделю
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "risk_guard.json")
TRADES_PATH = os.path.join(HERE, "trades.jsonl")

WEEK_LIMIT_R = float(getattr(cfg, "PORTFOLIO_WEEK_LIMIT_R", -6.0))
MIN_TRADES = int(getattr(cfg, "PORTFOLIO_WEEK_MIN_TRADES", 4))


def _tz() -> dt.tzinfo:
    try:
        return ZoneInfo(cfg.DISPLAY_TZ)
    except Exception:
        return dt.timezone.utc


def week_bounds(now_ts: float | None = None) -> tuple[float, float, str]:
    """(начало, конец, ключ) текущей недели в часовом поясе отображения."""
    now = dt.datetime.fromtimestamp(time.time() if now_ts is None else now_ts, _tz())
    start = (now.replace(hour=0, minute=0, second=0, microsecond=0)
             - dt.timedelta(days=now.weekday()))
    end = start + dt.timedelta(days=7)
    year, week, _ = start.isocalendar()
    return start.timestamp(), end.timestamp(), f"{year}-W{week:02d}"


def until_text(ts: float) -> str:
    return dt.datetime.fromtimestamp(float(ts), _tz()).strftime("%d.%m %H:%M")


def read_closed_trades(path: str | None = None) -> list[dict]:
    """Закрытые сделки из trades.jsonl без частичных выходов и дублей."""
    path = path or TRADES_PATH
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return out
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("kind") in ("tp1", "tp2"):
                continue
            tid = str(rec.get("trade_id") or "")
            key = (tid, str(rec.get("kind") or "")) if tid else None
            if key is not None and key in seen:
                continue
            if key is not None:
                seen.add(key)
            out.append(rec)
    return out


def weekly_stats(now_ts: float | None = None,
                 rows: list[dict] | None = None) -> dict:
    start, end, key = week_bounds(now_ts)
    rows = read_closed_trades() if rows is None else rows
    week = [t for t in rows if start <= float(t.get("ts", 0)) < end]
    return {
        "n": len(week),
        "r": sum(float(t.get("r", 0)) for t in week),
        "start": start,
        "end": end,
        "week_key": key,
        "from_text": until_text(start),
        "to_text": until_text(end),
    }


def _load() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    tmp = STATE_PATH + f".tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def evaluate(now_ts: float | None = None,
             rows: list[dict] | None = None) -> dict:
    """Текущее правило без записи паузы: удобно для status/диагностики."""
    now = time.time() if now_ts is None else now_ts
    stats = weekly_stats(now, rows)
    state = _load()
    until = float(state.get("paused_until") or 0)
    # лимит 0 (или положительный) означает «выключено»
    breach = (WEEK_LIMIT_R < 0 and stats["n"] >= MIN_TRADES
              and stats["r"] <= WEEK_LIMIT_R)
    return {
        **stats,
        "limit": WEEK_LIMIT_R,
        "min_trades": MIN_TRADES,
        "breach": breach,
        "paused": until > now,
        "until": until,
        "reason": str(state.get("reason") or ""),
        "overridden": state.get("overridden_week") == stats["week_key"],
        "state": state,
    }


def apply(now_ts: float | None = None,
          rows: list[dict] | None = None) -> dict:
    """Применить правило: включить паузу при пробое, снять истёкшую."""
    now = time.time() if now_ts is None else now_ts
    res = evaluate(now, rows)
    if res["paused"]:
        return {**res, "newly_paused": False}
    if not res["breach"]:
        if res["state"].get("paused_until"):
            _save({})          # пауза истекла и пробоя больше нет
        return {**res, "newly_paused": False}
    if res["overridden"]:
        return {**res, "newly_paused": False,
                "reason": "убыток недели уже принят вручную"}
    reason = (f"неделя {res['week_key']}: {res['r']:+.2f}R за "
              f"{res['n']} сделок (лимит {res['limit']:+.1f}R)")
    state = {
        "paused_until": res["end"],
        "week_key": res["week_key"],
        "triggered_at": now,
        "reason": reason,
        "week_r": round(res["r"], 4),
        "week_trades": res["n"],
    }
    _save(state)
    return {**res, "paused": True, "until": res["end"],
            "reason": reason, "newly_paused": True, "state": state}


def pause_state(now_ts: float | None = None) -> dict:
    """Дешёвая проверка для bot.py: писать ничего не должна."""
    now = time.time() if now_ts is None else now_ts
    state = _load()
    until = float(state.get("paused_until") or 0)
    return {
        "paused": until > now,
        "until": until,
        "reason": str(state.get("reason") or ""),
        "week_key": str(state.get("week_key") or ""),
    }


def resume(now_ts: float | None = None) -> str:
    """Снять паузу до конца текущей недели (ручное решение владельца)."""
    now = time.time() if now_ts is None else now_ts
    _start, _end, key = week_bounds(now)
    _save({"overridden_week": key, "resumed_at": now,
           "reason": "снято вручную"})
    return key


def status_text(now_ts: float | None = None) -> str:
    res = evaluate(now_ts)
    head = (f"Неделя {res['week_key']}: {res['r']:+.2f}R за {res['n']} сделок "
            f"(лимит {res['limit']:+.1f}R, минимум {res['min_trades']} сделок)")
    if res["limit"] >= 0:
        return f"{head}\n⏸ Лимит выключен (PORTFOLIO_WEEK_LIMIT_R >= 0)."
    if res["paused"]:
        return (f"{head}\n⛔ Пауза до {until_text(res['until'])}: "
                f"{res['reason']}\nСнять: /guard resume")
    if res["breach"] and res["overridden"]:
        return f"{head}\n⚠️ Лимит пробит, но пауза снята вручную до конца недели."
    if res["breach"]:
        return f"{head}\n⚠️ Лимит пробит — monitor.py включит паузу при следующей проверке."
    return f"{head}\n✅ В пределах лимита."


def main() -> int:
    cmd = sys.argv[1].strip().lower() if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(status_text())
        return 0
    if cmd == "check":
        res = apply()
        print(status_text())
        print("новая пауза" if res["newly_paused"] else "изменений нет")
        return 0
    if cmd == "resume":
        key = resume()
        print(f"пауза снята до конца недели {key}")
        return 0
    print("Формат: python risk_guard.py status|check|resume")
    return 2


if __name__ == "__main__":
    sys.exit(main())
