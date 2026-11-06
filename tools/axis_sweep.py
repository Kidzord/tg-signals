#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Осевой перебор этапа 1: один параметр за прогон.

В отличие от `walk_forward.py`, здесь НЕТ подбора на окне оптимизации:
каждое значение оси прогоняется с фиксированными параметрами на тех же
OOS-окнах (15 дней, шаг 15). Так видно, что делает сам параметр, а не
шум повторного выбора.

Метрики и вердикт — те же, что в WFA (`wfa_verdict`), издержки — `costs`.
Знак каждого значения сравнивается с базовым (live) значением: если ось
переворачивает знак, это кандидат на подгонку, а не эдж.

    python tools/axis_sweep.py --strategy classic --symbol XAUUSD \
        --axis er_min --values 0.10,0.15,0.20,0.25

    # база/оверрайды: --base "chop_len=100,use_trail=1"
    # второй фид: --shift-hours 2 --rebuild-htf
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot
import costs
import wfa_verdict
from tools import walk_forward as wf

LIVE_RANGE = {
    "AUDUSD": {"er_max": 0.10, "tp_r": 3.0, "trend": "with"},
    "EURUSD": {"er_max": 0.0, "tp_r": 3.0, "trend": "with"},
    "USDCAD": {"er_max": 0.20, "tp_r": 3.0, "trend": "off"},
}


def coerce(raw, ref=None):
    """Привести строку CLI к типу базового значения."""
    text = str(raw).strip()
    low = text.lower()
    if isinstance(ref, bool) or low in ("true", "false"):
        if low in ("1", "true", "on", "yes"):
            return True
        if low in ("0", "false", "off", "no"):
            return False
        return bool(text)
    if isinstance(ref, int) and not isinstance(ref, bool):
        return int(float(text))
    if isinstance(ref, float):
        return float(text)
    if isinstance(ref, str):
        return text                      # htf_tf/touch_tf/sess_mode: не числа
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def parse_overrides(spec: str | None) -> dict:
    out: dict = {}
    for part in str(spec or "").split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        out[key.strip()] = coerce(value)
    return out


def _grid_seed(name: str) -> dict:
    """Зафиксированные параметры кандидата из wf.STRATEGIES[name]['grid'].

    Для кандидатов с одной записью (range_live, ict_gold3, ...) берём её
    целиком; для переборных — только поля, одинаковые во всех записях
    (side, stop_atr), чтобы не подмешать случайный TP/ER.
    """
    try:
        try:
            import walk_forward as wf
        except ImportError:
            # при импорте как tools.axis_sweep sibling не виден без пакета
            from tools import walk_forward as wf
        grid = (wf.STRATEGIES.get(name) or {}).get("grid") or []
        if not grid:
            return {}
        first = dict(grid[0])
        if len(grid) == 1:
            return first
        return {k: v for k, v in first.items()
                if all(g.get(k) == v for g in grid)}
    except Exception:
        return {}


def base_params(name: str, overrides: dict) -> dict:
    """База — live-параметры, чтобы ось сравнивалась с тем, что торгует бот."""
    if name == "classic":
        base = {f.name: getattr(bot.PARAMS, f.name)
                for f in dataclasses.fields(bot.PARAMS)}
    elif name in ("range", "range_short", "range_er10grid", "range_live",
                  "range_er10_tp3", "range_er10_tp4", "range_er0_tp3",
                  "range_er0_tp4", "range_er20_tp4", "range_er0_tp2",
                  "range_er20_tp2", "range_trend_eur", "range_trend_aud",
                  "range_cad_trail"):
        base = dict(stop_atr=bot.RANGE_STOP_ATR, tp_r=bot.RANGE_TP_R,
                    er_max=bot.RANGE_ER_MAX, side="both", trend=bot.RANGE_TREND,
                    max_stops=bot.RANGE_MAX_STOPS, tol_mult=0.2, frac=3,
                    stop_pts=4.0, stop_mode="points", swing_len=12,
                    stop_buf=0.0, max_stop_pts=20.0, adx_max=0.0)
        base.update(LIVE_RANGE.get(os.getenv("BOT_SYMBOL", "").upper(), {}))
        # Exness-тикеры с суффиксом m: база всё равно live-настройка символа
        sym = os.getenv("BOT_SYMBOL", "").upper()
        if sym.endswith("M"):
            base.update(LIVE_RANGE.get(sym[:-1], {}))
        base["max_r"] = float(base["tp_r"]) + 2.0
    elif name in ("ict", "ict_gold3", "ict_gold4", "ict_swing30"):
        base = dict(tp_r=bot.ICT_TP_R, stop_buf=bot.ICT_STOP_BUF,
                    swing=bot.ICT_SWING, fvg_window=bot.ICT_FVG_WINDOW,
                    retest_look=bot.ICT_RETEST_LOOK, max_bars=120, atr_len=14,
                    er_min=0.0, adx_min=0.0, session="", trend="off")
    elif name == "pdl":
        base = dict(tp_r=bot.PDL_TP_R, stop_buf_atr=bot.PDL_STOP_BUF_ATR,
                    max_bars=bot.PDL_MAX_BARS, atr_len=14,
                    er_min=0.0, adx_min=0.0, session="")
    elif name == "sonar":
        base = dict(target_r=bot.SONAR_TARGET_R, touches=bot.SONAR_TOUCHES)
    elif name == "crt":
        import crt
        p = crt.params_from_env(os.environ)
        base = {f.name: getattr(p, f.name) for f in dataclasses.fields(crt.CrtParams)}
    else:
        raise SystemExit(f"нет базы параметров для {name!r}")
    # Сначала фиксированные поля кандидата, затем явные --base переопределения.
    base.update(_grid_seed(name))
    base.update(overrides)
    return base


def run_value(cfg: dict, chart, htf, params: dict, spread: float,
              stress: float, delay: int) -> dict:
    """Фиксированные параметры на всех OOS-окнах: сделки и per-fold net."""
    runner = cfg["runner"]
    fold_net: list[float] = []
    fold_n: list[int] = []
    rs: list[float] = []
    gross = 0.0
    for a, b, c in wf._fold_bounds(len(chart.c)):
        trades = runner(wf.slice_frame(chart, b, c), htf, wf._apply(cfg, params), delay,
                        spread=spread)
        nets = costs.net_rs(trades, spread, stress=stress)
        fold_net.append(sum(nets))
        fold_n.append(len(trades))
        rs.extend(nets)
        gross += sum(float(t.get("r", 0.0)) for t in trades)
    active = [(n, net) for n, net in zip(fold_n, fold_net) if n > 0]
    active_pos = sum(1 for n, net in active if net > 0)
    net = sum(fold_net)
    avg = net / len(rs) if rs else 0.0
    return {
        "trades": len(rs),
        "gross_r": round(gross, 4),
        "net_r": round(net, 4),
        "avg_r": round(avg, 4),
        "folds": len(fold_net),
        "folds_active": len(active),
        "folds_active_positive": active_pos,
        "folds_active_share": round(active_pos / len(active), 4) if active else 0.0,
        "verdict": wfa_verdict.verdict(net, len(active), active_pos, avg),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Перебор одной оси на OOS-фолдах")
    ap.add_argument("--strategy", required=True, choices=sorted(wf.STRATEGIES))
    ap.add_argument("--symbol", default=os.getenv("BOT_SYMBOL", "XAUUSD"))
    ap.add_argument("--axis", required=True)
    ap.add_argument("--values", required=True, help="через запятую")
    ap.add_argument("--baseline", help="базовое значение оси (по умолчанию live)")
    ap.add_argument("--base", help="оверрайды базы: key=value,key=value")
    ap.add_argument("--bars", type=int, default=30000)
    ap.add_argument("--stress", type=float, default=costs.stress_from_env())
    ap.add_argument("--delay", type=int, default=costs.entry_delay_from_env())
    ap.add_argument("--shift-hours", type=float, default=0.0)
    ap.add_argument("--rebuild-htf", action="store_true")
    ap.add_argument("--exclude", default=os.getenv("WF_EXCLUDE", "").strip() or None)
    ap.add_argument("--json", help="путь для JSON")
    args = ap.parse_args()

    name = args.strategy
    cfg = wf.STRATEGIES[name]
    base = base_params(name, parse_overrides(args.base))
    ref = base.get(args.axis)
    if ref is None:
        raise SystemExit(f"оси {args.axis!r} нет в базе {name}")
    if args.baseline is not None:
        base_value = coerce(args.baseline, ref)
    else:
        base_value = ref
    values = [coerce(v, ref) for v in args.values.split(",") if v.strip()]

    data = bot.connect()
    if data is None:
        print("нет подключения к MT5")
        return 3
    chart, htf = bot.load_frames(data, args.symbol, bot.TF, args.bars)
    htf = wf.ensure_htf(data, args.symbol, chart, htf)
    chart, htf = wf.exclude_window(chart, htf, args.exclude)
    chart, htf = wf.shift_sessions(chart, htf, args.shift_hours)
    if args.rebuild_htf:
        htf = wf.rebuild_htf(chart, htf)
    spread = costs.symbol_spread(args.symbol)
    days = (chart.t[-1] - chart.t[0]) / 86400.0
    wf.BARS_PER_DAY = max(1, int(round(len(chart.c) / days)))
    data.close()
    folds_possible = len(list(wf._fold_bounds(len(chart.c))))
    print(f"{name}/{args.symbol}: {len(chart.c)} баров, {days:.0f} дн., "
          f"фолдов {folds_possible}, спред {spread:.5f} ×{args.stress:g}, "
          f"ось {args.axis}, база {base_value!r}")

    rows = []
    for value in values:
        params = dict(base)
        params[args.axis] = value
        res = run_value(cfg, chart, htf, params, spread, args.stress, args.delay)
        res["value"] = value
        res["is_baseline"] = value == base_value
        rows.append(res)

    base_row = next((r for r in rows if r["is_baseline"]), None)
    base_sign = (1 if base_row["net_r"] > 0 else -1 if base_row["net_r"] < 0 else 0) \
        if base_row else 0
    for r in rows:
        sign = 1 if r["net_r"] > 0 else -1 if r["net_r"] < 0 else 0
        r["sign_flip"] = bool(base_sign and sign and sign != base_sign
                              and not r["is_baseline"])

    print(f"{'значение':<16} {'сделок':>7} {'net R':>9} {'avg R':>8} "
          f"{'актив+':>9} {'вердикт':<12} знак")
    for r in rows:
        mark = "база" if r["is_baseline"] else ("МЕНЯЕТ" if r["sign_flip"] else "—")
        print(f"{str(r['value']):<16} {r['trades']:>7} {r['net_r']:>+9.2f} "
              f"{r['avg_r']:>+8.3f} "
              f"{r['folds_active_positive']:>4}/{r['folds_active']:<4} "
              f"{r['verdict']:<12} {mark}")

    flips = [str(r["value"]) for r in rows if r["sign_flip"]]
    if flips:
        print("ось нестабильна, знак меняют: " + ", ".join(flips))
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "strategy": name, "symbol": args.symbol, "axis": args.axis,
            "baseline": base_value, "base_params": base,
            "spread": spread, "stress": args.stress, "delay": args.delay,
            "shift_hours": args.shift_hours, "bars": len(chart.c),
            "rows": rows,
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"JSON: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
