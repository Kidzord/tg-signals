"""SQLite-хранилище для того, что нельзя терять при перезапуске.

Три вещи живут здесь, а не в JSON:

* ``deliveries`` — очередь доставки сигналов и результатов подписчикам.
  У каждой строки свой статус, число попыток и время следующей попытки,
  поэтому падение процесса не теряет и не дублирует сообщения.
* ``payments`` — журнал платежей. Уникальный ``(provider, external_id)``
  делает выдачу подписки идемпотентной: повторный вебхук Stars или
  повторный опрос Crypto Pay не продлит доступ дважды.
* ``group_outbox`` — отложенные посты в бесплатную группу. Раньше они
  лежали в списке в памяти bot.py и пропадали при перезапуске.

Файл общий для нескольких процессов: bot.py (3 шт.), subbot.py и
служебные скрипты. Поэтому WAL + busy_timeout, а строки забираются
транзакцией со статусом ``sending``.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("STATE_DB", "").strip() or os.path.join(HERE, "state.db")

# Сколько «зависшая» строка может висеть в sending, прежде чем считаем,
# что процесс упал и её можно забрать заново.
STALE_SEC = 300

SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    uid         TEXT NOT NULL,
    trade_id    TEXT NOT NULL,
    kind        TEXT NOT NULL,              -- entry | tp1 | tp2 | tp3 | sl | tp | be | time
    symbol      TEXT NOT NULL DEFAULT '',
    side        TEXT NOT NULL DEFAULT '',
    token       TEXT NOT NULL DEFAULT 'sub',
    chat_id     TEXT NOT NULL,
    image_path  TEXT,
    caption     TEXT NOT NULL,
    message_id  INTEGER,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending|sending|sent|failed|blocked|skipped
    attempts    INTEGER NOT NULL DEFAULT 0,
    next_try    REAL NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    UNIQUE(uid, trade_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_deliveries_due ON deliveries(status, next_try);
CREATE INDEX IF NOT EXISTS idx_deliveries_trade ON deliveries(trade_id);

CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT NOT NULL,              -- stars|crypto|promo|trial|ref|manual
    external_id TEXT NOT NULL,
    uid         TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT '',   -- src_threads, src_tiktok, ...
    amount      REAL NOT NULL DEFAULT 0,
    currency    TEXT NOT NULL DEFAULT '',
    days        INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'paid',  -- paid|refunded
    raw         TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    UNIQUE(provider, external_id)
);
CREATE INDEX IF NOT EXISTS idx_payments_uid ON payments(uid);
CREATE INDEX IF NOT EXISTS idx_payments_created ON payments(created_at);

CREATE TABLE IF NOT EXISTS group_outbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    symbol      TEXT NOT NULL DEFAULT '',
    path        TEXT NOT NULL,
    caption     TEXT NOT NULL,
    entry       REAL,
    side        TEXT,
    due_ts      REAL NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    attempts    INTEGER NOT NULL DEFAULT 0,
    next_try    REAL NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    UNIQUE(trade_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_group_due ON group_outbox(status, next_try);

CREATE TABLE IF NOT EXISTS nurture (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    uid         TEXT NOT NULL,
    step        TEXT NOT NULL,
    due_ts      REAL NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    attempts    INTEGER NOT NULL DEFAULT 0,
    next_try    REAL NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    UNIQUE(uid, step)
);
CREATE INDEX IF NOT EXISTS idx_nurture_due ON nurture(status, next_try);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    # isolation_level=None — сами управляем транзакциями через BEGIN IMMEDIATE.
    con = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_db() -> None:
    con = connect()
    try:
        con.executescript(SCHEMA)
        # миграция для баз, созданных до появления symbol в group_outbox
        cols = {r["name"] for r in con.execute("PRAGMA table_info(group_outbox)")}
        if "symbol" not in cols:
            con.execute("ALTER TABLE group_outbox ADD COLUMN symbol TEXT NOT NULL DEFAULT ''")
        pay_cols = {r["name"] for r in con.execute("PRAGMA table_info(payments)")}
        if "source" not in pay_cols:
            con.execute("ALTER TABLE payments ADD COLUMN source TEXT NOT NULL DEFAULT ''")
        _recover_stale(con, "deliveries")
        _recover_stale(con, "group_outbox")
        _recover_stale(con, "nurture")
    finally:
        con.close()


def _recover_stale(con: sqlite3.Connection, table: str) -> None:
    cutoff = time.time() - STALE_SEC
    con.execute(
        f"UPDATE {table} SET status='pending', next_try=?, updated_at=? "
        f"WHERE status='sending' AND updated_at < ?",
        (time.time(), time.time(), cutoff),
    )


# --------------------------------------------------------------- deliveries

def enqueue_delivery(*, uid: str, trade_id: str, kind: str, chat_id: str,
                     caption: str, token: str = "sub", symbol: str = "",
                     side: str = "", image_path: str | None = None,
                     next_try: float | None = None) -> bool:
    """Поставить сообщение в очередь. Повторная постановка не дублирует."""
    now = time.time()
    con = connect()
    try:
        cur = con.execute(
            "INSERT OR IGNORE INTO deliveries "
            "(uid, trade_id, kind, symbol, side, token, chat_id, image_path, caption, "
            " status, attempts, next_try, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,'pending',0,?,?,?)",
            (str(uid), trade_id, kind, symbol, side, token, str(chat_id),
             image_path, caption, now if next_try is None else next_try, now, now),
        )
        return cur.rowcount > 0
    finally:
        con.close()


def claim_deliveries(limit: int = 20) -> list[sqlite3.Row]:
    """Забрать пачку готовых к отправке строк, пометив их sending."""
    now = time.time()
    con = connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute(
            "SELECT * FROM deliveries WHERE status='pending' AND next_try <= ? "
            "ORDER BY id LIMIT ?", (now, limit),
        ).fetchall()
        if rows:
            ids = [r["id"] for r in rows]
            q = ",".join("?" * len(ids))
            con.execute(
                f"UPDATE deliveries SET status='sending', updated_at=? WHERE id IN ({q})",
                (now, *ids),
            )
        con.execute("COMMIT")
        return rows
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def mark_delivery_sent(delivery_id: int, message_id: int | None = None) -> None:
    con = connect()
    try:
        con.execute(
            "UPDATE deliveries SET status='sent', message_id=COALESCE(?, message_id), "
            "last_error=NULL, updated_at=? WHERE id=?",
            (message_id, time.time(), delivery_id),
        )
    finally:
        con.close()


def mark_delivery_retry(delivery_id: int, error: str, delay: float) -> None:
    now = time.time()
    con = connect()
    try:
        con.execute(
            "UPDATE deliveries SET status='pending', attempts=attempts+1, "
            "next_try=?, last_error=?, updated_at=? WHERE id=?",
            (now + max(1.0, delay), str(error)[:500], now, delivery_id),
        )
    finally:
        con.close()


def mark_delivery_terminal(delivery_id: int, status: str, error: str = "") -> None:
    con = connect()
    try:
        con.execute(
            "UPDATE deliveries SET status=?, last_error=?, updated_at=? WHERE id=?",
            (status, str(error)[:500], time.time(), delivery_id),
        )
    finally:
        con.close()


def mark_user_blocked(uid: str, error: str = "") -> None:
    """Пользователь заблокировал бота: больше не шлём ему ничего."""
    con = connect()
    try:
        con.execute(
            "UPDATE deliveries SET status='blocked', last_error=?, updated_at=? "
            "WHERE uid=? AND status IN ('pending','sending')",
            (str(error)[:500], time.time(), str(uid)),
        )
    finally:
        con.close()


def entry_message_id(trade_id: str, uid: str) -> int | None:
    con = connect()
    try:
        row = con.execute(
            "SELECT message_id FROM deliveries WHERE trade_id=? AND uid=? AND kind='entry' "
            "AND status='sent'", (trade_id, str(uid)),
        ).fetchone()
        return int(row["message_id"]) if row and row["message_id"] else None
    finally:
        con.close()


def entry_recipients(trade_id: str) -> list[str]:
    """Кто получил карточку входа — им и обновляем её результатом."""
    con = connect()
    try:
        rows = con.execute(
            "SELECT uid FROM deliveries WHERE trade_id=? AND kind='entry' AND status='sent'",
            (trade_id,),
        ).fetchall()
        return [str(r["uid"]) for r in rows]
    finally:
        con.close()


def entry_targets(trade_id: str) -> list[str]:
    """Кому поставлен вход (включая ещё не отправленный) — цели результата."""
    con = connect()
    try:
        rows = con.execute(
            "SELECT DISTINCT uid FROM deliveries WHERE trade_id=? AND kind='entry' "
            "AND status IN ('pending','sending','sent')", (trade_id,),
        ).fetchall()
        return [str(r["uid"]) for r in rows]
    finally:
        con.close()


def entry_caption(trade_id: str, uid: str) -> str:
    con = connect()
    try:
        row = con.execute(
            "SELECT caption FROM deliveries WHERE trade_id=? AND uid=? AND kind='entry'",
            (trade_id, str(uid)),
        ).fetchone()
        return str(row["caption"]) if row else ""
    finally:
        con.close()


def entry_pending_exists(trade_id: str, uid: str) -> bool:
    """Есть ли ещё не отправленная карточка входа (результат должен подождать)."""
    con = connect()
    try:
        row = con.execute(
            "SELECT 1 FROM deliveries WHERE trade_id=? AND uid=? AND kind='entry' "
            "AND status IN ('pending','sending') LIMIT 1", (trade_id, str(uid)),
        ).fetchone()
        return row is not None
    finally:
        con.close()


def sent_entries_today(uid: str, since_ts: float) -> int:
    con = connect()
    try:
        row = con.execute(
            "SELECT COUNT(*) AS n FROM deliveries WHERE uid=? AND kind='entry' "
            "AND status='sent' AND created_at>=?", (str(uid), since_ts),
        ).fetchone()
        return int(row["n"]) if row else 0
    finally:
        con.close()


def delivery_stats() -> dict[str, Any]:
    con = connect()
    try:
        rows = con.execute(
            "SELECT status, COUNT(*) AS n, MIN(created_at) AS oldest "
            "FROM deliveries GROUP BY status"
        ).fetchall()
        out: dict[str, Any] = {}
        for r in rows:
            out[str(r["status"])] = {"n": int(r["n"]), "oldest": r["oldest"]}
        return out
    finally:
        con.close()


# ----------------------------------------------------------------- payments

def record_payment(*, provider: str, external_id: str, uid: str,
                   amount: float = 0.0, currency: str = "", days: int = 0,
                   source: str = "", raw: dict | None = None) -> bool:
    """Записать платёж. True — он новый, False — уже был (повтор вебхука)."""
    now = time.time()
    con = connect()
    try:
        cur = con.execute(
            "INSERT OR IGNORE INTO payments "
            "(provider, external_id, uid, source, amount, currency, days, status, raw, "
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,'paid',?,?,?)",
            (provider, str(external_id), str(uid), str(source),
             float(amount), currency, int(days),
             json.dumps(raw or {}, ensure_ascii=False), now, now),
        )
        return cur.rowcount > 0
    finally:
        con.close()


def mark_payment_refunded(*, provider: str, external_id: str) -> bool:
    con = connect()
    try:
        cur = con.execute(
            "UPDATE payments SET status='refunded', updated_at=? "
            "WHERE provider=? AND external_id=? AND status='paid'",
            (time.time(), provider, str(external_id)),
        )
        return cur.rowcount > 0
    finally:
        con.close()


def payment_days(provider: str, external_id: str) -> int:
    con = connect()
    try:
        row = con.execute(
            "SELECT days FROM payments WHERE provider=? AND external_id=?",
            (provider, str(external_id)),
        ).fetchone()
        return int(row["days"]) if row else 0
    finally:
        con.close()


def payment_totals(since_ts: float = 0.0) -> dict[str, float]:
    con = connect()
    try:
        rows = con.execute(
            "SELECT provider, status, COUNT(*) AS n, COALESCE(SUM(amount),0) AS amount "
            "FROM payments WHERE created_at>=? GROUP BY provider, status",
            (since_ts,),
        ).fetchall()
        out: dict[str, float] = {}
        for r in rows:
            key = f"{r['provider']}:{r['status']}"
            out[key] = out.get(key, 0.0) + float(r["amount"])
            out[f"{key}:n"] = out.get(f"{key}:n", 0.0) + float(r["n"])
        return out
    finally:
        con.close()


def payments_since(since_ts: float) -> list[sqlite3.Row]:
    con = connect()
    try:
        return con.execute(
            "SELECT * FROM payments WHERE created_at>=? ORDER BY created_at DESC",
            (since_ts,),
        ).fetchall()
    finally:
        con.close()


# -------------------------------------------------------------- group outbox

def enqueue_group(*, trade_id: str, kind: str, path: str, caption: str,
                  due_ts: float, entry: float | None = None,
                  side: str = "", symbol: str = "") -> bool:
    now = time.time()
    con = connect()
    try:
        cur = con.execute(
            "INSERT OR IGNORE INTO group_outbox "
            "(trade_id, kind, symbol, path, caption, entry, side, due_ts, status, "
            " attempts, next_try, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,'pending',0,?,?,?)",
            (trade_id, kind, symbol, path, caption, entry, side, due_ts, due_ts, now, now),
        )
        return cur.rowcount > 0
    finally:
        con.close()


def claim_group(limit: int = 10) -> list[sqlite3.Row]:
    now = time.time()
    con = connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute(
            "SELECT * FROM group_outbox WHERE status='pending' AND next_try <= ? "
            "ORDER BY due_ts, id LIMIT ?", (now, limit),
        ).fetchall()
        if rows:
            ids = [r["id"] for r in rows]
            q = ",".join("?" * len(ids))
            con.execute(
                f"UPDATE group_outbox SET status='sending', updated_at=? WHERE id IN ({q})",
                (now, *ids),
            )
        con.execute("COMMIT")
        return rows
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def mark_group_sent(row_id: int) -> None:
    con = connect()
    try:
        con.execute(
            "UPDATE group_outbox SET status='sent', last_error=NULL, updated_at=? WHERE id=?",
            (time.time(), row_id),
        )
    finally:
        con.close()


def mark_group_retry(row_id: int, error: str, delay: float) -> None:
    now = time.time()
    con = connect()
    try:
        con.execute(
            "UPDATE group_outbox SET status='pending', attempts=attempts+1, "
            "next_try=?, last_error=?, updated_at=? WHERE id=?",
            (now + max(1.0, delay), str(error)[:500], now, row_id),
        )
    finally:
        con.close()


def mark_group_terminal(row_id: int, status: str, error: str = "") -> None:
    con = connect()
    try:
        con.execute(
            "UPDATE group_outbox SET status=?, last_error=?, updated_at=? WHERE id=?",
            (status, str(error)[:500], time.time(), row_id),
        )
    finally:
        con.close()


def group_entry_due(trade_id: str) -> float | None:
    con = connect()
    try:
        row = con.execute(
            "SELECT due_ts FROM group_outbox WHERE trade_id=? AND kind='entry'",
            (trade_id,),
        ).fetchone()
        return float(row["due_ts"]) if row else None
    finally:
        con.close()


def group_pending_count() -> int:
    con = connect()
    try:
        row = con.execute(
            "SELECT COUNT(*) AS n FROM group_outbox WHERE status IN ('pending','sending')"
        ).fetchone()
        return int(row["n"]) if row else 0
    finally:
        con.close()


# ------------------------------------------------------------------ nurture

def enqueue_nurture(uid: str, step: str, due_ts: float) -> bool:
    now = time.time()
    con = connect()
    try:
        cur = con.execute(
            "INSERT OR IGNORE INTO nurture "
            "(uid, step, due_ts, status, attempts, next_try, created_at, updated_at) "
            "VALUES (?,?,?,'pending',0,?,?,?)",
            (str(uid), step, due_ts, due_ts, now, now),
        )
        return cur.rowcount > 0
    finally:
        con.close()


def claim_nurture(limit: int = 20) -> list[sqlite3.Row]:
    now = time.time()
    con = connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute(
            "SELECT * FROM nurture WHERE status='pending' AND next_try <= ? "
            "ORDER BY id LIMIT ?", (now, limit),
        ).fetchall()
        if rows:
            ids = [r["id"] for r in rows]
            q = ",".join("?" * len(ids))
            con.execute(
                f"UPDATE nurture SET status='sending', updated_at=? WHERE id IN ({q})",
                (now, *ids),
            )
        con.execute("COMMIT")
        return rows
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def mark_nurture_sent(row_id: int) -> None:
    con = connect()
    try:
        con.execute(
            "UPDATE nurture SET status='sent', last_error=NULL, updated_at=? WHERE id=?",
            (time.time(), row_id),
        )
    finally:
        con.close()


def mark_nurture_retry(row_id: int, error: str, delay: float) -> None:
    now = time.time()
    con = connect()
    try:
        con.execute(
            "UPDATE nurture SET status='pending', attempts=attempts+1, "
            "next_try=?, last_error=?, updated_at=? WHERE id=?",
            (now + max(1.0, delay), str(error)[:500], now, row_id),
        )
    finally:
        con.close()


def mark_nurture_terminal(row_id: int, status: str, error: str = "") -> None:
    con = connect()
    try:
        con.execute(
            "UPDATE nurture SET status=?, last_error=?, updated_at=? WHERE id=?",
            (status, str(error)[:500], time.time(), row_id),
        )
    finally:
        con.close()


def cancel_nurture(uid: str, steps: tuple[str, ...] = ()) -> None:
    """Отменить запланированные сообщения (например, после оплаты)."""
    con = connect()
    try:
        if steps:
            q = ",".join("?" * len(steps))
            con.execute(
                f"UPDATE nurture SET status='skipped', updated_at=? "
                f"WHERE uid=? AND status IN ('pending','sending') AND step IN ({q})",
                (time.time(), str(uid), *steps),
            )
        else:
            con.execute(
                "UPDATE nurture SET status='skipped', updated_at=? "
                "WHERE uid=? AND status IN ('pending','sending')",
                (time.time(), str(uid)),
            )
    finally:
        con.close()


def has_paid_subscription(uid: str) -> bool:
    """Была ли реальная оплата (Stars/крипта), а не триал или промокод."""
    con = connect()
    try:
        row = con.execute(
            "SELECT 1 FROM payments WHERE uid=? AND status='paid' "
            "AND provider IN ('stars','crypto') LIMIT 1", (str(uid),),
        ).fetchone()
        return row is not None
    finally:
        con.close()


# --------------------------------------------------------------------- meta

def meta_get(key: str, default: str = "") -> str:
    con = connect()
    try:
        row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default
    finally:
        con.close()


def meta_set(key: str, value: str) -> None:
    con = connect()
    try:
        con.execute(
            "INSERT INTO meta(key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
    finally:
        con.close()
