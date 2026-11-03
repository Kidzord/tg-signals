"""Общий конструктор «среднего» поста: разработка + сделки + цифры.

Пишем от первого лица, как автор канала: соцсети (особенно Threads) банят
аккаунты за формулировки «этот пост написал бот». Скрин — последняя ЗАКРЫТАЯ
В ПЛЮС сделка; убытки при этом не прячутся, они остаются в журнале и в
вечернем итоге.

Модуль ничего не отправляет: только собирает строки и находит картинку.
"""
from __future__ import annotations

import json
import os
import time

import config as cfg
import devlog
import render as card

HERE = os.path.dirname(os.path.abspath(__file__))
TRADES_PATH = os.path.join(HERE, "trades.jsonl")
RENDERS = os.path.join(HERE, "renders")


def load_trades(path: str | None = None) -> list[dict]:
    """Закрытые сделки целиком: без частичных выходов и дублей, по времени."""
    path = path or TRADES_PATH
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    if not os.path.exists(path):
        return rows
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                kind = str(rec.get("kind") or "")
                if kind in ("tp1", "tp2"):
                    continue
                tid = str(rec.get("trade_id") or "")
                key = (tid, kind) if tid else None
                if key is not None:
                    if key in seen:
                        continue
                    seen.add(key)
                try:
                    rec["ts"] = float(rec.get("ts", 0))
                    rec["r"] = float(rec.get("r", 0))
                    rec["total_r"] = float(rec.get("total_r", rec["r"]) or rec["r"])
                except (TypeError, ValueError):
                    continue
                rows.append(rec)
    except OSError:
        return rows
    rows.sort(key=lambda t: t["ts"])
    return rows


def latest_trade(rows: list[dict] | None = None) -> dict | None:
    rows = load_trades() if rows is None else rows
    return rows[-1] if rows else None


def latest_positive(rows: list[dict] | None = None) -> dict | None:
    """Последняя закрытая сделка в плюс — её и показываем на скрине."""
    rows = load_trades() if rows is None else rows
    wins = [t for t in rows if float(t.get("total_r", t.get("r", 0)) or 0) > 0.001]
    return wins[-1] if wins else None


def week_stats(rows: list[dict] | None = None,
               now_ts: float | None = None) -> dict:
    rows = load_trades() if rows is None else rows
    cutoff = (time.time() if now_ts is None else now_ts) - 7 * 86400
    week = [t for t in rows if float(t.get("ts", 0)) >= cutoff]
    n = len(week)
    total = sum(float(t.get("total_r", t.get("r", 0)) or 0) for t in week)
    wins = sum(1 for t in week
               if float(t.get("total_r", t.get("r", 0)) or 0) > 0.001)
    return {"n": n, "r": total, "win": 100.0 * wins / n if n else 0.0}


def _trades_word(n: int) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return "сделка"
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return "сделки"
    return "сделок"


def trade_line(trade: dict | None, lang: str = "ru") -> str:
    if not trade:
        return ""
    side_raw = str(trade.get("side") or "").upper()
    if lang == "en":
        side = "long" if side_raw == "LONG" else "short"
    else:
        side = "лонг" if side_raw == "LONG" else "шорт"
    r = float(trade.get("total_r", trade.get("r", 0)) or 0)
    return f"{trade.get('symbol', '')} {side}, {r:+.1f}R"


def stats_line(rows: list[dict] | None = None,
               now_ts: float | None = None, lang: str = "ru") -> str:
    s = week_stats(rows, now_ts)
    if lang == "en":
        if not s["n"]:
            return "No trades in the last 7 days — waiting for a setup."
        return (f"Last 7 days: {s['n']} trades, {s['r']:+.1f}R, "
                f"{s['win']:.0f}% win.")
    if not s["n"]:
        return "За 7 дней сделок не было — жду сетап, а не повод зайти."
    return (f"За 7 дней: {s['n']} {_trades_word(s['n'])}, {s['r']:+.1f}R, "
            f"{s['win']:.0f}% в плюс.")


def image_for(trade: dict | None) -> str:
    """Картинка сделки; если её нет — дорисовать карточку результата."""
    if not trade:
        return ""
    path = str(trade.get("image") or "")
    if path and os.path.exists(path):
        return path
    os.makedirs(RENDERS, exist_ok=True)
    tid = str(trade.get("trade_id") or int(float(trade.get("ts", time.time()))))
    out = os.path.join(RENDERS, f"social_result_{tid}.png")
    if os.path.exists(out):
        return out
    try:
        card.render_result_card(
            symbol=str(trade.get("symbol", "")),
            side=str(trade.get("side", "")),
            kind=str(trade.get("kind", "") or "tp"),
            r=float(trade.get("total_r", trade.get("r", 0)) or 0),
            entry=trade.get("entry"),
            stop=trade.get("stop"),
            exit_px=trade.get("exit"),
            note=str(trade.get("note", "") or ""),
            tf=str(trade.get("tf", "15")),
            out_path=out,
        )
        return out
    except Exception:
        return ""


def ru_lines(trade: dict | None = None, note: str | None = None,
             with_stats: bool = True) -> list[str]:
    """Строки поста от первого лица: сделка + разработка + цифры."""
    trade = latest_positive() if trade is None else trade
    note = devlog.note_for_day() if note is None else note
    lines = ["Вот мой пост на сегодня."]
    if trade:
        lines.append(f"По сделкам: последняя закрытая в плюс — "
                     f"{trade_line(trade)}. Скрин ниже.")
    if note:
        lines.append(f"Что делал по проекту: {note}.")
    if with_stats:
        lines.append(stats_line())
    return lines


def ru_text(trade: dict | None = None, note: str | None = None,
            with_stats: bool = True, with_cta: bool = True) -> str:
    lines = ru_lines(trade=trade, note=note, with_stats=with_stats)
    if with_cta:
        handle = (cfg.CHANNEL_HANDLE or "@KidzordFX").lstrip("@")
        lines.append(f"Журнал сделок и сигналы — в Telegram: t.me/{handle}")
    return "\n".join(lines)


def _clip_note(text: str, limit: int) -> str:
    """Обрезать заметку по естественной границе, а не посреди «если…»."""
    text = " ".join(str(text).split()).rstrip(".")
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in (" — ", ", ", ". ", " "):
        pos = cut.rfind(sep)
        if pos >= 40:
            cut = cut[:pos]
            break
    return cut.rstrip(" ,;:—") + "…"


def ru_short(trade: dict | None = None, note: str | None = None,
             note_limit: int = 70) -> str:
    """Короткая версия для Threads: лимит 500 БАЙТ, кириллица ест два."""
    trade = latest_positive() if trade is None else trade
    note = devlog.note_for_day() if note is None else note
    lines = ["Вот мой пост на сегодня."]
    if trade:
        lines.append(f"Сделка в плюс: {trade_line(trade)}. Скрин ниже.")
    if note:
        lines.append(f"В разработке: {_clip_note(note, note_limit)}.")
    lines.append(stats_line())
    return "\n".join(lines)
