"""Ретеншн картинок и скриншотов: старые файлы удаляются, свежие не трогаем.

Растёт от рендера карточек и приёма скриншотов, а не от кода. Поэтому чистим
только перечисленные папки и только обычные файлы старше N дней. По умолчанию
команда ничего не удаляет — сначала показывает, сколько освободит.

    python tools/prune.py                # показать
    python tools/prune.py --days 60      # другой порог
    python tools/prune.py --apply        # удалить

Автоматически запускается из autoposts.py раз в сутки (PRUNE_TIME).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as cfg

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# только эти папки; всё остальное не трогаем ни при каких условиях
TARGETS = ("renders", "tg_screenshots", "discord_inbox", "preview")


def _human(size: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.1f} {unit}" if unit != "Б" else f"{int(size)} Б"
        size /= 1024
    return f"{size:.1f} ГБ"


def prune(days: int = 30, *, apply: bool = False,
          dirs: dict[str, str] | None = None,
          now_ts: float | None = None) -> dict:
    """Удаляет файлы старше `days`. Возвращает статистику по каждой папке."""
    now = time.time() if now_ts is None else float(now_ts)
    cutoff = now - max(0, int(days)) * 86400
    targets = dirs if dirs is not None else {
        name: os.path.join(HERE, name) for name in TARGETS}
    report: dict[str, dict] = {}
    for name, folder in targets.items():
        stats = {"files": 0, "bytes": 0, "deleted": 0, "freed": 0,
                 "missing": not os.path.isdir(folder)}
        if not stats["missing"]:
            for entry in os.scandir(folder):
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if info.st_mtime >= cutoff:
                    continue
                stats["files"] += 1
                stats["bytes"] += info.st_size
                if apply:
                    try:
                        os.unlink(entry.path)
                        stats["deleted"] += 1
                        stats["freed"] += info.st_size
                    except OSError:
                        pass
        report[name] = stats
    return report


def text_report(report: dict, days: int, applied: bool) -> str:
    lines = [f"ретеншн {days} дн. — " + ("удалено" if applied else "к удалению")]
    total_files = total_bytes = 0
    for name, stats in report.items():
        if stats["missing"]:
            lines.append(f"  {name}: нет папки")
            continue
        total_files += stats["files"]
        total_bytes += stats["bytes"]
        verb = "удалено" if applied else "старше порога"
        lines.append(f"  {name}: {stats['files']} файлов, "
                     f"{_human(stats['bytes'])} {verb}")
    lines.append(f"итого: {total_files} файлов · {_human(total_bytes)}"
                 + ("" if applied else " (запусти с --apply, чтобы удалить)"))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Удаление старых картинок и скриншотов")
    ap.add_argument("--days", type=int, default=int(getattr(cfg, "PRUNE_DAYS", 30)))
    ap.add_argument("--apply", action="store_true", help="действительно удалить")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    args = ap.parse_args()
    report = prune(args.days, apply=args.apply)
    if args.json:
        print(json.dumps({"days": args.days, "apply": args.apply,
                          "report": report}, ensure_ascii=False, indent=2))
    else:
        print(text_report(report, args.days, args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
