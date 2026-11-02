"""PRO Mitigation Block — механическая интерпретация v0.1.

Правила и список упрощений: docs/PMB_RULES.md.
Это исследовательский бэктест, а не сигнальный модуль: он отвечает на вопрос
«выживает ли идея при жёсткой формализации», а не «повторяет ли авторскую
разметку».
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Params:
    dr_len: int = 20            # HTF DR, закрытых D1-баров
    liq_d1: int = 60            # окно D1-FVG для ликвидности
    liq_h4: int = 40            # окно H4-FVG для ликвидности
    swing_look: int = 20        # свинг-минимум/максимум H4
    sweep_look: int = 30        # окно поиска свипа
    sweep_tol_atr: float = 0.05
    rb_wick: float = 0.40       # мин. доля тени у rejection block
    exp_look: int = 20          # окно расширения после свипа
    exp_atr: float = 1.0        # мин. ход расширения в ATR
    entry_ttl: int = 30         # сколько баров живёт лимит
    sl_buf_atr: float = 0.10    # буфер за MB
    tp_r: float = 2.0
    max_bars: int = 100         # макс. удержание после входа
    min_risk_atr: float = 0.0   # диагностический фильтр: мин. риск в ATR


@dataclass
class Frame:
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    atr: np.ndarray = field(default=None)  # type: ignore[assignment]


@dataclass
class Trade:
    side: int                  # +1 лонг, -1 шорт
    setup_i: int               # бар свипа, H4
    trigger_i: int             # бар, на котором сетап подтвердился
    entry_i: int | None
    exit_i: int | None
    entry: float
    sl: float
    tp: float
    result: str                # win / loss / cancelled / open
    ts_setup: int
    ts_trigger: int
    ts_entry: int | None
    ts_exit: int | None
    level: float = 0.0         # снятая ликвидность
    mb_i: int = -1
    fvg: tuple[float, float] = (0.0, 0.0)
    rb: tuple[float, float] = (0.0, 0.0)
    ta: float = 0.0            # противоположная граница DR: отмена до входа
    tp_r: float = 2.0


def atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    """ATR Уайлдера, как в strategy.ta."""
    prev = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    out = np.full(len(tr), np.nan)
    if len(tr) < n:
        return out
    acc = float(np.mean(tr[:n]))
    out[n - 1] = acc
    for i in range(n, len(tr)):
        acc = (acc * (n - 1) + tr[i]) / n
        out[i] = acc
    return out


def fvg_list(h: np.ndarray, l: np.ndarray) -> list[tuple[int, float, float, int]]:
    """(индекс подтверждения, low зоны, high зоны, +1 бычий / -1 медвежий)."""
    out: list[tuple[int, float, float, int]] = []
    for i in range(2, len(h)):
        if l[i] > h[i - 2]:
            out.append((i, float(h[i - 2]), float(l[i]), 1))
        elif h[i] < l[i - 2]:
            out.append((i, float(h[i]), float(l[i - 2]), -1))
    return out


def _d1_index(d1_t: np.ndarray, t: int) -> int:
    """Последний ЗАКРЫТЫЙ D1-бар на момент t (t — открытие H4-бара)."""
    return int(np.searchsorted(d1_t, t - 86400, side="right")) - 1


class Engine:
    def __init__(self, h4: Frame, d1: Frame, p: Params):
        self.h4 = h4
        self.d1 = d1
        self.p = p
        if h4.atr is None:
            h4.atr = atr(h4.h, h4.l, h4.c)
        self.h4_fvg = fvg_list(h4.h, h4.l)
        self.d1_fvg = fvg_list(d1.h, d1.l)
        self.d1_idx = np.array([_d1_index(d1.t, int(x)) for x in h4.t])
        self.trades: list[Trade] = []
        self.cancelled = 0
        self._order: Trade | None = None
        self._active: Trade | None = None
        self._last_long = -1
        self._last_short = -1
        self.stage: Counter = Counter()

    # ---------------------------------------------------------------- setup
    def _dr(self, i: int):
        d = int(self.d1_idx[i])
        p = self.p
        if d < p.dr_len - 1:
            return None
        lo = float(np.min(self.d1.l[d - p.dr_len + 1:d + 1]))
        hi = float(np.max(self.d1.h[d - p.dr_len + 1:d + 1]))
        return lo, hi, (lo + hi) / 2.0, d

    def _liquidity(self, i: int, side: int, lo: float, hi: float, mid: float):
        """Ближайший снизу (лонг) / сверху (шорт) уровень ликвидности."""
        p = self.p
        c = float(self.h4.c[i])
        d = int(self.d1_idx[i])
        cands: list[float] = []
        for f, bot, top, sd in self.d1_fvg:
            if sd != side or f > d - 1 or f < d - p.liq_d1:
                continue
            lvl = bot if side == 1 else top
            if side == 1 and lo <= lvl < min(c, mid):
                cands.append(lvl)
            if side == -1 and max(c, mid) < lvl <= hi:
                cands.append(lvl)
        for f, bot, top, sd in self.h4_fvg:
            if sd != side or f > i - 1 or f < i - p.liq_h4:
                continue
            lvl = bot if side == 1 else top
            if side == 1 and lo <= lvl < min(c, mid):
                cands.append(lvl)
            if side == -1 and max(c, mid) < lvl <= hi:
                cands.append(lvl)
        sw = float(np.min(self.h4.l[max(0, i - p.swing_look):i])) if side == 1 else float(np.max(self.h4.h[max(0, i - p.swing_look):i]))
        if side == 1:
            cands.append(lo)
            if sw < min(c, mid):
                cands.append(sw)
            return max(cands) if cands else None
        cands.append(hi)
        if sw > max(c, mid):
            cands.append(sw)
        return min(cands) if cands else None

    def _sweep(self, i: int, side: int, level: float, a: float):
        p = self.p
        h, l, c = self.h4.h, self.h4.l, self.h4.c
        best = None
        for j in range(max(0, i - p.sweep_look), i):
            if side == 1:
                if l[j] <= level + p.sweep_tol_atr * a and c[j] > level:
                    if best is None or l[j] < l[best]:
                        best = j
            else:
                if h[j] >= level - p.sweep_tol_atr * a and c[j] < level:
                    if best is None or h[j] > h[best]:
                        best = j
        return best

    def _rb(self, j: int, side: int):
        p = self.p
        o, h, l, c = self.h4.o, self.h4.h, self.h4.l, self.h4.c
        rng = h[j] - l[j]
        if rng <= 0:
            return None
        if side == 1:
            wick = min(o[j], c[j]) - l[j]
            if wick < p.rb_wick * rng or (c[j] - l[j]) < 0.5 * rng:
                return None
            return float(l[j]), float(max(o[j], c[j]))
        wick = h[j] - max(o[j], c[j])
        if wick < p.rb_wick * rng or (h[j] - c[j]) < 0.5 * rng:
            return None
        return float(min(o[j], c[j])), float(h[j])

    def _setup(self, i: int, side: int):
        p = self.p
        dr = self._dr(i)
        if dr is None:
            return None
        self.stage['dr'] += 1
        lo, hi, mid, _ = dr
        c = float(self.h4.c[i])
        level = self._liquidity(i, side, lo, hi, mid)
        if level is None:
            return None
        self.stage['liq'] += 1
        # ликвидность должна быть в рабочей области DR
        if (side == 1 and level > mid) or (side == -1 and level < mid):
            self.stage['not_wa'] += 1
            return None
        a = float(self.h4.atr[i])
        if not np.isfinite(a) or a <= 0:
            return None
        j = self._sweep(i, side, level, a)
        if j is None:
            return None
        self.stage['sweep'] += 1
        rb = self._rb(j, side)
        if rb is None:
            return None
        self.stage['rb'] += 1
        rb_low, rb_high = rb

        o, h, l, cc = self.h4.o, self.h4.h, self.h4.l, self.h4.c
        k = None
        zone = None
        for q in range(j + 1, min(i, j + p.exp_look) + 1):
            if q < 2:
                continue
            if side == 1:
                ok_fvg = l[q] > h[q - 2]
                displ = h[q] - rb_low >= p.exp_atr * a
                broke = cc[q] > rb_high
                zone_q = (float(h[q - 2]), float(l[q]))
            else:
                ok_fvg = h[q] < l[q - 2]
                displ = rb_high - l[q] >= p.exp_atr * a
                broke = cc[q] < rb_low
                zone_q = (float(h[q]), float(l[q - 2]))
            if ok_fvg and displ and broke:
                k, zone = q, zone_q
                break
        if k is None:
            return None
        self.stage['exp'] += 1
        zbot, ztop = zone

        # тест FVG: цена заходит в гэп, но закрывается по правильную сторону
        m = None
        for q in range(k + 1, i + 1):
            if side == 1:
                if l[q] <= ztop and cc[q] >= zbot:
                    m = q
                    break
            else:
                if h[q] >= zbot and cc[q] <= ztop:
                    m = q
                    break
        if m is None:
            return None
        self.stage['fvg_test'] += 1

        # после теста FVG цена возвращается к RB
        m2 = None
        for q in range(m, i + 1):
            if side == 1:
                if l[q] <= rb_high and cc[q] >= rb_low:
                    m2 = q
                    break
            else:
                if h[q] >= rb_low and cc[q] <= rb_high:
                    m2 = q
                    break
        if m2 is None:
            return None
        self.stage['rb_test'] += 1

        # MB — последняя противоположная свеча перед расширением
        b = None
        for q in range(k - 1, j - 2, -1):
            if (side == 1 and cc[q] < o[q]) or (side == -1 and cc[q] > o[q]):
                b = q
                break
        if b is None:
            return None
        self.stage['mb'] += 1

        if side == 1:
            entry = float(max(o[b], cc[b]))
            sl = float(l[b]) - p.sl_buf_atr * a
        else:
            entry = float(min(o[b], cc[b]))
            sl = float(h[b]) + p.sl_buf_atr * a
        if side == 1 and not (sl < entry < c and entry > rb_low):
            self.stage['bad_entry'] += 1
            return None
        if side == -1 and not (sl > entry > c and entry < rb_high):
            self.stage['bad_entry'] += 1
            return None
        risk = abs(entry - sl)
        if risk <= 0:
            return None
        if risk < p.min_risk_atr * a:
            self.stage['tiny_risk'] += 1
            return None
        tp = entry + side * p.tp_r * risk
        return Trade(
            side=side, setup_i=j, trigger_i=i, entry_i=None, exit_i=None,
            entry=entry, sl=sl, tp=tp, result="open",
            ts_setup=int(self.h4.t[j]), ts_trigger=int(self.h4.t[i]),
            ts_entry=None, ts_exit=None, level=float(level), mb_i=b,
            fvg=zone, rb=(rb_low, rb_high), tp_r=p.tp_r,
            ta=hi if side == 1 else lo,
        )

    # -------------------------------------------------------------- trading
    def _close(self, i: int, price: float, result: str) -> None:
        tr = self._active
        assert tr is not None
        tr.exit_i = i
        tr.ts_exit = int(self.h4.t[i])
        tr.result = result
        self.trades.append(tr)
        self._active = None

    def _manage(self, i: int) -> None:
        p = self.p
        o, h, l = self.h4.o, self.h4.h, self.h4.l
        tr = self._active
        if tr is not None:
            if tr.side == 1:
                if l[i] <= tr.sl:
                    self._close(i, tr.sl, "loss")
                elif h[i] >= tr.tp:
                    self._close(i, tr.tp, "win")
            else:
                if h[i] >= tr.sl:
                    self._close(i, tr.sl, "loss")
                elif l[i] <= tr.tp:
                    self._close(i, tr.tp, "win")
            if self._active is not None and i - (tr.entry_i or i) >= p.max_bars:
                self._close(i, float(self.h4.c[i]), "open")
            return

        od = self._order
        if od is None:
            return
        # отмена: цена дошла до противоположной границы DR (TA) раньше входа
        ta = od.ta
        if (od.side == 1 and h[i] >= ta) or (od.side == -1 and l[i] <= ta):
            self.cancelled += 1
            self._order = None
            return
        filled = (l[i] <= od.entry) if od.side == 1 else (h[i] >= od.entry)
        if filled:
            fill = min(float(o[i]), od.entry) if od.side == 1 else max(float(o[i]), od.entry)
            od.entry = fill
            od.entry_i = i
            od.ts_entry = int(self.h4.t[i])
            self._active = od
            self._order = None
            if od.side == 1:
                if l[i] <= od.sl:
                    self._close(i, od.sl, "loss")
                elif h[i] >= od.tp:
                    self._close(i, od.tp, "win")
            else:
                if h[i] >= od.sl:
                    self._close(i, od.sl, "loss")
                elif l[i] <= od.tp:
                    self._close(i, od.tp, "win")
            return
        if i - od.trigger_i >= p.entry_ttl:
            self._order = None

    def run(self, start: int | None = None) -> list[Trade]:
        p = self.p
        n = len(self.h4.t)
        begin = start if start is not None else max(p.dr_len * 6, 200)
        for i in range(begin, n):
            self._manage(i)
            if self._active is not None or self._order is not None:
                self.stage['busy'] += 1
                continue
            for side in (1, -1):
                last = self._last_long if side == 1 else self._last_short
                tr = self._setup(i, side)
                if tr is None:
                    continue
                if tr.setup_i <= last:
                    self.stage['dup'] += 1
                    continue
                if side == 1:
                    self._last_long = tr.setup_i
                else:
                    self._last_short = tr.setup_i
                self._order = tr
                break
        if self._active is not None:
            self.trades.append(self._active)
            self._active = None
        return self.trades


def result(trades: list[Trade], cancelled: int = 0) -> dict:
    closed = [t for t in trades if t.result in ("win", "loss")]
    wins = sum(1 for t in closed if t.result == "win")
    losses = sum(1 for t in closed if t.result == "loss")
    tp_r = closed[0].tp_r if closed else 2.0
    net = wins * tp_r - losses
    return {
        "setups": len(trades),
        "entries": len(closed),
        "wins": wins,
        "losses": losses,
        "wr": (wins / len(closed) * 100.0) if closed else 0.0,
        "net_r": net,
        "cancelled": cancelled,
        "open": sum(1 for t in trades if t.result == "open"),
    }
