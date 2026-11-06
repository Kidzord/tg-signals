#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Этап 4: портфельная симуляция живого флота.

Берём реальные бэктест-сделки четырёх live-ботов (classic XAUUSD + range
AUDUSD/USDCAD/EURUSD), переводим R в деньги при риске 0.5% текущего баланса
и проверяем портфельные правила:

  * лимит одновременного риска (по умолчанию 1%);
  * кросс-лок по RISK_GROUP (одна открытая сделка на группу);
  * недельный kill-switch: после −6R за неделю новые входы до конца недели
    запрещены.

Все сценарии считаются на одном и том же наборе сделок, поэтому разница —
это эффект самого правила, а не выборки. Инструмент ничего не пишет:
результат печатается JSON в stdout.

    python tools/portfolio_backtest.py
    python tools/portfolio_backtest.py --bars 30000
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot
import costs
import range_v2
import strategy as st

GROUPS = {"XAUUSD": "metals", "AUDUSD": "usd_fx", "USDCAD": "usd_fx", "EURUSD": "usd_fx"}
# Live-флот после 2026-11-05: AUDUSD/USDCAD выведены по WFA, остался EURUSD.
RANGE_LIVE = {
    "EURUSD": dict(er_max=0.0, tp_r=3.0, trend="with", spread=0.00004),
}


def _week(ts: float) -> str:
    d = dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).date()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def collect_trades(bars: int) -> list[dict]:
    data = bot.connect()
    if data is None:
        raise SystemExit("нет MT5")
    out: list[dict] = []
    try:
        # classic XAUUSD
        chart, htf = bot.load_frames(data, "XAUUSD", bot.TF, bars)
        res = st.evaluate(chart, htf, bot.PARAMS)
        if res.get("ok"):
            spread = costs.symbol_spread("XAUUSD", data.mt5) or 0.21
            for t in bot.simulate(res, chart, bot.PARAMS):
                risk = float(t.get("risk") or 0.0)
                net = float(t["r"]) - costs.cost_r(spread, risk)
                out.append({"sym": "XAUUSD", "side": "LONG" if t["dir"] > 0 else "SHORT",
                            "entry_ts": int(chart.t[t["i"]]),
                            "exit_ts": int(chart.t[t["ei"]]),
                            "r": net, "risk": risk})
        # range-боты
        for sym, cfg in RANGE_LIVE.items():
            chart, htf = bot.load_frames(data, sym, bot.TF, bars)
            H = htf[bot.RANGE_ZONE_TF]
            zones = range_v2.zones_for_chart(chart, H, touches=2,
                                             flips_keep=bot.RANGE_FLIP_KEEP)
            spread = costs.symbol_spread(sym, data.mt5) or cfg["spread"]
            trades = range_v2.backtest(
                chart, zones, H, stop_atr=1.0, tp_r=cfg["tp_r"],
                max_r=cfg["tp_r"] + 2.0, session="", smt=None, smt_look=0,
                max_bars=bot.PARAMS.max_bars, er_max=cfg["er_max"],
                side_allow="both", trend_filter=cfg["trend"],
                max_stops=bot.RANGE_MAX_STOPS, spread=spread,
                max_spread_r=bot.SPREAD_MAX_R)
            for t in trades:
                risk = float(t.get("risk") or 0.0)
                net = float(t["r"]) - costs.cost_r(spread, risk)
                out.append({"sym": sym, "side": t["side"],
                            "entry_ts": int(chart.t[t["entry_i"]]),
                            "exit_ts": int(chart.t[t["exit_i"]]),
                            "r": net, "risk": risk})
    finally:
        data.close()
    out.sort(key=lambda x: (x["entry_ts"], x["exit_ts"]))
    return out


def simulate(trades: list[dict], bank: float = 5000.0, risk_pct: float = 0.5,
             max_risk_pct: float = 1.0, group_limit: int = 1,
             kill_r: float = -6.0, use_kill: bool = False) -> dict:
    equity = bank
    peak = bank
    max_dd = 0.0
    open_trades: list[dict] = []
    week_r: dict[str, float] = {}
    accepted = blocked_cap = blocked_group = blocked_kill = 0
    realized: list[tuple[int, float]] = []

    def realize(until_ts: int) -> None:
        nonlocal equity, peak, max_dd, open_trades
        still = []
        for tr in sorted(open_trades, key=lambda x: x["exit_ts"]):
            if tr["exit_ts"] <= until_ts:
                pnl = tr["risk_amount"] * tr["r"]
                equity += pnl
                peak = max(peak, equity)
                if peak > 0:
                    max_dd = max(max_dd, (peak - equity) / peak)
                realized.append((tr["exit_ts"], tr["r"]))
                wk = _week(tr["exit_ts"])
                week_r[wk] = week_r.get(wk, 0.0) + tr["r"]
            else:
                still.append(tr)
        open_trades = still

    for tr in trades:
        realize(tr["entry_ts"])
        wk = _week(tr["entry_ts"])
        if use_kill and kill_r < 0 and week_r.get(wk, 0.0) <= kill_r:
            blocked_kill += 1
            continue
        group = GROUPS.get(tr["sym"], tr["sym"])
        if group_limit > 0 and sum(1 for o in open_trades
                                   if GROUPS.get(o["sym"], o["sym"]) == group) >= group_limit:
            blocked_group += 1
            continue
        risk_amount = equity * risk_pct / 100.0
        concurrent = sum(o["risk_amount"] for o in open_trades)
        if max_risk_pct > 0 and concurrent + risk_amount > equity * max_risk_pct / 100.0 + 1e-9:
            blocked_cap += 1
            continue
        open_trades.append({**tr, "risk_amount": risk_amount})
        accepted += 1
    realize(10 ** 18)

    worst_week = min(week_r.values()) if week_r else 0.0
    return {
        "accepted": accepted,
        "blocked_cap": blocked_cap,
        "blocked_group": blocked_group,
        "blocked_kill": blocked_kill,
        "final": round(equity, 2),
        "return_pct": round((equity / bank - 1.0) * 100.0, 2),
        "max_dd_pct": round(max_dd * 100.0, 2),
        "worst_week_r": round(worst_week, 2),
        "weeks": len(week_r),
        "win_share": round(sum(1 for _, r in realized if r > 0) / len(realized), 3)
        if realized else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=30000)
    ap.add_argument("--risk", type=float, default=0.5)
    ap.add_argument("--max-risk", type=float, default=1.0)
    ap.add_argument("--kill-r", type=float, default=-6.0)
    args = ap.parse_args()

    trades = collect_trades(args.bars)
    print(f"сделок во флоте: {len(trades)}", file=sys.stderr)
    scenarios = {
        "только риск 0.5%, без лимитов": simulate(
            trades, risk_pct=args.risk, max_risk_pct=0, group_limit=0),
        "лимит 1% + кросс-лок группы": simulate(
            trades, risk_pct=args.risk, max_risk_pct=args.max_risk, group_limit=1),
        "лимит 1% + кросс-лок + kill-switch -6R": simulate(
            trades, risk_pct=args.risk, max_risk_pct=args.max_risk, group_limit=1,
            kill_r=args.kill_r, use_kill=True),
    }
    print(f"{'сценарий':<42} {'сделок':>6} {'ret%':>7} {'maxDD':>7} "
          f"{'худ.нед':>8} {'блок':>12}")
    for name, r in scenarios.items():
        blocks = f"{r['blocked_cap']}/{r['blocked_group']}/{r['blocked_kill']}"
        print(f"{name:<42} {r['accepted']:>6} {r['return_pct']:>6.1f}% "
              f"{r['max_dd_pct']:>6.1f}% {r['worst_week_r']:>+8.1f} {blocks:>12}")
    print("блок = лимит риска / кросс-лок / kill-switch")
    print(json.dumps({"trades": len(trades), "scenarios": scenarios},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
