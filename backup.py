"""Ежедневный бэкап состояния: SQLite + JSON-журналы.

SQLite копируется через backup API, а не копированием файла: так снимок
консистентен даже при активной записи из bot.py/subbot.py. Храним последние
14 снимков — этого достаточно, чтобы откатиться после неудачного обновления.
"""
from __future__ import annotations

import datetime as dt
import glob
import os
import shutil
import sqlite3
import sys

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
)


def snapshot() -> str:
    import store

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, stamp)
    os.makedirs(dest, exist_ok=True)

    src_db = store.DB_PATH
    if os.path.exists(src_db):
        src = sqlite3.connect(src_db, timeout=30)
        try:
            dst = sqlite3.connect(os.path.join(dest, "state.db"))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

    for name in FILES:
        path = os.path.join(HERE, name)
        if os.path.exists(path):
            shutil.copy2(path, os.path.join(dest, name))
    for path in glob.glob(os.path.join(HERE, "bot_state_*.json")):
        shutil.copy2(path, os.path.join(dest, os.path.basename(path)))
    for path in glob.glob(os.path.join(HERE, "hb_*.json")):
        shutil.copy2(path, os.path.join(dest, os.path.basename(path)))

    _prune()
    return dest


def _prune() -> None:
    dirs = sorted(d for d in glob.glob(os.path.join(BACKUP_DIR, "*")) if os.path.isdir(d))
    for path in dirs[:-KEEP]:
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    print(snapshot())
