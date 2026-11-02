"""Ежедневный бэкап состояния: SQLite + JSON-журналы.

SQLite копируется через backup API, а не копированием файла: так снимок
консистентен даже при активной записи из bot.py/subbot.py. Храним последние
14 снимков — этого достаточно, чтобы откатиться после неудачного обновления.

Снимок собирается во временный каталог и переименовывается только после
проверки целостности: полупустой бэкап не должен вытеснять целые.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
BACKUP_DIR = os.path.join(HERE, "backups")
KEEP = 14
FILES = (
    "users.json",
    "trades.jsonl",
    "signals_queue.jsonl",
    "results_queue.jsonl",
    "daily_risk.log",
    "sub_state.json",
    "owner.json",
    "heartbeat_state.json",
    "autoposts_state.json",
    "monitor_state.json",
    "crypto_paid.json",
    "tg_posts_used.json",
    "x_used.json",
    "vk_used.json",
    "dzen_used.json",
    "threads_used.json",
)


def _copy_state_db(dest_dir: str) -> None:
    import store

    src_db = store.DB_PATH
    if not os.path.exists(src_db):
        return
    src = sqlite3.connect(src_db, timeout=30)
    try:
        dst = sqlite3.connect(os.path.join(dest_dir, "state.db"))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    check = sqlite3.connect(os.path.join(dest_dir, "state.db"))
    try:
        row = check.execute("PRAGMA integrity_check").fetchone()
    finally:
        check.close()
    if not row or str(row[0]).lower() != "ok":
        raise RuntimeError(f"state.db: integrity_check = {row}")


def _verify_json_files(dest_dir: str) -> None:
    for name in os.listdir(dest_dir):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(dest_dir, name), encoding="utf-8") as f:
            json.load(f)


def snapshot() -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    tmp = os.path.join(BACKUP_DIR, f".tmp-{stamp}-{os.getpid()}")
    if os.path.exists(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        _copy_state_db(tmp)
        for name in FILES:
            path = os.path.join(HERE, name)
            if os.path.exists(path):
                shutil.copy2(path, os.path.join(tmp, name))
        for pattern in ("bot_state_*.json", "hb_*.json"):
            for path in glob.glob(os.path.join(HERE, pattern)):
                shutil.copy2(path, os.path.join(tmp, os.path.basename(path)))
        _verify_json_files(tmp)
        with open(os.path.join(tmp, ".ok"), "w", encoding="utf-8") as f:
            f.write(stamp)
        dest = os.path.join(BACKUP_DIR, stamp)
        if os.path.exists(dest):
            dest = f"{dest}-{os.getpid()}"
        os.replace(tmp, dest)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    _prune()
    return dest


def latest_snapshot() -> str | None:
    """Последний завершённый снимок (временные каталоги и мусор не считаются)."""
    dirs = [d for d in glob.glob(os.path.join(BACKUP_DIR, "*"))
            if os.path.isdir(d) and not os.path.basename(d).startswith(".tmp-")]
    if not dirs:
        return None
    return max(dirs, key=os.path.getmtime)


def ensure_fresh(max_age_h: float = 24.0) -> str | None:
    """Сделать снимок, если свежего нет (ПК спал в 03:00, процесс стартовал позже)."""
    last = latest_snapshot()
    if last:
        age_h = (time.time() - os.path.getmtime(last)) / 3600.0
        if 0 <= age_h <= max_age_h:
            return None
    return snapshot()


def _prune() -> None:
    dirs = [d for d in glob.glob(os.path.join(BACKUP_DIR, "*"))
            if os.path.isdir(d) and not os.path.basename(d).startswith(".tmp-")]
    dirs.sort(key=os.path.getmtime)
    for path in dirs[:-KEEP]:
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    print(snapshot())
