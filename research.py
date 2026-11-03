"""Реестр идей из внешних источников: гипотеза → проверка → вердикт.

Идея из чужого канала — не сигнал для ретрансляции, а повод проверить
правило на своих данных. Модуль фиксирует источник (это обязательное поле:
без атрибуции идею нельзя использовать), ведёт идею по статусам и не даёт
включить её в боевой флот без пройденного walk-forward.

    idea → testing → tested → promoted | rejected

Ворота promote проверяют результат WFA: вердикт ПРОХОДИТ, минимум сделок,
положительный net R и доля фолдов в плюсе не ниже порога. Promotion — это
запись решения в docs/RESEARCH_LOG.md, а не автодеплой: конфиг бота и состав
флота по-прежнему меняет человек.

Запуск:
    python research.py add --title "..." --source "@channel" --url ... \
        --hypothesis "..." --symbols AUDUSD --tf 15
    python research.py list
    python research.py wfa <id> --strategy range --symbol AUDUSD
    python research.py record <id> --strategy range --symbol AUDUSD \
        --trades 42 --net-r 18.5 --folds 5/7
    python research.py promote <id>
    python research.py reject <id> --note "OOS минус"
    python research.py log
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg

HERE = os.path.dirname(os.path.abspath(__file__))
STORE_PATH = os.path.join(HERE, "research.json")
LOG_PATH = os.path.join(HERE, "docs", "RESEARCH_LOG.md")

STATUSES = ("idea", "testing", "tested", "promoted", "rejected")
PASS_VERDICT = "ПРОХОДИТ"

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(text: str, limit: int = 40) -> str:
    out = []
    for ch in str(text).lower():
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isalnum():
            out.append(ch)
        else:
            out.append("-")
    slug = re.sub(r"-+", "-", "".join(out)).strip("-")
    return (slug or "idea")[:limit]


def _store_path() -> str:
    return getattr(cfg, "RESEARCH_PATH", "") or STORE_PATH


def _load() -> dict:
    target = _store_path()
    if not os.path.exists(target):
        return {"ideas": []}
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"ideas": []}
    if not isinstance(data, dict) or not isinstance(data.get("ideas"), list):
        return {"ideas": []}
    return data


def _save(data: dict) -> None:
    target = _store_path()
    os.makedirs(os.path.dirname(os.path.abspath(target)) or ".", exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, target)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _find(data: dict, idea_id: str) -> dict:
    for idea in data["ideas"]:
        if idea["id"] == idea_id:
            return idea
    raise KeyError(f"идея {idea_id!r} не найдена")


def add(title: str, source: str, *, url: str = "", hypothesis: str = "",
        symbols: str = "", tf: str = "") -> dict:
    """Регистрирует идею. Источник обязателен — в этом смысл воронки."""
    title = str(title).strip()
    source = str(source).strip()
    if not title:
        raise ValueError("title обязателен")
    if not source:
        raise ValueError("source обязателен: идея без атрибуции не принимается")
    data = _load()
    base = slugify(title)
    idea_id = base
    n = 2
    existing = {i["id"] for i in data["ideas"]}
    while idea_id in existing:
        idea_id = f"{base}-{n}"
        n += 1
    idea = {
        "id": idea_id,
        "title": title,
        "source": source,
        "url": str(url).strip(),
        "hypothesis": str(hypothesis).strip(),
        "symbols": [s.strip().upper() for s in str(symbols).split(",") if s.strip()],
        "tf": str(tf).strip(),
        "status": "idea",
        "created": _now(),
        "updated": _now(),
        "wfa": None,
        "notes": [],
    }
    data["ideas"].append(idea)
    _save(data)
    render_log()
    return idea


def note(idea_id: str, text: str) -> dict:
    text = str(text).strip()
    if not text:
        raise ValueError("пустая заметка")
    data = _load()
    idea = _find(data, idea_id)
    idea["notes"].append({"ts": _now(), "text": text})
    idea["updated"] = _now()
    _save(data)
    return idea


def set_status(idea_id: str, status: str, text: str = "") -> dict:
    if status not in STATUSES:
        raise ValueError(f"статус должен быть одним из {STATUSES}")
    data = _load()
    idea = _find(data, idea_id)
    idea["status"] = status
    idea["updated"] = _now()
    if text:
        idea["notes"].append({"ts": _now(), "text": f"[{status}] {text}"})
    _save(data)
    render_log()
    return idea


def record_wfa(idea_id: str, *, strategy: str, symbol: str, trades: int,
               net_r: float, folds: str = "0/0", avg_r: float = 0.0,
               verdict: str = "") -> dict:
    """Сохраняет результат WFA. Вердикт можно не передавать — он выводится."""
    data = _load()
    idea = _find(data, idea_id)
    folds_total, folds_positive = _parse_folds(folds)
    if not verdict:
        verdict = PASS_VERDICT if _passes(trades, net_r, folds_total, folds_positive) \
            else "НЕ ПРОХОДИТ"
    idea["wfa"] = {
        "strategy": str(strategy).strip(),
        "symbol": str(symbol).strip().upper(),
        "trades": int(trades),
        "net_r": round(float(net_r), 3),
        "avg_r": round(float(avg_r), 4),
        "folds_total": folds_total,
        "folds_positive": folds_positive,
        "verdict": verdict,
        "ran_at": _now(),
    }
    idea["status"] = "tested"
    idea["updated"] = _now()
    idea["notes"].append({
        "ts": _now(),
        "text": f"WFA {strategy}/{symbol}: {verdict}, {trades} сделок, "
                f"net {net_r:+.2f}R, фолды {folds_positive}/{folds_total}",
    })
    _save(data)
    render_log()
    return idea


def _parse_folds(folds: str) -> tuple[int, int]:
    """«5/7» → (7, 5): сначала всего, потом в плюсе."""
    try:
        a, b = str(folds).split("/")
        pos, total = int(a), int(b)
    except (ValueError, TypeError):
        return 0, 0
    return max(0, total), max(0, min(pos, total))


def _passes(trades: int, net_r: float, folds_total: int, folds_positive: int) -> bool:
    min_trades = int(getattr(cfg, "RESEARCH_MIN_TRADES", 20))
    min_share = float(getattr(cfg, "RESEARCH_MIN_POS_FOLDS", 0.6))
    if trades < min_trades or net_r <= 0:
        return False
    if folds_total <= 0:
        return False
    return (folds_positive / folds_total) >= min_share


def gate(idea_id: str) -> tuple[bool, list[str]]:
    """Проверяет, можно ли идею включать в боевой флот."""
    idea = _find(_load(), idea_id)
    wfa = idea.get("wfa")
    reasons: list[str] = []
    if not wfa:
        return False, ["нет результата WFA — идея не проверена"]
    if wfa.get("verdict") != PASS_VERDICT:
        reasons.append(f"вердикт WFA: {wfa.get('verdict')}")
    if idea["status"] == "rejected":
        reasons.append("идея отклонена")
    if not _passes(int(wfa.get("trades", 0)), float(wfa.get("net_r", 0.0)),
                   int(wfa.get("folds_total", 0)), int(wfa.get("folds_positive", 0))):
        reasons.append(
            f"порог не пройден: сделок {wfa.get('trades')} "
            f"(нужно ≥{cfg.RESEARCH_MIN_TRADES}), "
            f"net {float(wfa.get('net_r', 0.0)):+.2f}R (нужно >0), "
            f"фолды {wfa.get('folds_positive')}/{wfa.get('folds_total')} "
            f"(нужно ≥{cfg.RESEARCH_MIN_POS_FOLDS:.0%})"
        )
    return (not reasons), reasons


def promote(idea_id: str) -> tuple[bool, list[str]]:
    ok, reasons = gate(idea_id)
    if not ok:
        return False, reasons
    set_status(idea_id, "promoted")
    return True, []


def run_wfa(idea_id: str, strategy: str, symbol: str) -> dict:
    """Запускает tools/walk_forward.py и записывает результат в реестр."""
    _find(_load(), idea_id)  # проверяем id до долгого прогона
    script = os.path.join(HERE, "tools", "walk_forward.py")
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        env = dict(os.environ, BOT_SYMBOL=symbol)
        proc = subprocess.run(
            [sys.executable, script, "--json", tmp, strategy],
            cwd=HERE, env=env, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-8:]
            raise RuntimeError("walk-forward не отработал: " + " | ".join(tail))
        with open(tmp, "r", encoding="utf-8") as f:
            results = json.load(f)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    match = next((r for r in results if r.get("name") == strategy), None)
    if match is None:
        raise RuntimeError(f"в отчёте нет стратегии {strategy!r}")
    return record_wfa(
        idea_id, strategy=strategy, symbol=symbol,
        trades=int(match.get("trades", 0)), net_r=float(match.get("net_r", 0.0)),
        folds=f"{match.get('folds_positive', 0)}/{match.get('folds', 0)}",
        avg_r=float(match.get("avg_r", 0.0)), verdict=str(match.get("verdict", "")),
    )


def render_log() -> str:
    """Пишет docs/RESEARCH_LOG.md — единственный публичный след реестра."""
    ideas = sorted(_load()["ideas"], key=lambda i: i.get("created", ""), reverse=True)
    lines = [
        "# Журнал идей из внешних источников",
        "",
        "Файл генерируется `python research.py log` / любой командой реестра.",
        "Идея становится кандидатом только после пройденного walk-forward;",
        "promote здесь — запись решения, а не включение в боевой флот.",
        "",
        "| Идея | Источник | Символ | Статус | WFA |",
        "|---|---|---|---|---|",
    ]
    for idea in ideas:
        wfa = idea.get("wfa") or {}
        wfa_txt = "—"
        if wfa:
            wfa_txt = (f"{wfa.get('verdict', '?')}: {wfa.get('trades')} сдел., "
                       f"net {float(wfa.get('net_r', 0)):+.2f}R, "
                       f"фолды {wfa.get('folds_positive')}/{wfa.get('folds_total')}")
        source = idea.get("source", "")
        if idea.get("url"):
            source = f"[{source}]({idea['url']})"
        lines.append(
            f"| {idea.get('title', '')} | {source} | "
            f"{', '.join(idea.get('symbols') or []) or '—'} | "
            f"{idea.get('status', '')} | {wfa_txt} |"
        )
    text = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    return LOG_PATH


def _print_idea(idea: dict) -> None:
    print(f"{idea['id']}  [{idea['status']}]  {idea['title']}")
    print(f"  источник: {idea['source']}" + (f"  {idea['url']}" if idea.get("url") else ""))
    print(f"  символы: {', '.join(idea.get('symbols') or []) or '—'}  "
          f"ТФ: {idea.get('tf') or '—'}")
    if idea.get("hypothesis"):
        print(f"  гипотеза: {idea['hypothesis']}")
    if idea.get("wfa"):
        w = idea["wfa"]
        print(f"  WFA: {w['strategy']}/{w['symbol']} → {w['verdict']} · "
              f"{w['trades']} сделок · net {w['net_r']:+.2f}R · "
              f"фолды {w['folds_positive']}/{w['folds_total']} · {w['ran_at']}")
    for note_row in idea.get("notes", []):
        print(f"  · {note_row['ts']}: {note_row['text']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Реестр идей: источник → WFA → вердикт")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="зарегистрировать идею")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--source", required=True)
    p_add.add_argument("--url", default="")
    p_add.add_argument("--hypothesis", default="")
    p_add.add_argument("--symbols", default="")
    p_add.add_argument("--tf", default="")

    p_list = sub.add_parser("list", help="список идей")
    p_list.add_argument("--status", choices=STATUSES)

    p_show = sub.add_parser("show", help="детали идеи")
    p_show.add_argument("id")

    p_note = sub.add_parser("note", help="заметка к идее")
    p_note.add_argument("id")
    p_note.add_argument("text")

    p_rec = sub.add_parser("record", help="записать результат WFA вручную")
    p_rec.add_argument("id")
    p_rec.add_argument("--strategy", required=True)
    p_rec.add_argument("--symbol", required=True)
    p_rec.add_argument("--trades", type=int, required=True)
    p_rec.add_argument("--net-r", type=float, required=True)
    p_rec.add_argument("--folds", default="0/0", help="в плюсе/всего, например 5/7")
    p_rec.add_argument("--avg-r", type=float, default=0.0)
    p_rec.add_argument("--verdict", default="")

    p_wfa = sub.add_parser("wfa", help="прогнать walk-forward и записать результат")
    p_wfa.add_argument("id")
    p_wfa.add_argument("--strategy", required=True)
    p_wfa.add_argument("--symbol", required=True)

    p_prom = sub.add_parser("promote", help="пройти ворота и отметить кандидатом")
    p_prom.add_argument("id")

    p_rej = sub.add_parser("reject", help="отклонить идею")
    p_rej.add_argument("id")
    p_rej.add_argument("--note", default="")

    sub.add_parser("log", help="перегенерировать docs/RESEARCH_LOG.md")
    sub.add_parser("check", help="показать идеи без проверки и без вердикта")

    args = ap.parse_args()
    try:
        if args.cmd == "add":
            _print_idea(add(args.title, args.source, url=args.url,
                            hypothesis=args.hypothesis, symbols=args.symbols, tf=args.tf))
        elif args.cmd == "list":
            for idea in _load()["ideas"]:
                if args.status and idea["status"] != args.status:
                    continue
                print(f"{idea['id']:40} {idea['status']:9} {idea['title']}")
        elif args.cmd == "show":
            _print_idea(_find(_load(), args.id))
        elif args.cmd == "note":
            _print_idea(note(args.id, args.text))
        elif args.cmd == "record":
            _print_idea(record_wfa(args.id, strategy=args.strategy, symbol=args.symbol,
                                   trades=args.trades, net_r=args.net_r,
                                   folds=args.folds, avg_r=args.avg_r,
                                   verdict=args.verdict))
        elif args.cmd == "wfa":
            _print_idea(run_wfa(args.id, args.strategy, args.symbol))
        elif args.cmd == "promote":
            ok, reasons = promote(args.id)
            if ok:
                print(f"{args.id}: кандидат подтверждён, запись в {render_log()}")
            else:
                print(f"{args.id}: promote запрещён")
                for reason in reasons:
                    print("  · " + reason)
                return 1
        elif args.cmd == "reject":
            _print_idea(set_status(args.id, "rejected", args.note))
        elif args.cmd == "log":
            print(render_log())
        else:
            data = _load()
            pending = [i for i in data["ideas"]
                       if i["status"] in ("idea", "testing")]
            if not pending:
                print("нет идей без проверки")
            for idea in pending:
                print(f"{idea['id']:40} {idea['status']:9} {idea['title']}")
    except (KeyError, ValueError) as e:
        print(f"ошибка: {e}")
        return 1
    except RuntimeError as e:
        print(f"ошибка: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
