#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Paper-трекинг кандидатов: форвард без отправки сигналов и без live-сделок.

Кандидаты (из этапов 1–4):
  range_usdcad_atr12          — USDCAD range со stop_atr=1.2 (главный кандидат);
  classic_xau_real_yield      — classic XAUUSD без лонгов при росте DFII10;
  dual_momentum_idx           — месячный dual momentum по индексам;
  tsmom_idx_long              — индексный TSMOM long-only.

Состояние — SQLite `shadow.db` (таблица records, ключ candidate+id). Шаг
идемпотентен: пропущенный запуск или повторный старт ничего не ломает.
Никаких сигналов в Telegram и никаких изменений live-конфига.

    python shadow.py step          # один пересчёт
    python shadow.py report        # сводка
    python shadow.py loop          # шаг каждые N минут (для start_all)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import bot
import calendar_effects as ce
import costs
import external_data
import range_v2
import strategy as st
import tsmom

DB_PATH = os.path.join(ROOT, "shadow.db")
KEEP_DAYS = 180
MONTHS_KEEP = 36


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "CREATE TABLE IF NOT EXISTS records ("
        "candidate TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL, "
        "payload TEXT NOT NULL, updated TEXT NOT NULL, "
        "PRIMARY KEY (candidate, id))")
    return con


def _load() -> dict:
    con = _conn()
    try:
        rows = con.execute("SELECT candidate, id, payload FROM records").fetchall()
    finally:
        con.close()
    out = {}
    for candidate, rid, payload in rows:
        try:
            out[(candidate, rid)] = json.loads(payload)
        except ValueError:
            continue
    return out


def _save(recs: dict) -> None:
    con = _conn()
    try:
        with con:
            con.execute("DELETE FROM records")
            con.executemany(
                "INSERT INTO records (candidate, id, kind, payload, updated) "
                "VALUES (?, ?, ?, ?, ?)",
                [(rec["candidate"], rec["id"], rec.get("kind", "trade"),
                  json.dumps(rec, ensure_ascii=False), rec.get("updated", ""))
                 for rec in recs.values()])
    finally:
        con.close()


def _date(ts: float) -> str:
    return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).strftime("%Y-%m-%d")


def _real_rising(real: dict[str, float], date: str) -> bool:
    """Real yield вырос за 20 наблюдений ДО торгового дня (без same-day close)."""
    keys = sorted(real)
    pos = {k: i for i, k in enumerate(keys)}
    # Значение DFII10 за сам день известно только к его закрытию — берём
    # строго предыдущий доступный день, иначе внутридневной фильтр смотрит в будущее.
    key = (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()
    for _ in range(4000):
        if key in pos:
            break
        d = dt.date.fromisoformat(key)
        if d.year <= 1900:
            return False
        key = (d - dt.timedelta(days=1)).isoformat()
    else:
        return False
    i = pos[key]
    return i >= 20 and real[keys[i]] - real[keys[i - 20]] > 0


# ---------------- кандидаты ----------------

def range_records(chart, trades: list[dict], last_i: int,
                  candidate: str = "range_usdcad_atr12",
                  spread: float = 0.00005) -> list[dict]:
    """Записи range-кандидата; незакрытая на последнем баре сделка — open."""
    out = []
    cutoff = float(chart.t[-1]) - KEEP_DAYS * 86400
    for t in trades:
        entry_ts = int(chart.t[t["entry_i"]])
        if entry_ts < cutoff:
            continue
        risk = float(t.get("risk") or 0.0)
        sign = 1.0 if t["side"] == "LONG" else -1.0
        exit_px = t.get("exit_price")
        if exit_px is None:
            # range_v2 не кладёт exit_price: восстанавливаем из R и риска.
            exit_px = float(t["entry"]) + sign * float(t["r"]) * risk
        out.append({
            "candidate": candidate, "kind": "trade", "id": str(entry_ts),
            "symbol": "USDCAD", "side": t["side"],
            "entry_ts": entry_ts, "exit_ts": int(chart.t[t["exit_i"]]),
            "entry": float(t["entry"]),
            "exit_price": float(exit_px),
            "r": round(float(t["r"]) - costs.cost_r(spread, risk), 4),
            "exit_reason": t["exit"],
            # TP/SL на последнем баре — это закрытая сделка; open только MTM.
            "status": ("open" if t["exit_i"] >= last_i and t["exit"] == "TIME"
                       else "closed"),
            "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        })
    return out


def classic_records(chart, trades: list[dict], real: dict[str, float],
                    last_i: int, spread: float = 0.21,
                    candidate: str = "classic_xau_real_yield",
                    use_real: bool = True) -> list[dict]:
    """Classic с real-yield фильтром: лонги при росте DFII10 не берём.

    `candidate`/`use_real` позволяют вести отдельный paper-кандидат
    (например, classic на M5) с тем же форматом записей.
    """
    out = []
    cutoff = float(chart.t[-1]) - KEEP_DAYS * 86400
    for t in trades:
        entry_ts = int(chart.t[t["i"]])
        if entry_ts < cutoff:
            continue
        side = "LONG" if t["dir"] > 0 else "SHORT"
        blocked = use_real and side == "LONG" and _real_rising(real, _date(entry_ts))
        net = float(t["r"]) - costs.cost_r(spread, float(t.get("risk") or 0.0))
        out.append({
            "candidate": candidate, "kind": "trade",
            "id": str(entry_ts), "symbol": "XAUUSD", "side": side,
            "entry_ts": entry_ts, "exit_ts": int(chart.t[t["ei"]]),
            "entry": float(t["entry"]),
            "exit_price": float(t.get("exit_price", t["entry"])),
            "r": 0.0 if blocked else round(net, 4), "exit_reason": t["exit"],
            "status": "blocked" if blocked else
                      ("open" if t["ei"] >= last_i
                       and t["exit"] in ("TIME", "TIME_END") else "closed"),
            "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        })
    return out


def monthly_records(rows: list[dict], candidate: str, now_month: str,
                    extra: dict | None = None) -> list[dict]:
    out = []
    for r in rows[-MONTHS_KEEP:]:
        rec = {
            "candidate": candidate, "kind": "month", "id": r["month"],
            "month": r["month"], "net": round(float(r["net"]), 6),
            "status": "open" if r["month"] >= now_month else "closed",
            "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }
        if extra and r["month"] in extra:
            rec.update(extra[r["month"]])
        out.append(rec)
    return out


# ---------------- сборка ----------------

def step() -> int:
    recs = _load()
    now = dt.datetime.now(dt.timezone.utc)
    now_month = now.strftime("%Y-%m")
    if ("_meta", "started") not in recs:
        recs[("_meta", "started")] = {
            "candidate": "_meta", "id": "started", "kind": "meta",
            "ts": time.time(), "start_month": now_month, "status": "meta",
            "updated": now.isoformat(timespec="seconds"),
        }

    data = bot.connect()
    if data is None:
        print("shadow: нет MT5", file=sys.stderr)
        return 1
    try:
        # 1) USDCAD range, stop_atr=1.2
        chart, htf = bot.load_frames(data, "USDCAD", bot.TF, 4000)
        H = htf[bot.RANGE_ZONE_TF]
        zones = range_v2.zones_for_chart(chart, H, touches=2,
                                         flips_keep=bot.RANGE_FLIP_KEEP)
        trades = range_v2.backtest(
            chart, zones, H, stop_atr=1.2, tp_r=3.0, max_r=5.0, session="",
            smt=None, smt_look=0, max_bars=bot.PARAMS.max_bars, er_max=0.20,
            side_allow="both", trend_filter="off", max_stops=bot.RANGE_MAX_STOPS,
            spread=0.00005, max_spread_r=bot.SPREAD_MAX_R)
        for rec in range_records(chart, trades, len(chart.c) - 1):
            recs[(rec["candidate"], rec["id"])] = rec

        # 2) classic XAU с real-yield фильтром
        chart, htf = bot.load_frames(data, "XAUUSD", bot.TF, 4000)
        res = st.evaluate(chart, htf, bot.PARAMS)
        if res.get("ok"):
            trades = bot.simulate(res, chart, bot.PARAMS)
            real = external_data.fred_by_date("DFII10")
            for rec in classic_records(chart, trades, real, len(chart.c) - 1):
                recs[(rec["candidate"], rec["id"])] = rec

        # 2b) classic XAU M5 — скальпинг-кандидат, только paper
        chart5, htf5 = bot.load_frames(data, "XAUUSD", "5", 4000)
        res5 = st.evaluate(chart5, htf5, bot.PARAMS)
        if res5.get("ok"):
            trades5 = bot.simulate(res5, chart5, bot.PARAMS)
            for rec in classic_records(chart5, trades5, {}, len(chart5.c) - 1,
                                       spread=0.19, candidate="classic_xau_m5",
                                       use_real=False):
                recs[(rec["candidate"], rec["id"])] = rec
    finally:
        data.close()

    # 3) месячные кандидаты на внешних данных
    try:
        frames = {s: external_data.yahoo_frame(s)
                  for s in ("SPX", "NDX", "DAX", "N225")}
        costs_pct = {s: external_data.DEFAULT_SPREAD_PCT[s] for s in frames}
        dual = ce.dual_momentum(frames, 1, cost_pct=costs_pct)
        chosen = {r["month"]: {"chosen": r["chosen"]} for r in dual}
        for rec in monthly_records(dual, "dual_momentum_idx", now_month, chosen):
            recs[(rec["candidate"], rec["id"])] = rec

        series = {s: tsmom.monthly_series(f, long_only=True,
                                          spread_pct=costs_pct[s])
                  for s, f in frames.items()}
        for rec in monthly_records(tsmom.portfolio(series), "tsmom_idx_long",
                                   now_month):
            recs[(rec["candidate"], rec["id"])] = rec
    except Exception as e:
        print(f"shadow: месячные кандидаты пропущены: {e!r}", file=sys.stderr)

    _save(recs)
    print(f"shadow: записей {len(recs)}", file=sys.stderr)
    return 0


# ---------------- отчёт ----------------

def _max_dd(values: list[float]) -> float:
    peak = 0.0
    dd = 0.0
    for v in values:
        peak = max(peak, v)
        dd = max(dd, peak - v)
    return dd


def agg_trades(recs: list[dict]) -> dict:
    closed = [r for r in recs if r.get("status") == "closed"]
    blocked = [r for r in recs if r.get("status") == "blocked"]
    rs = [float(r["r"]) for r in closed]
    cum = 0.0
    curve = []
    for r in sorted(closed, key=lambda x: x["entry_ts"]):
        cum += float(r["r"])
        curve.append(cum)
    return {
        "closed": len(closed), "blocked": len(blocked),
        "open": sum(1 for r in recs if r.get("status") == "open"),
        "net_r": round(sum(rs), 2),
        "avg_r": round(sum(rs) / len(rs), 3) if rs else 0.0,
        "win_share": round(sum(1 for x in rs if x > 0) / len(rs), 3) if rs else 0.0,
        "max_dd_r": round(_max_dd(curve), 2),
    }


def agg_months(recs: list[dict]) -> dict:
    closed = sorted([r for r in recs if r.get("status") == "closed"],
                    key=lambda x: x["month"])
    eq = 1.0
    peak = 1.0
    dd = 0.0
    for r in closed:
        eq *= 1.0 + float(r["net"])
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak if peak else 0.0)
    nets = [float(r["net"]) for r in closed]
    open_rec = next((r for r in recs if r.get("status") == "open"), None)
    return {
        "months": len(closed),
        "return_pct": round((eq - 1.0) * 100.0, 2),
        "avg_month_pct": round(sum(nets) / len(nets) * 100.0, 2) if nets else 0.0,
        "win_share": round(sum(1 for x in nets if x > 0) / len(nets), 3) if nets else 0.0,
        "max_dd_pct": round(dd * 100.0, 2),
        "current": (open_rec.get("chosen") if open_rec and open_rec.get("chosen")
                    else (open_rec or {}).get("status", "—")),
    }


def _month_key(ts: float) -> str:
    return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).strftime("%Y-%m")


def _last_closed_month_index(frame) -> int:
    """Индекс последнего бара предыдущего полностью закрытого месяца."""
    m = _month_key(frame.t[-1])
    i = len(frame.c) - 1
    while i > 0 and _month_key(frame.t[i]) == m:
        i -= 1
    return i


def current_positions(frames: dict) -> tuple[list[str], str]:
    """Текущая позиция кандидатов по последнему закрытому месяцу.

    dual: top-1 по 12M среди индексов, иначе cash.
    tsmom: сколько из 4 индексов в лонге по знаку 12M.
    """
    scores = []
    longs = 0
    for sym, frame in frames.items():
        i = _last_closed_month_index(frame)
        if i < 252:
            continue
        mom = float(frame.c[i] / frame.c[i - 252] - 1.0)
        if mom > 0:
            scores.append((mom, sym))
            longs += 1
    scores.sort(reverse=True)
    return [s for _, s in scores[:1]], f"long {longs}/{len(frames)}"


def report() -> int:
    recs = _load()
    meta = recs.get(("_meta", "started"), {})
    start_ts = float(meta.get("ts", time.time()))
    start_month = meta.get("start_month", "0000-00")
    groups: dict[str, list[dict]] = {}
    for rec in recs.values():
        if rec.get("candidate") == "_meta":
            continue
        groups.setdefault(rec["candidate"], []).append(rec)
    started = dt.datetime.fromtimestamp(start_ts, dt.timezone.utc).strftime("%d.%m.%Y")
    print(f"Paper-трекинг кандидатов (live не затронут) · форвард с {started}\n")
    print(f"{'кандидат':<26} {'ист.сд':>6} {'ист.net':>9} "
          f"{'фв.сд':>5} {'фв.net':>9} {'open/block':>11}")
    for name in ("range_usdcad_atr12", "classic_xau_real_yield", "classic_xau_m5"):
        g = groups.get(name, [])
        a = agg_trades(g)
        fwd = [r for r in g if r.get("status") == "closed"
               and float(r.get("entry_ts", 0)) >= start_ts]
        fwd_net = sum(float(r["r"]) for r in fwd)
        print(f"{name:<26} {a['closed']:>6} {a['net_r']:>+8.2f}R "
              f"{len(fwd):>5} {fwd_net:>+8.2f}R {a['open']:>5}/{a['blocked']:<5}")
    print(f"\n{'кандидат':<26} {'ист.мес':>7} {'ист.доход':>10} "
          f"{'фв.мес':>6} {'фв.доход':>9}  текущая")
    positions = {}
    try:
        frames = {s: external_data.yahoo_frame(s)
                  for s in ("SPX", "NDX", "DAX", "N225")}
        dual_cur, tsmom_cur = current_positions(frames)
        positions["dual_momentum_idx"] = ",".join(dual_cur) or "cash"
        positions["tsmom_idx_long"] = tsmom_cur
    except Exception:
        pass
    for name in ("dual_momentum_idx", "tsmom_idx_long"):
        g = groups.get(name, [])
        a = agg_months(g)
        fwd = [r for r in g if r.get("status") == "closed"
               and r.get("month", "") >= start_month]
        eq = 1.0
        for r in fwd:
            eq *= 1.0 + float(r["net"])
        cur = positions.get(name, a["current"])
        if isinstance(cur, list):
            cur = ",".join(cur) or "cash"
        print(f"{name:<26} {a['months']:>7} {a['return_pct']:>+9.2f}% "
              f"{len(fwd):>6} {(eq - 1.0) * 100:>+8.2f}%  {cur}")
    if not groups:
        print("\nпока пусто — запусти `python shadow.py step`")
    return 0


def loop(interval_min: int) -> int:
    while True:
        try:
            step()
        except Exception as e:
            print(f"shadow loop error: {e!r}", file=sys.stderr)
        time.sleep(max(60, interval_min * 60))


def main() -> int:
    ap = argparse.ArgumentParser(description="Paper-трекинг кандидатов")
    ap.add_argument("cmd", choices=("step", "report", "loop"))
    ap.add_argument("--interval-min", type=int, default=5)
    args = ap.parse_args()
    if args.cmd == "step":
        return step()
    if args.cmd == "report":
        return report()
    return loop(args.interval_min)


if __name__ == "__main__":
    sys.exit(main())
