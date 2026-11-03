#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка и восстановление состояния из снимков backups/.

Бэкап, который ни разу не разворачивали, — это не бэкап. Скрипт умеет:
  list     — список снимков;
  check    — проверить целостность state.db и JSON-файлов;
  drill    — развернуть последний снимок в песочницу и проверить (ничего
             живого не трогает, временный каталог удаляется);
  restore  — развернуть снимок в указанный каталог. В корень проекта —
             только с --force, чтобы случайно не перезаписать живые данные.

Запуск:
    python tools/restore.py list
    python tools/restore.py check latest
    python tools/restore.py drill
    python tools/restore.py restore latest --dest C:\\temp\\restore --force
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backup  # noqa: E402

REQUIRED_TABLES = {"deliveries", "payments", "group_outbox", "nurture", "meta"}


def snapshots() -> list[Path]:
    base = Path(backup.BACKUP_DIR)
    if not base.exists():
        return []
    dirs = [p for p in base.iterdir()
            if p.is_dir() and not p.name.startswith(".tmp-")]
    return sorted(dirs, key=lambda p: p.stat().st_mtime)


def resolve(name: str | None) -> Path | None:
    if not name or name.lower() == "latest":
        last = backup.latest_snapshot()
        return Path(last) if last else None
    path = Path(name)
    if not path.is_absolute():
        path = Path(backup.BACKUP_DIR) / name
    return path if path.is_dir() else None


def _database_stats(db: Path) -> tuple[dict, list[str]]:
    errors: list[str] = []
    info: dict = {}
    if not db.exists():
        return info, ["нет state.db"]
    uri = f"file:{db.resolve().as_posix()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            row = con.execute("PRAGMA integrity_check").fetchone()
            info["integrity"] = str(row[0]) if row else "?"
            if info["integrity"].lower() != "ok":
                errors.append(f"state.db integrity_check = {info['integrity']}")
            tables = {str(r[0]) for r in
                      con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            info["tables"] = sorted(tables)
            missing = REQUIRED_TABLES - tables
            if missing:
                errors.append("в state.db нет таблиц: " + ", ".join(sorted(missing)))
            for table in ("payments", "deliveries", "group_outbox", "nurture"):
                if table in tables:
                    info[table] = con.execute(
                        f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error as e:
        errors.append(f"state.db не открывается: {e}")
    return info, errors


def _json_stats(path: Path) -> tuple[int | None, str | None]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return (len(data) if isinstance(data, (dict, list)) else None), None
    except (OSError, ValueError) as e:
        return None, f"{path.name}: {e}"


def _jsonl_stats(path: Path) -> tuple[int, list[str]]:
    bad: list[str] = []
    n = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    json.loads(line)
                except json.JSONDecodeError:
                    bad.append(f"{path.name}:{i}")
                    continue
                n += 1
    except OSError as e:
        bad.append(f"{path.name}: {e}")
    return n, bad


def inspect(snapshot: Path) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    info: dict = {"snapshot": snapshot.name}
    if not snapshot.is_dir():
        return {"ok": False, "errors": ["снимок не найден"], "warnings": []}
    if not (snapshot / ".ok").exists():
        warnings.append("нет маркера .ok — снимок мог не дозаписаться")

    db_info, db_errors = _database_stats(snapshot / "state.db")
    info.update(db_info)
    errors.extend(db_errors)

    files: list[str] = []
    for path in sorted(snapshot.iterdir()):
        if not path.is_file() or path.name == ".ok":
            continue
        files.append(path.name)
        if path.suffix == ".json":
            count, err = _json_stats(path)
            if err:
                errors.append(err)
            elif path.name == "users.json":
                info["users"] = count
        elif path.suffix == ".jsonl":
            count, bad = _jsonl_stats(path)
            info[f"{path.name}_lines"] = count
            errors.extend(bad)
    info["files"] = files

    users = snapshot / "users.json"
    if not users.exists():
        warnings.append("нет users.json")
    if not (snapshot / "trades.jsonl").exists():
        warnings.append("нет trades.jsonl — журнал сделок пуст или не сохранился")
    return {"ok": not errors, "errors": errors, "warnings": warnings, **info}


def _copy_snapshot(snapshot: Path, dest: Path, force: bool = False) -> list[str]:
    if dest.exists() and any(dest.iterdir()) and not force:
        raise RuntimeError(f"каталог {dest} не пуст — нужен --force")
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for path in snapshot.iterdir():
        if not path.is_file():
            continue
        shutil.copy2(path, dest / path.name)
        if path.name != ".ok":
            copied.append(path.name)
    return copied


def restore(snapshot: Path, dest: Path, force: bool = False) -> list[str]:
    if dest.resolve() == ROOT.resolve() and not force:
        raise RuntimeError("это корень проекта: живые данные перезаписываются "
                           "только с --force")
    if dest.resolve() == ROOT.resolve():
        # в корне разрешаем поверх живых файлов, но state.db должен быть целым
        preview = inspect(snapshot)
        if not preview["ok"]:
            raise RuntimeError("снимок не прошёл проверку: " +
                               "; ".join(preview["errors"]))
    return _copy_snapshot(snapshot, dest, force=force)


def drill() -> dict:
    """Развернуть последний снимок во временный каталог и проверить."""
    snapshot = resolve("latest")
    if not snapshot:
        return {"ok": False, "errors": ["нет ни одного снимка"], "warnings": []}
    with tempfile.TemporaryDirectory(prefix="restore-drill-") as tmp:
        dest = Path(tmp) / "restored"
        try:
            _copy_snapshot(snapshot, dest, force=True)
        except Exception as e:
            return {"ok": False, "snapshot": snapshot.name,
                    "errors": [f"копирование: {e}"], "warnings": []}
        res = inspect(dest)
        res["snapshot"] = snapshot.name
        res["restored_to"] = str(dest)
        return res


def _print_result(res: dict) -> None:
    print(f"снимок: {res.get('snapshot', '?')}")
    print("итог:", "OK" if res.get("ok") else "ОШИБКА")
    if res.get("tables"):
        print("таблицы:", ", ".join(res["tables"]))
    for key in ("payments", "deliveries", "group_outbox", "nurture",
                "users", "trades.jsonl_lines"):
        if key in res:
            print(f"{key}: {res[key]}")
    for err in res.get("errors") or []:
        print("  ОШИБКА:", err)
    for warn in res.get("warnings") or []:
        print("  предупреждение:", warn)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="restore из backups/")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="список снимков")
    p_check = sub.add_parser("check", help="проверить снимок")
    p_check.add_argument("name", nargs="?", default="latest")
    sub.add_parser("drill", help="пробное восстановление в песочницу")
    p_restore = sub.add_parser("restore", help="развернуть снимок в каталог")
    p_restore.add_argument("name", nargs="?", default="latest")
    p_restore.add_argument("--dest", required=True)
    p_restore.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "list":
        items = snapshots()
        if not items:
            print("снимков нет")
            return 1
        for path in items:
            print(path.name)
        return 0

    if args.cmd == "drill":
        res = drill()
        _print_result(res)
        return 0 if res["ok"] else 1

    snapshot = resolve(args.name)
    if not snapshot:
        print(f"снимок {args.name!r} не найден")
        return 1
    if args.cmd == "check":
        res = inspect(snapshot)
        _print_result(res)
        return 0 if res["ok"] else 1

    try:
        copied = restore(snapshot, Path(args.dest), force=args.force)
    except Exception as e:
        print("восстановление отменено:", e)
        return 1
    print(f"скопировано {len(copied)} файлов в {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
