#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Walk-forward для вшитых стратегий bot.py.

Методика (протокол IS -> OOS): скользящие фолды. На окне оптимизации
(60 дней) подбираем параметры из маленькой сетки по сумме R, затем
торгуем следующие 15 дней выбранными параметрами — эти сделки идут в
итог как out-of-sample. Склеиваем OOS-сделки всех фолдов.

Издержки — единая модель `costs.py`: `max(0.05R, спред/риск)` на сделку,
плюс cost stress (`--stress 2`) и задержка входа (`--delay 1`).

Вердикт считается по АКТИВНЫМ фолдам (где были сделки), а не по всем:
    net_R > 5  и  folds_active >= 8  и  доля активных в плюсе >= 60%
    и  avg_R > 0.05.
Для прошедших кандидатов печатается плато: соседи победителя по сетке
(±1 шаг одного параметра). Если сосед меняет знак net R — это подгонка.

Запуск:
    python tools/walk_forward.py              # все стратегии
    python tools/walk_forward.py classic      # одну
    python tools/walk_forward.py range --json out.json   # машинный результат
    python tools/walk_forward.py classic --stress 2 --shift-hours 1

Символ задаётся BOT_SYMBOL (по умолчанию XAUUSD). JSON нужен research.py,
чтобы гейт опирался на числа, а не на разбор консольного текста.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import bot
import costs
import feeds
import strategy as st
import wfa_verdict

BARS = int(os.getenv("WF_BARS", "18000"))
OPT_DAYS = 60
TEST_DAYS = 15
BARS_PER_DAY = 96          # M15, 24/7 (выходные дают запас)

# имена оставлены для совместимости с внешними отчётами
MIN_NET_R = wfa_verdict.MIN_NET_R
MIN_ACTIVE_FOLDS = wfa_verdict.MIN_ACTIVE_FOLDS
MIN_ACTIVE_POS_SHARE = wfa_verdict.MIN_ACTIVE_POS_SHARE
MIN_AVG_R = wfa_verdict.MIN_AVG_R


def slice_frame(chart, a: int, b: int):
    return st.Frame(chart.t[a:b], chart.o[a:b], chart.h[a:b],
                    chart.l[a:b], chart.c[a:b], chart.v[a:b])


def _drop_window(frame, start: float, end: float):
    """Убрать бары из окна [start, end) — для проверки без аномального участка."""
    keep = ~((frame.t >= start) & (frame.t < end))
    return st.Frame(frame.t[keep], frame.o[keep], frame.h[keep],
                    frame.l[keep], frame.c[keep], frame.v[keep])


def exclude_window(chart, htf, spec: str | None):
    """WF_EXCLUDE=2026-10-20:2026-10-26 — исключить дни из всех ТФ."""
    if not spec:
        return chart, htf
    import datetime as dt

    try:
        a, b = spec.split(":")
        start = dt.datetime.fromisoformat(a).replace(tzinfo=dt.timezone.utc).timestamp()
        end = dt.datetime.fromisoformat(b).replace(tzinfo=dt.timezone.utc).timestamp()
    except ValueError:
        print(f"WF_EXCLUDE: неверный формат {spec!r}, ожидается ГГГГ-ММ-ДД:ГГГГ-ММ-ДД")
        return chart, htf
    before = len(chart.c)
    out = _drop_window(chart, start, end)
    out_htf = {k: _drop_window(v, start, end) for k, v in htf.items()}
    print(f"исключено {before - len(out.c)} баров M15 за {spec}")
    return out, out_htf


def shift_sessions(chart, htf, hours: int):
    """Сдвинуть все метки времени на N часов: проверка артефакта сессии.

    Если эдж держится только при «правильном» сдвиге брокерского времени,
    это артефакт таймстампа, а не стратегия.
    """
    if not hours:
        return chart, htf
    sec = int(hours) * 3600
    out = st.Frame(chart.t + sec, chart.o, chart.h, chart.l, chart.c, chart.v)
    out_htf = {k: st.Frame(v.t + sec, v.o, v.h, v.l, v.c, v.v)
               for k, v in htf.items()}
    print(f"сессии сдвинуты на {hours:+g} ч")
    return out, out_htf


def _aggregate(frame, sec: int, week: bool = False):
    """Собрать старший ТФ из рабочего: те же границы, что у native."""
    if week:                                # неделя с понедельника 00:00
        day = frame.t // 86400
        keys = (day - ((day - 4) % 7)) * 86400
    else:
        keys = frame.t // sec * sec
    order = np.argsort(keys, kind="stable")
    keys, o, h, l, c, v = (keys[order], frame.o[order], frame.h[order],
                           frame.l[order], frame.c[order], frame.v[order])
    out = []
    start = 0
    for i in range(1, len(keys) + 1):
        if i == len(keys) or keys[i] != keys[start]:
            out.append((keys[start], o[start], h[start:i].max(),
                        l[start:i].min(), c[i - 1], v[start:i].sum()))
            start = i
    arr = np.array(out, dtype=float)
    return st.Frame(t=arr[:, 0].astype("int64"), o=arr[:, 1], h=arr[:, 2],
                    l=arr[:, 3], c=arr[:, 4], v=arr[:, 5])


def rebuild_htf(chart, htf):
    """Пересобрать H1/H4/D1/W1 из M15 — обязательный шаг на втором фиде.

    У брокеров разные границы сессий и сдвиг сервера; брокерские HTF дают
    classic неверный дневной контекст (−28R на Exness). Пересборка из
    рабочего ТФ возвращает результат к нулю.
    """
    out = {}
    for key, frame in htf.items():
        mins = feeds.TF_MINUTES.get(str(key).upper())
        if not mins:
            out[key] = frame
            continue
        out[key] = _aggregate(chart, int(mins) * 60,
                             week=str(key).upper() in ("1W", "W1"))
    print("старшие ТФ пересобраны из рабочего: " + ", ".join(out))
    return out


def ensure_htf(data, symbol, chart, htf, keys=("15", "60", "240", "1D", "1W")):
    """Догрузить ТФ, которых нет в htf (нужны для осей htf_tf/touch_tf)."""
    span_sec = float(chart.t[-1] - chart.t[0])
    out = dict(htf)
    for key in keys:
        if key in out:
            continue
        if str(key).upper() == str(bot.TF).upper():
            out[key] = chart
            continue
        mins = feeds.TF_MINUTES.get(str(key).upper())
        if not mins:
            continue
        need = int(span_sec / (int(mins) * 60)) + 320
        out[key] = data.frame(symbol, key, need)
    return out


# ---------------- runner'ы стратегий ----------------

def run_classic(chart, htf, params, delay: int = 0, spread: float = 0.0):
    res = st.evaluate(chart, htf, params)
    if not res.get("ok"):
        return []
    return bot.simulate(res, chart, params, entry_delay=delay)


def run_sonar(chart, htf, params, delay: int = 0, spread: float = 0.0):
    import sonar
    atr = sonar.build_atr(chart)
    levels = sonar.find_levels(chart, atr, min_touches=params["touches"])
    return sonar.backtest(chart, atr, levels, target_r=params["target_r"],
                          entry_delay=delay)


def run_range(chart, htf, params, delay: int = 0, spread: float = 0.0):
    import range_v2
    H = htf[bot.RANGE_ZONE_TF]
    zones = range_v2.zones_for_chart(chart, H, touches=2,
                                     flips_keep=bot.RANGE_FLIP_KEEP,
                                     tol_mult=params.get("tol_mult", 0.2),
                                     frac=int(params.get("frac", 3)))
    return range_v2.backtest(chart, zones, H,
                             stop_pts=params.get("stop_pts", 4.0),
                             stop_mode=params.get("stop_mode", "points"),
                             swing_len=int(params.get("swing_len", 12)),
                             stop_buf=params.get("stop_buf", 0.0),
                             max_stop_pts=params.get("max_stop_pts", 20.0),
                             stop_atr=params["stop_atr"],
                             tp_r=params["tp_r"],
                             max_r=params.get("max_r", params["tp_r"] + 2.0),
                             session="", smt=None, smt_look=0,
                             max_bars=bot.PARAMS.max_bars, er_max=params["er_max"],
                             side_allow=params.get("side", "both"),
                             trend_filter=params.get("trend", "off"),
                             trail_after_r=params.get("trail_after_r", 0.0),
                             trail_k=params.get("trail_k", 2.0),
                             d1_tol=params.get("d1_tol", 0.0),
                             max_stops=params.get("max_stops", bot.RANGE_MAX_STOPS),
                             entry_delay=delay,
                             adx_max=params.get("adx_max", 0.0),
                             spread=spread, max_spread_r=bot.SPREAD_MAX_R)


def run_ict(chart, htf, params, delay: int = 0, spread: float = 0.0):
    import ict2022
    H = htf.get(bot.PARAMS.htf_tf)
    return ict2022.backtest(chart, swing_len=params.get("swing", bot.ICT_SWING),
                            fvg_window=params.get("fvg_window", bot.ICT_FVG_WINDOW),
                            retest_look=params.get("retest_look", bot.ICT_RETEST_LOOK),
                            stop_buf_atr=params["stop_buf"],
                            tp_r=params["tp_r"],
                            max_bars=params.get("max_bars", 120),
                            atr_len=params.get("atr_len", 14),
                            entry_delay=delay,
                            er_min=params.get("er_min", 0.0),
                            adx_min=params.get("adx_min", 0.0),
                            session=params.get("session", ""),
                            htf_frame=H,
                            trend_filter=params.get("trend", "off"))


def run_pdl(chart, htf, params, delay: int = 0, spread: float = 0.0):
    import pdl_sweep
    return pdl_sweep.backtest(chart, tp_r=params.get("tp_r", bot.PDL_TP_R),
                              stop_buf_atr=params.get("stop_buf_atr", bot.PDL_STOP_BUF_ATR),
                              max_bars=params.get("max_bars", bot.PDL_MAX_BARS),
                              atr_len=params.get("atr_len", 14),
                              entry_delay=delay,
                              er_min=params.get("er_min", 0.0),
                              adx_min=params.get("adx_min", 0.0),
                              session=params.get("session", ""))


def run_crt(chart, htf, params, delay: int = 0, spread: float = 0.0):
    import crt
    H = htf.get(bot.CRT_RANGE_TF)
    if H is None:
        return []
    p = crt.params_from_env(os.environ)
    if params:
        p = dataclasses.replace(p, **params)
    return crt.backtest(chart, H, p, entry_delay=delay)


STRATEGIES = {
    "classic": {
        "runner": run_classic,
        "grid": [{"risk_max_atr": a, "min_rr": rr}
                 for a in (2.0, 4.0) for rr in (1.5, 2.0)],
        "apply": lambda p: dataclasses.replace(bot.PARAMS, **p),
    },
    "sonar": {
        "runner": run_sonar,
        "grid": [{"target_r": t, "touches": k}
                 for t in (1.5, 2.0, 3.0) for k in (2, 3)],
    },
    "range": {
        "runner": run_range,
        "grid": [{"tp_r": t, "stop_atr": 1.0, "er_max": e, "side": "both"}
                 for t in (2.0, 3.0, 4.0) for e in (0.0, 0.20)],
    },
    # тот же протокол, но сделки только в шорт: проверяем гипотезу стороны
    # как фиксированный вариант, а не как ещё один подбираемый параметр
    "range_short": {
        "runner": run_range,
        "grid": [{"tp_r": t, "stop_atr": 1.0, "er_max": e, "side": "short"}
                 for t in (2.0, 3.0, 4.0) for e in (0.0, 0.20)],
    },
    "range_er10grid": {
        "runner": run_range,
        "grid": [{"tp_r": t, "stop_atr": 1.0, "er_max": e, "side": "both"}
                 for t in (2.0, 3.0, 4.0) for e in (0.0, 0.10, 0.20)],
    },
    # фиксированные кандидаты: OOS без подбора, чистая проверка одной настройки
    "range_live": {
        "runner": run_range,
        "grid": [{"tp_r": 3.0, "stop_atr": 1.0, "er_max": 0.20, "side": "both"}],
    },
    "range_er10_tp3": {
        "runner": run_range,
        "grid": [{"tp_r": 3.0, "stop_atr": 1.0, "er_max": 0.10, "side": "both"}],
    },
    "range_er10_tp4": {
        "runner": run_range,
        "grid": [{"tp_r": 4.0, "stop_atr": 1.0, "er_max": 0.10, "side": "both"}],
    },
    "range_er0_tp3": {
        "runner": run_range,
        "grid": [{"tp_r": 3.0, "stop_atr": 1.0, "er_max": 0.0, "side": "both"}],
    },
    "range_er0_tp4": {
        "runner": run_range,
        "grid": [{"tp_r": 4.0, "stop_atr": 1.0, "er_max": 0.0, "side": "both"}],
    },
    "range_er20_tp4": {
        "runner": run_range,
        "grid": [{"tp_r": 4.0, "stop_atr": 1.0, "er_max": 0.20, "side": "both"}],
    },
    "range_er0_tp2": {
        "runner": run_range,
        "grid": [{"tp_r": 2.0, "stop_atr": 1.0, "er_max": 0.0, "side": "both"}],
    },
    "range_er20_tp2": {
        "runner": run_range,
        "grid": [{"tp_r": 2.0, "stop_atr": 1.0, "er_max": 0.20, "side": "both"}],
    },
    "range_trend_eur": {
        "runner": run_range,
        "grid": [{"tp_r": 3.0, "stop_atr": 1.0, "er_max": 0.0,
                  "side": "both", "trend": "with"}],
    },
    "range_trend_aud": {
        "runner": run_range,
        "grid": [{"tp_r": 3.0, "stop_atr": 1.0, "er_max": 0.10,
                  "side": "both", "trend": "with"}],
    },
    "range_cad_trail": {
        "runner": run_range,
        "grid": [{"tp_r": 3.0, "stop_atr": 1.0, "er_max": 0.20,
                  "side": "both", "trail_after_r": 1.0, "trail_k": 1.5}],
    },
    "ict": {
        "runner": run_ict,
        "grid": [{"tp_r": t, "stop_buf": b} for t in (2.0, 4.0) for b in (0.25, 0.5)],
    },
    # фиксированные кандидаты по золоту (отобраны на in-sample, проверяем OOS)
    "classic_nopart": {
        "runner": run_classic,
        "grid": [{"p1_pct": 0.0, "p2_pct": 0.0}],
        "apply": lambda p: dataclasses.replace(bot.PARAMS, **p),
    },
    "ict_gold3": {
        "runner": run_ict,
        "grid": [{"tp_r": 3.0, "stop_buf": 0.20}],
    },
    "ict_gold4": {
        "runner": run_ict,
        "grid": [{"tp_r": 4.0, "stop_buf": 0.25}],
    },
    "ict_swing30": {
        "runner": run_ict,
        "grid": [{"tp_r": 4.0, "stop_buf": 0.25, "swing": 30}],
    },
    # висящие статусы из docs: честный WFA вместо in-sample
    "pdl": {
        "runner": run_pdl,
        "grid": [{"tp_r": t, "stop_buf_atr": b}
                 for t in (1.5, 2.0, 3.0) for b in (0.15, 0.25, 0.4)],
    },
    "crt": {
        "runner": run_crt,
        "grid": [{"variant": v, "stop_buf_atr": b}
                 for v in ("classic", "failed") for b in (0.25, 0.5)],
    },
}


def _apply(cfg: dict, params: dict) -> dict:
    return cfg["apply"](params) if "apply" in cfg else params


def _fold_bounds(n: int):
    opt = OPT_DAYS * BARS_PER_DAY
    test = TEST_DAYS * BARS_PER_DAY
    a = 0
    while a + opt + test <= n:
        yield a, a + opt, a + opt + test
        a += test


def _run_fixed(cfg: dict, chart, htf, params: dict, spread: float,
               stress: float, delay: int) -> dict:
    """OOS-прогон ОДНОГО набора параметров на всех тестовых окнах (без подбора)."""
    runner = cfg["runner"]
    rs: list[float] = []
    net = 0.0
    for a, b, c in _fold_bounds(len(chart.c)):
        trades = runner(slice_frame(chart, b, c), htf, _apply(cfg, params), delay,
                        spread=spread)
        net += sum(costs.net_r(t, spread, stress=stress) for t in trades)
        rs.extend(t["r"] for t in trades)
    return {"trades": len(rs), "net_r": round(net, 4),
            "avg_r": round(net / len(rs), 4) if rs else 0.0}


def neighbors(grid: list[dict], params: dict) -> list[tuple[str, dict]]:
    """Соседи по сетке: ±1 шаг ровно одного параметра, остальные те же."""
    axes: dict[str, list] = {}
    for gp in grid:
        for key, value in gp.items():
            axes.setdefault(key, [])
            if value not in axes[key]:
                axes[key].append(value)
    out: list[tuple[str, dict]] = []
    for key, values in axes.items():
        if key not in params or len(values) < 2:
            continue
        try:
            ordered = sorted(values)
        except TypeError:
            continue
        try:
            pos = ordered.index(params[key])
        except ValueError:
            continue
        for npos in (pos - 1, pos + 1):
            if not (0 <= npos < len(ordered)):
                continue
            cand = dict(params)
            cand[key] = ordered[npos]
            if cand in grid and cand != params:
                out.append((f"{key}={ordered[npos]}", cand))
    return out


def plateau(cfg: dict, chart, htf, base: dict, spread: float,
            stress: float, delay: int) -> dict:
    """Отчёт о плато: база и соседи победителя на тех же OOS-окнах."""
    base_res = _run_fixed(cfg, chart, htf, base, spread, stress, delay)
    rows = [{"label": "база", "params": base, **base_res, "sign_flip": False}]
    base_sign = np.sign(base_res["net_r"])
    flips = []
    for label, params in neighbors(cfg["grid"], base):
        res = _run_fixed(cfg, chart, htf, params, spread, stress, delay)
        flip = bool(base_sign != 0 and np.sign(res["net_r"]) != base_sign)
        if flip:
            flips.append(f"{label}: {base_res['net_r']:+.2f}R -> {res['net_r']:+.2f}R")
        rows.append({"label": label, "params": params, **res, "sign_flip": flip})
    return {"rows": rows, "sign_flips": flips, "ok": not flips}


def verdict(net_r: float, folds_active: int, folds_active_pos: int,
            avg_r: float) -> str:
    """Единый вердикт: те же пороги, что в research.py и docs (wfa_verdict)."""
    return wfa_verdict.verdict(net_r, folds_active, folds_active_pos, avg_r)


def wfa(name: str, cfg: dict, chart, htf, spread: float = 0.0,
        stress: float = 1.0, delay: int = 0, want_plateau: bool = True) -> dict:
    runner = cfg["runner"]
    if runner is None:
        print(f"\n{name.upper()}: runner не задан (negative control) — пропуск")
        return {"name": name, "verdict": "НЕ ПРОХОДИТ", "trades": 0,
                "gross_r": 0.0, "net_r": 0.0, "avg_r": 0.0, "folds": 0,
                "folds_active": 0, "folds_active_positive": 0,
                "folds_positive": 0, "plateau": None}
    grid = cfg["grid"]
    print(f"\n{'=' * 70}\n{name.upper()} · фолды: окно {OPT_DAYS}д -> тест {TEST_DAYS}д, "
          f"сетка {len(grid)} комб. · спред {spread:.5f} ×{stress:g}"
          + (f" · задержка входа {delay} бар" if delay else ""))
    print(f"{'=' * 70}")

    all_oos: list[float] = []
    fold_rows = []
    best_params: dict | None = None
    for a, b, c in _fold_bounds(len(chart.c)):
        opt_slice = slice_frame(chart, a, b)
        test_slice = slice_frame(chart, b, c)
        when = time.strftime("%d.%m", time.gmtime(chart.t[b]))

        # 1) подбор на окне оптимизации (издержки учитываются при выборе)
        best_r, best = None, None
        for gp in grid:
            pp = _apply(cfg, gp)
            try:
                trades = runner(opt_slice, htf, pp, delay, spread=spread)
            except Exception as e:
                print(f"  ошибка на фолде {when}: {e!r}")
                return {"name": name, "verdict": "ОШИБКА", "error": repr(e),
                        "trades": 0, "gross_r": 0.0, "net_r": 0.0, "avg_r": 0.0,
                        "folds": 0, "folds_active": 0, "folds_active_positive": 0,
                        "folds_positive": 0, "plateau": None}
            # Конфиг без сделок не должен выигрывать IS с net=0.
            if len(trades) < 3:
                continue
            r = sum(costs.net_r(t, spread, stress=stress) for t in trades)
            if best_r is None or r > best_r:
                best_r, best = r, gp
        if best is None:
            best = grid[0]   # валидных на IS нет — пусть OOS покажет пустоту

        # 2) OOS-тест выбранных параметров
        pp = _apply(cfg, best)
        trades = runner(test_slice, htf, pp, delay, spread=spread)
        gross = sum(float(t["r"]) for t in trades)
        net = sum(costs.net_r(t, spread, stress=stress) for t in trades)
        all_oos.extend(costs.net_rs(trades, spread, stress=stress))
        fold_rows.append((when, best, len(trades), gross, net))
        best_params = best

    total_gross = sum(f[3] for f in fold_rows)
    total_net = sum(f[4] for f in fold_rows)
    pos_folds = sum(1 for f in fold_rows if f[4] > 0)
    active = [f for f in fold_rows if f[2] > 0]
    pos_active = sum(1 for f in active if f[4] > 0)
    avg = total_net / len(all_oos) if all_oos else 0.0
    share_active = pos_active / len(active) if active else 0.0

    print(f"{'фолд':>7} {'сделок':>7} {'raw R':>8} {'net R':>8}  параметры")
    for when, gp, cnt, raw, net in fold_rows:
        print(f"{when:>7} {cnt:>7} {raw:>+8.2f} {net:>+8.2f}  {gp}")
    print(f"\nOOS итог: {len(all_oos)} сделок · gross {total_gross:+.2f}R · "
          f"издержки {total_gross - total_net:+.2f}R · net {total_net:+.2f}R · "
          f"средняя {avg:+.3f}R")
    print(f"фолды: всего {len(fold_rows)} · активных {len(active)} · "
          f"активных в плюсе {pos_active}/{len(active)} "
          f"({share_active:.0%}) · всех в плюсе {pos_folds}/{len(fold_rows)}")

    base_verdict = verdict(total_net, len(active), pos_active, avg)
    plat = None
    if want_plateau and base_verdict == "ПРОХОДИТ" and best_params is not None:
        plat = plateau(cfg, chart, htf, best_params, spread, stress, delay)
        print("\nплато (соседи победителя на OOS, параметры фиксированы):")
        for row in plat["rows"]:
            mark = "  ← меняет знак" if row["sign_flip"] else ""
            print(f"  {row['label']:<22} {row['trades']:>4} сдел. "
                  f"net {row['net_r']:>+8.2f}R{mark}")
        if not plat["ok"]:
            print("ПЛАТО НЕ УСТОЙЧИВО: " + "; ".join(plat["sign_flips"]))

    final_verdict = base_verdict
    if plat is not None and not plat["ok"]:
        final_verdict = "НЕ ПРОХОДИТ (плато)"
    print(f"Вердикт walk-forward: {final_verdict}")
    return {
        "name": name,
        "verdict": final_verdict,
        "trades": len(all_oos),
        "gross_r": round(total_gross, 4),
        "net_r": round(total_net, 4),
        "avg_r": round(avg, 4),
        "folds": len(fold_rows),
        "folds_active": len(active),
        "folds_active_positive": pos_active,
        "folds_positive": pos_folds,
        "folds_active_share": round(share_active, 4),
        "spread": round(float(spread), 6),
        "stress": round(float(stress), 3),
        "entry_delay": int(delay),
        "params_best": best_params,
        "plateau": plat,
    }


def _cli_args(argv: list[str]) -> tuple[str | None, str | None, str | None]:
    """Разбирает «[имя_стратегии] [--json путь] [--exclude A:B]»."""
    only, json_path, exclude = None, None, None
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg == "--json":
            json_path = rest.pop(0) if rest else None
        elif arg.startswith("--json="):
            json_path = arg.split("=", 1)[1]
        elif arg == "--exclude":
            exclude = rest.pop(0) if rest else None
        elif arg.startswith("--exclude="):
            exclude = arg.split("=", 1)[1]
        elif not arg.startswith("-") and only is None:
            only = arg
    return only, json_path, exclude


def _cli_opts(argv: list[str]) -> dict:
    """--stress N, --shift-hours N, --delay N, --rebuild-htf, --no-plateau."""
    opts = {"stress": costs.stress_from_env(),
            "shift": 0, "delay": costs.entry_delay_from_env(),
            "plateau": True, "rebuild_htf": False}
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg in ("--stress", "--shift-hours", "--delay"):
            value = rest.pop(0) if rest else ""
            try:
                num = float(value)
            except ValueError:
                continue
            key = {"--stress": "stress", "--shift-hours": "shift",
                   "--delay": "delay"}[arg]
            opts[key] = (int(num) if arg == "--delay" else num)
        elif arg.startswith("--stress="):
            opts["stress"] = float(arg.split("=", 1)[1])
        elif arg.startswith("--shift-hours="):
            opts["shift"] = float(arg.split("=", 1)[1])
        elif arg.startswith("--delay="):
            opts["delay"] = int(float(arg.split("=", 1)[1]))
        elif arg == "--no-plateau":
            opts["plateau"] = False
        elif arg == "--rebuild-htf":
            opts["rebuild_htf"] = True
    return opts


def main() -> int:
    only, json_path, exclude = _cli_args(sys.argv[1:])
    opts = _cli_opts(sys.argv[1:])
    exclude = exclude or os.getenv("WF_EXCLUDE", "").strip() or None
    symbol = os.getenv("BOT_SYMBOL", "XAUUSD").strip()
    data = bot.connect()
    if data is None:
        print("нет подключения к MT5: запусти терминал, войди в счёт и повтори")
        return 3
    chart, htf = bot.load_frames(data, symbol, bot.TF, BARS)
    htf = ensure_htf(data, symbol, chart, htf)
    chart, htf = exclude_window(chart, htf, exclude)
    chart, htf = shift_sessions(chart, htf, opts["shift"])
    if opts["rebuild_htf"]:
        htf = rebuild_htf(chart, htf)
    spread = costs.symbol_spread(symbol)
    days = (chart.t[-1] - chart.t[0]) / 86400
    global BARS_PER_DAY
    # у индексов сессии короче суток: считаем фактические бары/день
    BARS_PER_DAY = max(1, int(round(len(chart.c) / days)))
    print(f"данные {symbol}: {len(chart.c)} баров ({days:.0f} дн., ~{BARS_PER_DAY}/день), "
          f"фолдов на стратегию ~{int((days - OPT_DAYS) // TEST_DAYS)}, "
          f"спред {spread:.5f} (env WF_SPREAD_{symbol} переопределяет)")
    names = [only] if only else list(STRATEGIES)
    results = []
    for name in names:
        if name not in STRATEGIES:
            print(f"неизвестная стратегия {name!r}; есть: {', '.join(STRATEGIES)}")
            data.close()
            return 2
        results.append(wfa(name, STRATEGIES[name], chart, htf, spread,
                           opts["stress"], opts["delay"], opts["plateau"]))
    data.close()
    if json_path:
        import json
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"JSON: {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
