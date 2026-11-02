"""Воронка и деньги: активные подписки, конверсия триала, churn, поступления.

Считается по users.json (кто есть, у кого активна подписка) и по ledger
payments в SQLite (что и когда оплачено). Это то, чего не видно в обычном
/tstats: не «сколько R», а «сколько людей платят и сколько уходит».
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
USERS_PATH = os.path.join(HERE, "users.json")


def load_users() -> dict:
    try:
        with open(USERS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def metrics(days: int = 30) -> dict[str, Any]:
    import store

    users = load_users()
    now = time.time()
    cut = now - days * 86400
    all_pays = store.payments_since(0)
    paid_uids = {str(r["uid"]) for r in all_pays
                 if r["provider"] in ("stars", "crypto") and r["status"] == "paid"}

    active = 0
    blocked = 0
    trials = 0
    trial_converted = 0
    expired = 0
    for uid, u in users.items():
        u = u if isinstance(u, dict) else {}
        try:
            until = float(u.get("paid_until", 0) or 0)
        except (TypeError, ValueError):
            until = 0.0
        if u.get("blocked"):
            blocked += 1
        if until > now:
            active += 1
        elif cut <= until <= now:
            expired += 1
        if u.get("trial_used"):
            trials += 1
            if str(uid) in paid_uids:
                trial_converted += 1

    pays = store.payments_since(cut)
    new_paid_uids = {str(r["uid"]) for r in pays
                     if r["provider"] in ("stars", "crypto") and r["status"] == "paid"}
    revenue: dict[str, float] = {}
    for r in pays:
        if r["status"] != "paid" or r["provider"] not in ("stars", "crypto"):
            continue
        key = f"{r['provider']}:{r['currency'] or '?'}"
        revenue[key] = revenue.get(key, 0.0) + float(r["amount"] or 0)

    churn_base = active + expired
    return {
        "users": len(users),
        "active": active,
        "blocked": blocked,
        "trials": trials,
        "trial_converted": trial_converted,
        "trial_rate": (trial_converted / trials * 100.0) if trials else 0.0,
        "expired": expired,
        "churn_rate": (expired / churn_base * 100.0) if churn_base else 0.0,
        "new_paid": len(new_paid_uids),
        "payments": len(pays),
        "revenue": revenue,
    }


def text_report(days: int = 30) -> str:
    m = metrics(days)
    rev = " · ".join(f"{k}: {v:g}" for k, v in sorted(m["revenue"].items())) or "нет"
    return (
        f"💼 <b>Подписки · {days} дней</b>\n\n"
        f"Активных: <b>{m['active']}</b> · всего в базе: {m['users']} · "
        f"заблокировали: {m['blocked']}\n"
        f"Новых платящих: <b>{m['new_paid']}</b> · поступления: {rev}\n"
        f"Триалы: {m['trials']} · конверсия в оплату: <b>{m['trial_rate']:.0f}%</b>\n"
        f"Истекли без продления: {m['expired']} · churn <b>{m['churn_rate']:.0f}%</b>"
    )


def send_report(days: int = 30) -> bool:
    import config as cfg
    import tg

    if not cfg.OWNER_CHAT_ID:
        return False
    ok, info = tg.send_message(
        text_report(days), token=cfg.SUB_BOT_TOKEN or cfg.BOT_TOKEN,
        chat_id=cfg.OWNER_CHAT_ID)
    print("revenue:", ok, info)
    return ok
