"""Candle Range Theory (CRT): диапазон C1 → свип C2 с возвратом внутрь → ход к цели.

Механическая версия модели, которую в тиктоке/ютубе продаёт RomeoTPT и десяток
разборов вокруг неё. Три свечи на старшем ТФ (по умолчанию H4):

  * C1 — «свеча-диапазон» (anchor): её high/low задают границы, тело не важно;
  * C2 — «манипуляция»: прокалывает РОВНО одну границу C1 и закрывается
    обратно внутрь диапазона. Закрытие снаружи = настоящий пробой, сетап не
    наш (для него есть отдельный вариант "failed");
  * C3 — «распределение»: ход к противоположной границе. В бот-версии не
    требуется закрытия C3: вход по закрытию C2, как в системных разборах
    (agm/LuxAlgo/ProEA), иначе сигнал приходит на целую свечу позже.

Сделка:
  * вход — по закрытию первого бара рабочего ТФ после закрытия C2;
  * стоп — за экстремум свипа C2 плюс буфер в ATR рабочего ТФ;
  * цель 1 — середина диапазона C1 (EQ), фиксируем половину и стоп в БУ;
  * цель 2 — противоположная граница C1 (полный диапазон).

Варианты и неоднозначности методики (C2 close vs C3 confirm, цвет C1,
двойной свип, глубина свипа) вынесены в CrtParams — источники расходятся,
и без параметров «CRT» не тестируется.

Источники модели — docs/CRT_METHOD.md.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from dataclasses import dataclass, replace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import strategy as st
import ta_core as ta

NAN = float("nan")


@dataclass
class CrtParams:
    """Параметры модели. Дефолты — «строгое ядро» + разумные фильтры."""
    variant: str = "classic"        # classic | failed
    min_range_atr: float = 0.8      # диапазон C1 не меньше N ATR старшего ТФ
    max_body_ratio: float = 0.85    # тело C1 не больше N доли диапазона
    max_sweep_frac: float = 0.40    # прокол C2 не глубже N доли диапазона C1
    min_rr: float = 1.0             # минимальный R:R до противоположной границы
    min_r1: float = 0.30            # цель 1 (середина) минимум в N R от входа
    stop_buf_atr: float = 0.25      # буфер стопа в ATR рабочего ТФ
    min_risk_atr: float = 0.15      # стоп ближе N ATR — спред съест риск, не берём
    require_dual_filter: bool = True  # свип обеих границ за одну свечу — не сетап
    c1_color: bool = False          # C1 против направления сделки (у части авторов)
    tp_r: float = 1.0               # цель варианта failed, в R
    max_bars: int = 120
    p1: float = 0.5                 # доля, закрываемая на середине диапазона
    session: str = ""               # окно сервера "0800-2300", пусто — выключено
    # --- структурные стоп/тейк по сильным уровням старшего ТФ ---
    sl_mode: str = "sweep"          # sweep — за свипом C2; level — за сильным уровнем
    tp_mode: str = "range"          # range — граница C1; zone — ближайшая сильная зона
    level_touches: int = 2          # сколько касаний делает уровень «сильным»
    level_flip_keep: int = 0        # сколько баров живёт перевёрнутый уровень
    stop_min_atr: float = 0.5       # уровень ближе N ATR — шум, не берём
    stop_max_atr: float = 3.0       # уровень дальше N ATR — стоп слишком широк
    tp_min_r: float = 1.0           # зона ближе N R — не цель
    tp_max_r: float = 4.0           # зона дальше N R — не дотягиваемся


def _tf_sec(frame: st.Frame) -> float:
    d = np.diff(frame.t)
    d = d[d > 0]
    return float(np.median(d)) if len(d) else 900.0


def pick_level_stop(levels: list[dict], side: str, entry: float, atr: float,
                    p: CrtParams) -> float | None:
    """Ближайший сильный уровень в защитную сторону, пригодный для стопа.

    Для лонга — поддержка ниже входа, для шорта — сопротивление выше. Уровни
    ближе `stop_min_atr` ATR считаются шумом, дальше `stop_max_atr` — слишком
    широким риском. Возвращает цену уровня (буфер добавляет вызывающий).
    """
    if not levels or atr <= 0:
        return None
    sgn = 1.0 if side == "LONG" else -1.0
    kind = "sup" if side == "LONG" else "res"
    lo, hi = p.stop_min_atr * atr, p.stop_max_atr * atr
    best, best_d = None, None
    for lv in levels:
        if lv.get("kind") != kind:
            continue
        price = float(lv.get("price", NAN))
        if np.isnan(price):
            continue
        d = sgn * (entry - price)
        if d <= 0 or d < lo or d > hi:
            continue
        if best is None or d < best_d:
            best, best_d = price, d
    return best


def pick_level_target(levels: list[dict], side: str, entry: float, risk: float,
                      p: CrtParams) -> float | None:
    """Ближайшая сильная зона в сторону сделки в коридоре [tp_min_r, tp_max_r]."""
    if not levels or risk <= 0:
        return None
    sgn = 1.0 if side == "LONG" else -1.0
    kind = "res" if side == "LONG" else "sup"
    lo, hi = p.tp_min_r * risk, p.tp_max_r * risk
    best, best_d = None, None
    for lv in levels:
        if lv.get("kind") != kind:
            continue
        price = float(lv.get("price", NAN))
        if np.isnan(price):
            continue
        d = sgn * (price - entry)
        if d <= 0 or d < lo or d > hi:
            continue
        if best is None or d < best_d:
            best, best_d = price, d
    return best


def scan(chart: st.Frame, H: st.Frame, p: CrtParams | None = None) -> dict:
    """Разметка сигналов CRT по барам рабочего ТФ.

    Возвращает массивы длиной len(chart.c). Сигнал ставится на первом баре
    рабочего ТФ, открывшемся после закрытия C2: именно его бот видит закрытым
    и по нему входит. Вход в бэктесте — по открытию следующего бара, как в
    ict2022/pdl_sweep (в живой торговле это цена закрытия сигнального бара).
    """
    p = p or CrtParams()
    n = len(chart.c)
    out = {
        "long": np.zeros(n, dtype=bool),
        "short": np.zeros(n, dtype=bool),
        "entry": np.full(n, NAN),
        "stop": np.full(n, NAN),
        "tp1": np.full(n, NAN),
        "tp2": np.full(n, NAN),
        "crh": np.full(n, NAN),
        "crl": np.full(n, NAN),
        "eq": np.full(n, NAN),
    }
    if H is None or len(H.c) < 30 or n < 3:
        return out

    h_tf = _tf_sec(H)
    l_tf = _tf_sec(chart)
    last_closed = float(chart.t[-1]) + l_tf      # конец последнего закрытого бара графика
    h_atr = ta.atr(H.h, H.l, H.c, 14)
    l_atr = ta.atr(chart.h, chart.l, chart.c, 14)

    # сильные зоны старшего ТФ — только если включён структурный стоп или тейк
    levels_by_bar = None
    if p.sl_mode == "level" or p.tp_mode == "zone":
        import range_v2
        levels_by_bar = range_v2.zones_for_chart(
            chart, H, touches=p.level_touches, flips_keep=p.level_flip_keep)

    for k in range(1, len(H.c)):
        close_t = float(H.t[k]) + h_tf
        if close_t > last_closed + 1e-6:
            break                                # C2 ещё не закрыта (живой бар)
        if np.isnan(h_atr[k]) or h_atr[k] <= 0:
            continue

        crh = float(H.h[k - 1])
        crl = float(H.l[k - 1])
        rng = crh - crl
        if rng <= 0:
            continue
        eq = (crh + crl) / 2.0

        # --- фильтры качества диапазона C1
        if rng < p.min_range_atr * float(h_atr[k]):
            continue
        body = abs(float(H.c[k - 1]) - float(H.o[k - 1])) / rng
        if body > p.max_body_ratio:
            continue

        swept_low = float(H.l[k]) < crl
        swept_high = float(H.h[k]) > crh

        c2c = float(H.c[k])
        c2l, c2h = float(H.l[k]), float(H.h[k])
        c1_bear = float(H.c[k - 1]) < float(H.o[k - 1])
        c1_bull = float(H.c[k - 1]) > float(H.o[k - 1])

        if p.variant == "failed":
            # C2 закрылась за границей C1 — торгуем продолжение пробоя.
            # Стоп — за пробитым уровнем: возврат внутрь = пробой не удался.
            if p.require_dual_filter and swept_low and swept_high:
                continue
            if c2c > crh:
                long_ = True
                stop_ref = crh
            elif c2c < crl:
                long_ = False
                stop_ref = crl
            else:
                continue
            if p.c1_color and ((long_ and not c1_bear) or (not long_ and not c1_bull)):
                continue
        else:
            # Строгое ядро: свип границы и закрытие обратно ВНУТРЬ C1.
            if not (crl <= c2c <= crh):
                continue
            if not (swept_low or swept_high):
                continue
            if swept_low and swept_high:
                # свип обеих границ — обычно мусор; без фильтра берём более
                # глубокую манипуляцию (она и была настоящей)
                if p.require_dual_filter:
                    continue
                long_ = (crl - c2l) >= (c2h - crh)
            else:
                long_ = swept_low
            if p.c1_color and ((long_ and not c1_bear) or (not long_ and not c1_bull)):
                continue
            # глубина свипа и R:R считаются по цене закрытия C2
            depth = ((crl - c2l) if long_ else (c2h - crh)) / rng
            if depth > p.max_sweep_frac + 1e-9:
                continue
            stop_ref = c2l if long_ else c2h
            opposite = crh if long_ else crl
            risk_ref = abs(c2c - stop_ref)
            if risk_ref <= 0:
                continue
            rr_ref = (opposite - c2c if long_ else c2c - opposite) / risk_ref
            if rr_ref < p.min_rr:
                continue

        i = int(np.searchsorted(chart.t, close_t, side="left"))
        if i >= n:
            continue
        if p.session and not st.in_session(float(chart.t[i]), p.session):
            continue
        if np.isnan(l_atr[i]):
            continue

        buf = p.stop_buf_atr * float(l_atr[i])
        sgn = 1.0 if long_ else -1.0
        side = "LONG" if long_ else "SHORT"
        entry_ref = c2c
        stop = stop_ref - sgn * buf

        # структурный стоп: ближайший сильный уровень в защитную сторону
        row = levels_by_bar[i]["levels"] if levels_by_bar is not None else []
        if p.sl_mode == "level" and row:
            lvl = pick_level_stop(row, side, entry_ref, float(l_atr[i]), p)
            if lvl is not None:
                stop = lvl - sgn * buf

        risk = abs(entry_ref - stop)
        if risk <= 0:
            continue
        # при фиксированном риске на сделку микроскопический стоп делает
        # позицию огромной, и спред превращается в десятки R
        if p.min_risk_atr > 0 and risk < p.min_risk_atr * float(l_atr[i]):
            continue

        if p.variant == "failed":
            tp1 = NAN
            tp2 = entry_ref + sgn * risk * p.tp_r
            if p.tp_mode == "zone" and row:
                zone = pick_level_target(row, side, entry_ref, risk, p)
                if zone is not None:
                    tp2 = zone
        else:
            opposite = crh if long_ else crl
            if p.tp_mode == "zone" and row:
                zone = pick_level_target(row, side, entry_ref, risk, p)
                tp2 = zone if zone is not None else opposite
            else:
                tp2 = opposite
            r1 = sgn * (eq - entry_ref) / risk
            r_final = sgn * (tp2 - entry_ref) / risk
            if r_final < p.min_rr:
                continue
            # середину берём только если она впереди входа и до финальной цели
            tp1 = eq if (r1 >= p.min_r1 and r_final > r1) else NAN

        out["long" if long_ else "short"][i] = True
        out["entry"][i] = entry_ref
        out["stop"][i] = stop
        out["tp1"][i] = tp1
        out["tp2"][i] = tp2
        out["crh"][i] = crh
        out["crl"][i] = crl
        out["eq"][i] = eq

    return out


def signal_at(S: dict, i: int) -> dict | None:
    """Сетап на баре i в виде, удобном для живого шага бота."""
    if i < 0 or i >= len(S["long"]):
        return None
    if not (bool(S["long"][i]) or bool(S["short"][i])):
        return None
    long_ = bool(S["long"][i])
    return {
        "side": "LONG" if long_ else "SHORT",
        "entry": float(S["entry"][i]),
        "stop": float(S["stop"][i]),
        "tp1": float(S["tp1"][i]) if not np.isnan(S["tp1"][i]) else None,
        "tp2": float(S["tp2"][i]),
        "crh": float(S["crh"][i]),
        "crl": float(S["crl"][i]),
        "eq": float(S["eq"][i]),
    }


def backtest(chart: st.Frame, H: st.Frame, p: CrtParams | None = None,
             entry_delay: int = 0) -> list[dict]:
    """Прогон по истории. Частичный выход: половина на EQ, остаток на границе.

    entry_delay > 0 — execution stress: вход на N баров позже обычного.
    """
    p = p or CrtParams()
    n = len(chart.c)
    delay = max(0, int(entry_delay))
    if n < 30:
        return []
    S = scan(chart, H, p)
    atr = ta.atr(chart.h, chart.l, chart.c, 14)

    trades: list[dict] = []
    busy_until = -1
    i = 1
    while i < n - 1:
        if i <= busy_until:
            i += 1
            continue
        sig = signal_at(S, i)
        if sig is None:
            i += 1
            continue
        fill_i = i + 1 + delay
        if fill_i >= n - 1:
            break

        side = sig["side"]
        sgn = 1.0 if side == "LONG" else -1.0
        entry = float(chart.o[fill_i])
        stop_raw = sig["stop"]
        risk = abs(entry - stop_raw)
        if risk <= 0:
            i += 1
            continue
        # гэп на входе может прижать цену к стопу: риск в пунктах становится
        # крошечным, и при фиксированном риске на сделку спред даёт десятки R
        if p.min_risk_atr > 0 and not np.isnan(atr[i]) and \
                risk < p.min_risk_atr * float(atr[i]):
            i += 1
            continue
        stop = entry - sgn * risk

        multi = sig["tp1"] is not None
        tp_a = sig["tp1"] if multi else sig["tp2"]
        tp_b = sig["tp2"]
        r_a = sgn * (float(tp_a) - entry) / risk
        r_b = sgn * (float(tp_b) - entry) / risk
        if r_a <= 0 or r_b <= 0:
            i += 1
            continue
        frac_a = p.p1 if multi else 1.0

        acc = 0.0
        hit_a = False
        exit_i = why = None

        for j in range(fill_i, min(fill_i + p.max_bars, n)):
            hi, lo = float(chart.h[j]), float(chart.l[j])
            if j == fill_i:
                # на баре входа возможен гэп через стоп или цель: разбираем
                # его отдельно и в этом баре больше ничего не трогаем
                op = float(chart.o[j])
                if (op <= stop) if sgn > 0 else (op >= stop):
                    # гэп через стоп: R по открытию, а не 0 (старый баг)
                    acc = sgn * (op - entry) / risk
                    exit_i, why = j, "SL"
                    break
                if (op >= tp_a) if sgn > 0 else (op <= tp_a):
                    if not multi:
                        exit_i, why = j, "TP"
                        break
                    acc, hit_a, stop = frac_a * r_a, True, entry
                    if (op >= tp_b) if sgn > 0 else (op <= tp_b):
                        acc += (1.0 - frac_a) * r_b
                        exit_i, why = j, "TP2"
                        break
                    continue
            o = float(chart.o[j])
            if (o <= stop) if sgn > 0 else (o >= stop):
                mtm = sgn * (o - entry) / risk
                acc = acc + (1.0 - frac_a if hit_a else 1.0) * mtm
                exit_i, why = j, "BE" if hit_a else "SL"
                break
            hit_sl = (lo <= stop) if sgn > 0 else (hi >= stop)
            if hit_sl:
                r = acc if hit_a else -1.0          # после TP1 стоп уже в БУ
                exit_i = j
                why = "BE" if hit_a else "SL"
                acc = r
                break
            if not hit_a and ((hi >= tp_a) if sgn > 0 else (lo <= tp_a)):
                hit_a = True
                acc = frac_a * r_a
                if not multi:
                    exit_i, why = j, "TP"
                    break
                stop = entry                          # стоп в безубыток
            if hit_a and ((hi >= tp_b) if sgn > 0 else (lo <= tp_b)):
                acc = acc + (1.0 - frac_a) * r_b
                exit_i, why = j, "TP2"
                break

        if exit_i is None:
            j = min(fill_i + p.max_bars, n - 1)
            mtm = sgn * (float(chart.c[j]) - entry) / risk
            acc = acc + (1.0 - frac_a if hit_a else 1.0) * mtm
            exit_i, why = j, "TIME"

        trades.append({"side": side, "entry_i": i, "exit_i": exit_i,
                       "entry": entry, "stop": stop, "risk": risk,
                       "target": tp_b, "tp1": tp_a, "tp2": tp_b, "multi": multi,
                       "r": acc, "exit": why})
        busy_until = exit_i
        i = exit_i + 1

    return trades


def equity_stats(trades: list[dict], bank: float = 5000.0, risk_pct: float = 0.5,
                 spread: float = 0.0) -> dict:
    """Кривая счёта при фиксированном риске на сделку.

    Риск каждой сделки — `risk_pct` % ТЕКУЩЕГО баланса: размер позиции
    подбирается так, чтобы стоп стоил ровно эту сумму. Поэтому R умножается
    на денежный риск текущего шага, а результат компаундится. Спред
    вычитается из R как `spread / риск_в_цене` (одна полная проходка).
    """
    eq = float(bank)
    peak = eq
    max_dd = 0.0
    worst = 0.0
    for t in trades:
        risk_px = float(t.get("risk") or abs(float(t["entry"]) - float(t["stop"])))
        cost_r = spread / risk_px if (spread > 0 and risk_px > 0) else 0.0
        net_r = float(t["r"]) - cost_r
        eq += eq * (risk_pct / 100.0) * net_r
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100.0)
        worst = min(worst, net_r * risk_pct)
    return {"bank": float(bank), "final": eq,
            "return_pct": (eq / bank - 1.0) * 100.0 if bank else 0.0,
            "max_dd_pct": max_dd, "worst_trade_pct": worst,
            "n": len(trades)}



def params_from_env(env) -> CrtParams:
    """Собрать CrtParams из окружения (bot.py передаёт os.environ)."""
    def _f(name: str, default: float) -> float:
        try:
            return float(env.get(name, "") or default)
        except (TypeError, ValueError):
            return default

    def _flag(name: str, default: bool) -> bool:
        raw = str(env.get(name, "1" if default else "0")).strip().lower()
        return raw in ("1", "true", "yes", "on")

    def _int(name: str, default: int) -> int:
        try:
            return int(float(env.get(name, "") or default))
        except (TypeError, ValueError):
            return default

    return replace(
        CrtParams(),
        variant=str(env.get("CRT_VARIANT", "classic")).strip().lower(),
        min_range_atr=_f("CRT_MIN_RANGE_ATR", 0.8),
        max_body_ratio=_f("CRT_MAX_BODY", 0.85),
        max_sweep_frac=_f("CRT_MAX_SWEEP", 0.40),
        min_rr=_f("CRT_MIN_RR", 1.0),
        min_r1=_f("CRT_MIN_R1", 0.30),
        stop_buf_atr=_f("CRT_STOP_BUF", 0.25),
        min_risk_atr=_f("CRT_MIN_RISK_ATR", 0.15),
        require_dual_filter=_flag("CRT_DUAL_FILTER", True),
        c1_color=_flag("CRT_C1_COLOR", False),
        tp_r=_f("CRT_TP_R", 1.0),
        max_bars=_int("CRT_MAX_BARS", 120),
        p1=_f("CRT_P1", 0.5),
        session=str(env.get("CRT_SESSION", "")).strip(),
        sl_mode=str(env.get("CRT_SL_MODE", "sweep")).strip().lower(),
        tp_mode=str(env.get("CRT_TP_MODE", "range")).strip().lower(),
        level_touches=_int("CRT_LEVEL_TOUCHES", 2),
        level_flip_keep=_int("CRT_LEVEL_FLIP_KEEP", 0),
        stop_min_atr=_f("CRT_STOP_MIN_ATR", 0.5),
        stop_max_atr=_f("CRT_STOP_MAX_ATR", 3.0),
        tp_min_r=_f("CRT_TP_MIN_R", 1.0),
        tp_max_r=_f("CRT_TP_MAX_R", 4.0),
    )


def main() -> None:
    import bot
    import feeds

    data = bot.MT5Data()
    spread = 0.0
    try:
        bars = int(os.getenv("BOT_BARS", "20000"))
        chart, htf = bot.load_frames(data, bot.SYMBOL, bot.TF, bars)
        range_tf = os.getenv("CRT_RANGE_TF", "240").strip()
        H = htf.get(range_tf)
        if H is None:
            span = float(chart.t[-1] - chart.t[0])
            mins = feeds.TF_MINUTES.get(range_tf.upper(), 240)
            H = data.frame(bot.SYMBOL, range_tf, int(span / (mins * 60)) + 320)
        try:
            si = data.mt5.symbol_info(bot.SYMBOL)
            if si is not None:
                spread = float(si.spread * si.point)
                if spread <= 0:
                    spread = max(0.0, float(si.ask) - float(si.bid))
        except Exception:
            spread = 0.0
    finally:
        data.close()

    p = params_from_env(os.environ)
    trades = backtest(chart, H, p)
    if not trades:
        print("сделок нет")
        return
    rs = [t["r"] for t in trades]
    wins = sum(1 for r in rs if r > 0)
    longs = sum(1 for t in trades if t["side"] == "LONG")
    span = (chart.t[-1] - chart.t[0]) / 86400.0
    by_exit: dict[str, int] = {}
    for t in trades:
        by_exit[t["exit"]] = by_exit.get(t["exit"], 0) + 1
    print(f"CRT ({p.variant}) · {bot.SYMBOL} {feeds.tf_label(bot.TF)} / "
          f"{feeds.tf_label(range_tf)} · {span:.0f} дн. · {len(trades)} сделок")
    print(f"лонг {longs}, шорт {len(trades) - longs} · прибыльных {wins} "
          f"({wins / len(rs) * 100:.0f}%) · сумма {sum(rs):+.2f}R · "
          f"средняя {sum(rs) / len(rs):+.2f}R")
    print("выходы:", ", ".join(f"{k} {v}" for k, v in sorted(by_exit.items(), key=lambda x: -x[1])))

    # счёт при фиксированном риске на сделку (по умолчанию 0.5% от баланса)
    bank = float(os.getenv("CRT_BANK", "5000") or 5000)
    risk_pct = float(os.getenv("CRT_RISK_PCT", "0.5") or 0.5)
    env_spread = os.getenv("CRT_SPREAD", "").strip()
    if env_spread:
        spread = float(env_spread)
    st_eq = equity_stats(trades, bank=bank, risk_pct=risk_pct, spread=spread)
    print(f"счёт: {bank:.0f}$ → {st_eq['final']:.0f}$ "
          f"({st_eq['return_pct']:+.1f}%) · риск {risk_pct}% "
          f"(~{bank * risk_pct / 100:.0f}$) · макс. просадка {st_eq['max_dd_pct']:.1f}% · "
          f"худшая сделка {st_eq['worst_trade_pct']:+.1f}% · спред {spread:.5f}")

    print("\nпоследние:")
    for t in trades[-8:]:
        when = dt.datetime.fromtimestamp(float(chart.t[t["entry_i"]]), dt.timezone.utc)
        print(f"  {when:%d.%m %H:%M}  {t['side']:5}  {t['entry']:.2f} → "
              f"{t['exit']:4}  {t['r']:+.2f}R")


if __name__ == "__main__":
    main()
