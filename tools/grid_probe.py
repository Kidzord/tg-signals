#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Воспроизведение схемы из рекламного ролика: OCO-сетка без стоп-лосса.

Что видно в кадрах ролика (secretea8290, XAUUSD.crp, M15):
  * на закрытии каждого бара выставляется пара stop-ордеров в обе стороны;
  * у позиций нет stop loss, есть только take profit на 1-2.5 доллара;
  * лот растёт с числом открытых позиций в ту же сторону (0.01 -> 0.02 -> 0.03),
    то есть убыточная сторона доливается, а не закрывается;
  * показ идёт в стратегическом тестере, где нет слиппеджа и комиссии.

Симулятор повторяет механику, но исполняет ордера по M5-барам (внутри бара
путь приближается эвристикой open -> ближний экстремум -> дальний -> close),
поэтому видно не только прибыль, но и плавающую просадку сетки.

Сценарии:
  ideal  — как тестер: нулевой спред, нулевой слиппедж, плечо без ограничений;
  real   — спред терминала + слиппедж stop-ордеров + stop-out 50%;
  stress — спред x2, слиппедж x3.

Инструмент ничего не пишет на диск: JSON в stdout, сводка в stderr.

    python tools/grid_probe.py --symbol XAUUSD --terminal "C:/Program Files/MetaTrader 5/terminal64.exe"
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass, field

CONTRACT_OZ = 100.0  # 1.00 лот XAUUSD = 100 унций


@dataclass
class Bar:
    t: int
    o: float
    h: float
    l: float
    c: float


@dataclass
class Params:
    dist: float = 0.8          # расстояние stop-ордера от цены закрытия M15
    tp: float = 1.2            # тейк-профит в цене инструмента
    base_lot: float = 0.01
    max_levels: int = 3        # в кадрах видно 0.01 -> 0.02 -> 0.03, дальше не растёт
    max_lot: float = 0.10
    spread: float = 0.0
    slippage: float = 0.0      # неблагоприятный слиппедж входа
    exit_slippage: float = 0.0  # слиппедж принудительного закрытия
    stop_loss: float = 0.0     # стоп на позицию в цене; 0 = выключен
    basket_stop: float = 0.0   # закрыть всё при плавающем минусе, USD; 0 = выкл
    equity_stop_pct: float = 0.0  # закрыть всё при просадке от пика, %; 0 = выкл
    commission_per_lot: float = 0.0
    leverage: float = 1e9
    stopout: float = 0.5
    start_balance: float = 1060.0


@dataclass
class Position:
    side: int                 # +1 buy, -1 sell
    entry: float              # цена исполнения (ask для buy, bid для sell)
    lots: float
    tp_trigger: float         # уровень bid, на котором срабатывает TP
    ts: int


@dataclass
class Pending:
    side: int
    level: float
    ts: int


@dataclass
class Trade:
    side: int
    lots: float
    entry: float
    exit: float
    open_ts: int
    close_ts: int
    pnl: float
    reason: str


@dataclass
class Result:
    trades: list[Trade] = field(default_factory=list)
    balance: float = 0.0
    equity: float = 0.0
    start_balance: float = 0.0
    max_dd_pct: float = 0.0
    max_floating_loss: float = 0.0
    max_lots: float = 0.0
    max_open: int = 0
    open_at_end: int = 0
    blowup_ts: int | None = None
    blowup_reason: str = ""
    worst_ts: int | None = None
    worst_equity: float = 0.0
    worst_positions: list[tuple[int, float, float, float]] = field(default_factory=list)
    sl_hits: int = 0
    basket_stops: int = 0
    equity_stops: int = 0
    realized_stops: float = 0.0
    equity_curve: list[tuple[int, float]] = field(default_factory=list)
    fills_per_day: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict:
        wins = [t for t in self.trades if t.pnl > 0]
        losses = [t for t in self.trades if t.pnl <= 0]
        gross_win = sum(t.pnl for t in wins)
        gross_loss = -sum(t.pnl for t in losses)
        return {
            "trades": len(self.trades),
            "win_rate": round(len(wins) / len(self.trades), 4) if self.trades else 0.0,
            "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
            "net_pnl": round(sum(t.pnl for t in self.trades), 2),
            "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
            "avg_loss": round(gross_loss / len(losses), 2) if losses else 0.0,
            "largest_loss": round(min((t.pnl for t in self.trades), default=0.0), 2),
            "balance": round(self.balance, 2),
            "return_pct": round((self.equity / self.start_balance - 1.0) * 100.0, 2)
            if self.start_balance else 0.0,
            "max_dd_pct": round(self.max_dd_pct * 100.0, 2),
            "max_floating_loss": round(self.max_floating_loss, 2),
            "max_lots": round(self.max_lots, 3),
            "max_open": self.max_open,
            "open_at_end": self.open_at_end,
            "blowup_ts": self.blowup_ts,
            "blowup_reason": self.blowup_reason,
            "worst_ts": self.worst_ts,
            "worst_equity": round(self.worst_equity, 2),
            "worst_positions": self.worst_positions,
            "sl_hits": self.sl_hits,
            "basket_stops": self.basket_stops,
            "equity_stops": self.equity_stops,
            "realized_stops": round(self.realized_stops, 2),
            "avg_fills_per_day": round(
                sum(self.fills_per_day.values()) / len(self.fills_per_day), 1)
            if self.fills_per_day else 0.0,
        }


def _segments(b: Bar) -> list[tuple[float, float]]:
    """Путь внутри бара: open -> ближний экстремум -> дальний -> close."""
    if abs(b.h - b.o) >= abs(b.o - b.l):
        return [(b.o, b.h), (b.h, b.l), (b.l, b.c)]
    return [(b.o, b.l), (b.l, b.h), (b.h, b.c)]


def _lots_for(positions: list[Position], side: int, p: Params) -> float:
    same = sum(1 for x in positions if x.side == side)
    level = min(same + 1, max(1, p.max_levels))
    lots = round(p.base_lot * level, 2)
    return min(p.max_lot, lots)


def _unrealized(positions: list[Position], bid: float, spread: float) -> float:
    total = 0.0
    for pos in positions:
        if pos.side > 0:
            total += (bid - pos.entry) * CONTRACT_OZ * pos.lots
        else:
            total += (pos.entry - (bid + spread)) * CONTRACT_OZ * pos.lots
    return total


def _close(pos: Position, px: float, ts: int, p: Params, reason: str,
           trades: list[Trade]) -> float:
    if pos.side > 0:
        pnl = (px - pos.entry) * CONTRACT_OZ * pos.lots
    else:
        pnl = (pos.entry - px) * CONTRACT_OZ * pos.lots
    pnl -= p.commission_per_lot * pos.lots
    trades.append(Trade(pos.side, pos.lots, pos.entry, px, pos.ts, ts, pnl, reason))
    return pnl


def simulate(bars: list[Bar], p: Params) -> Result:
    """Прогон OCO-сетки по M5-барам."""
    if not bars:
        return Result(balance=p.start_balance, equity=p.start_balance)
    bars = sorted(bars, key=lambda b: b.t)

    balance = p.start_balance
    positions: list[Position] = []
    pendings: list[Pending] = []
    res = Result(balance=balance, equity=balance, start_balance=p.start_balance)

    peak = balance
    last_close: float | None = None
    cur_bucket: int | None = None

    def book_close(pos: Position, px: float, ts: int, reason: str) -> None:
        nonlocal balance
        balance += _close(pos, px, ts, p, reason, res.trades)

    def check_tp(price: float, ts: int) -> None:
        """Закрыть всё, чей TP пройден на этом участке пути."""
        keep: list[Position] = []
        for pos in positions:
            if pos.side > 0 and price >= pos.tp_trigger:
                book_close(pos, pos.tp_trigger, ts, "tp")
            elif pos.side < 0 and price <= pos.tp_trigger:
                # tp_trigger — уровень bid; в PnL продажи входит ask
                book_close(pos, pos.tp_trigger + p.spread, ts, "tp")
            else:
                keep.append(pos)
        positions[:] = keep

    def check_sl(price: float, start: float, ts: int) -> None:
        """Стоп на позицию; при гэпе исполняется по открытию, а не по уровню."""
        if p.stop_loss <= 0:
            return
        keep: list[Position] = []
        for pos in positions:
            if pos.side > 0:
                trig = pos.entry - p.stop_loss
                if price <= trig:
                    book_close(pos, min(trig, start), ts, "sl")
                    res.sl_hits += 1
                    continue
            else:
                trig = pos.entry + p.stop_loss - p.spread
                if price >= trig:
                    book_close(pos, max(trig, start) + p.spread, ts, "sl")
                    res.sl_hits += 1
                    continue
            keep.append(pos)
        positions[:] = keep

    def try_pending(price: float, start: float, ts: int) -> None:
        """Исполнить stop-ордера, пробитые на участке start -> price.

        `start` нужен для гэпа: если сегмент открылся уже за уровнем, цена
        исполнения — открытие, а не дальний конец сегмента.
        """
        keep: list[Pending] = []
        for pend in pendings:
            filled: Position | None = None
            if pend.side > 0 and price + p.spread >= pend.level:
                fill = max(pend.level, start + p.spread) + p.slippage
                filled = Position(1, fill, _lots_for(positions, 1, p),
                                  fill + p.tp, ts)
            elif pend.side < 0 and price <= pend.level:
                fill = min(pend.level, start) - p.slippage
                filled = Position(-1, fill, _lots_for(positions, -1, p),
                                  fill - p.tp - p.spread, ts)
            if filled is None:
                keep.append(pend)
                continue
            same = sum(1 for x in positions if x.side == filled.side)
            if same >= p.max_levels:
                continue  # сетка заполнена: четвёртый уровень не открываем
            positions.append(filled)
            res.max_lots = max(res.max_lots, filled.lots)
            res.max_open = max(res.max_open, len(positions))
            day = dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%d")
            res.fills_per_day[day] = res.fills_per_day.get(day, 0) + 1
        pendings[:] = keep

    def process(price: float, start: float, ts: int) -> None:
        # на участке могут сработать TP, SL и новый ордер: до стабильности
        for _ in range(8):
            before = (len(positions), len(pendings), res.sl_hits)
            check_tp(price, ts)
            check_sl(price, start, ts)
            try_pending(price, start, ts)
            check_tp(price, ts)
            check_sl(price, start, ts)
            if (len(positions), len(pendings), res.sl_hits) == before:
                break

    def force_close(bid: float, ts: int, reason: str) -> None:
        nonlocal positions
        for pos in positions:
            if pos.side > 0:
                px = bid - p.exit_slippage
            else:
                px = bid + p.spread + p.exit_slippage
            book_close(pos, px, ts, reason)
        positions = []

    def maybe_basket(bid: float, ts: int) -> None:
        """Добровольный стоп по корзине или по просадке equity: закрыть всё."""
        if not positions:
            return
        floating = _unrealized(positions, bid, p.spread)
        reason = ""
        if p.basket_stop > 0 and floating <= -p.basket_stop:
            reason = "basket"
        if p.equity_stop_pct > 0 and peak > 0:
            equity = balance + floating
            if equity <= peak * (1.0 - p.equity_stop_pct / 100.0):
                reason = reason or "equity"
        if not reason:
            return
        before = sum(t.pnl for t in res.trades)
        force_close(bid, ts, reason)
        pendings.clear()
        res.realized_stops += sum(t.pnl for t in res.trades) - before
        if reason == "basket":
            res.basket_stops += 1
        else:
            res.equity_stops += 1

    def stopout_bid() -> float | None:
        """Цена bid, при которой equity = stopout * margin (линейно по цене)."""
        if not positions:
            return None
        margin = sum(x.entry * CONTRACT_OZ * x.lots for x in positions) / p.leverage
        if margin <= 0:
            return None
        threshold = p.stopout * margin
        const = 0.0
        slope = 0.0
        for pos in positions:
            if pos.side > 0:
                const -= pos.entry * CONTRACT_OZ * pos.lots
                slope += CONTRACT_OZ * pos.lots
            else:
                const += (pos.entry - p.spread) * CONTRACT_OZ * pos.lots
                slope -= CONTRACT_OZ * pos.lots
        if abs(slope) < 1e-12:
            return None
        return (threshold - balance - const) / slope

    def maybe_stopout(a: float, b: float, ts: int) -> bool:
        """Если путь прошёл цену stop-out, закрыть всё именно на ней."""
        star = stopout_bid()
        if star is None or not (min(a, b) <= star <= max(a, b)):
            return False
        force_close(star, ts, "stopout")
        res.blowup_ts = ts
        res.blowup_reason = "stopout"
        return True

    for bar in bars:
        bucket = bar.t // 900
        if bucket != cur_bucket:
            if cur_bucket is not None and last_close is not None:
                pendings = [
                    Pending(1, last_close + p.dist, bar.t),
                    Pending(-1, last_close - p.dist, bar.t),
                ]
            cur_bucket = bucket

        # гэп на открытии бара: TP и stop-ордера могли быть пробиты сразу
        process(bar.o, bar.o, bar.t)
        maybe_basket(bar.o, bar.t)
        if maybe_stopout(bar.o, bar.o, bar.t):
            res.equity_curve.append((bar.t, balance))
            break
        blown = False
        for a, b in _segments(bar):
            if a == b:
                continue
            process(b, a, bar.t)
            maybe_basket(b, bar.t)
            if maybe_stopout(a, b, bar.t):
                blown = True
                break
        if blown:
            res.equity_curve.append((bar.t, balance))
            break

        last_close = bar.c
        floating = _unrealized(positions, bar.c, p.spread)
        equity = balance + floating
        if res.worst_ts is None or equity < res.worst_equity:
            res.worst_ts = bar.t
            res.worst_equity = equity
            res.worst_positions = [(x.side, x.entry, x.lots, x.tp_trigger)
                                   for x in positions]
        peak = max(peak, equity)
        if peak > 0:
            res.max_dd_pct = max(res.max_dd_pct, (peak - equity) / peak)
        res.max_floating_loss = min(res.max_floating_loss, equity - balance)
        res.equity_curve.append((bar.t, equity))

        if balance <= 0 and res.blowup_ts is None:
            res.blowup_ts = bar.t
            res.blowup_reason = "deposit"
            res.equity_curve.append((bar.t, balance))
            positions.clear()
            break

    res.balance = round(balance, 2)
    res.equity = round(balance + _unrealized(positions, bars[-1].c, p.spread), 2)
    res.open_at_end = len(positions)
    return res


# ------------------------------------------------------------------ данные

def _chunks(start: dt.datetime, end: dt.datetime, days: int = 45):
    cur = start
    while cur < end:
        nxt = min(end, cur + dt.timedelta(days=days))
        yield cur, nxt
        cur = nxt


def load_m5(terminal: str, symbol: str, start: dt.datetime,
            end: dt.datetime) -> tuple[list[Bar], float]:
    """M5-бары кусками: годовой запрос терминал обрезает, месячные отдаёт."""
    import MetaTrader5 as mt5

    if not mt5.initialize(path=terminal):
        raise RuntimeError(f"MT5 не инициализировался: {mt5.last_error()}")
    try:
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"символ {symbol} не выбран")
        info = mt5.symbol_info(symbol)
        point = float(getattr(info, "point", 0.01) or 0.01)
        spread = float(getattr(info, "spread", 0) or 0) * point
        rows: dict[int, Bar] = {}
        for a, b in _chunks(start, end):
            rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, a, b)
            for r in rates if rates is not None else []:
                rows[int(r["time"])] = Bar(int(r["time"]), float(r["open"]),
                                           float(r["high"]), float(r["low"]),
                                           float(r["close"]))
        return [rows[t] for t in sorted(rows)], spread
    finally:
        mt5.shutdown()


def _in_window(bars: list[Bar], start: dt.datetime, end: dt.datetime) -> list[Bar]:
    lo = int(start.replace(tzinfo=dt.UTC).timestamp())
    hi = int(end.replace(tzinfo=dt.UTC).timestamp())
    return [b for b in bars if lo <= b.t < hi]


def _daily_stats(res: Result) -> dict:
    out: dict[str, float] = {}
    for ts, eq in res.equity_curve:
        key = dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%d")
        out[key] = round(eq, 2)
    return out


def _month_stats(res: Result, bars: list[Bar], start_balance: float) -> dict:
    curve = dict(res.equity_curve)
    months: dict[str, dict] = {}
    for b in bars:
        key = dt.datetime.fromtimestamp(b.t, dt.UTC).strftime("%Y-%m")
        eq = curve.get(b.t)
        if eq is None:
            continue
        m = months.setdefault(key, {"first": eq, "last": eq, "min": eq, "max": eq})
        m["last"] = eq
        m["min"] = min(m["min"], eq)
        m["max"] = max(m["max"], eq)
    out = {}
    for key, m in months.items():
        out[key] = {
            "equity_end": round(m["last"], 2),
            "month_pct": round((m["last"] / m["first"] - 1.0) * 100.0, 2)
            if m["first"] else 0.0,
            "intra_month_dd_pct": round((m["max"] - m["min"]) / m["max"] * 100.0, 2)
            if m["max"] else 0.0,
        }
    return out


SCENARIOS = {
    "ideal": dict(spread=0.0, slippage=0.0, exit_slippage=0.0, leverage=1e9),
    "real": dict(spread=None, slippage=0.06, exit_slippage=0.10, leverage=500.0),
    "stress": dict(spread=None, slippage=0.18, exit_slippage=0.30, leverage=500.0),
}


def run_scenarios(bars: list[Bar], base: dict, spread: float,
                  scenarios: list[str], start_balance: float) -> dict:
    out = {}
    for name in scenarios:
        sc = dict(SCENARIOS[name])
        sc_spread = spread if sc["spread"] is None else sc["spread"]
        if name == "stress":
            sc_spread = spread * 2.0
        p = Params(start_balance=start_balance, **{**base, **sc, "spread": sc_spread})
        res = simulate(bars, p)
        s = res.summary()
        s["spread_used"] = round(sc_spread, 4)
        s["slippage"] = p.slippage
        s["leverage"] = p.leverage
        s["months"] = _month_stats(res, bars, start_balance)
        s["days"] = _daily_stats(res)
        out[name] = s
    return out


def sweep(bars: list[Bar], base: dict, spread: float, start: dt.datetime,
          fit_end: dt.datetime, test_end: dt.datetime, dists: list[float],
          tps: list[float], start_balance: float) -> dict:
    """Перебор dist/tp: лучший на первой неделе ролика -> проверка дальше."""
    fit = _in_window(bars, start, fit_end)
    test = _in_window(bars, fit_end, test_end)
    rows = []
    for dist in dists:
        for tp in tps:
            cfg = {**base, "dist": dist, "tp": tp}
            ideal = Params(start_balance=start_balance,
                           **{**cfg, **SCENARIOS["ideal"], "spread": 0.0})
            real = Params(start_balance=start_balance,
                          **{**cfg, **SCENARIOS["real"], "spread": spread})
            fr = simulate(fit, ideal).summary()
            tr = simulate(test, real).summary()
            rows.append({
                "dist": dist, "tp": tp,
                "fit_return_pct": fr["return_pct"],
                "fit_dd_pct": fr["max_dd_pct"],
                "fit_trades": fr["trades"],
                "test_return_pct": tr["return_pct"],
                "test_dd_pct": tr["max_dd_pct"],
                "test_blowup": tr["blowup_reason"] or None,
                "test_balance": tr["balance"],
            })
    best = max(rows, key=lambda r: r["fit_return_pct"]) if rows else None
    return {"fit_bars": len(fit), "test_bars": len(test), "rows": rows, "best": best}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--terminal", default="C:/Program Files/MetaTrader 5/terminal64.exe")
    ap.add_argument("--start", default="2025-12-31")
    ap.add_argument("--end", default="2026-03-08")
    ap.add_argument("--oos-start", default="2026-03-08")
    ap.add_argument("--oos-end", default="2026-11-05")
    ap.add_argument("--dist", type=float, default=0.8)
    ap.add_argument("--tp", type=float, default=1.2)
    ap.add_argument("--sl", type=float, default=0.0,
                    help="стоп на позицию в цене (0 = выключен)")
    ap.add_argument("--basket-stop", type=float, default=0.0,
                    help="закрыть всё при плавающем минусе, USD (0 = выкл)")
    ap.add_argument("--equity-stop", type=float, default=0.0,
                    help="закрыть всё при просадке от пика, %% (0 = выкл)")
    ap.add_argument("--base-lot", type=float, default=0.01)
    ap.add_argument("--max-levels", type=int, default=3)
    ap.add_argument("--max-lot", type=float, default=0.10)
    ap.add_argument("--balance", type=float, default=1060.0)
    ap.add_argument("--spread", type=float, default=None,
                    help="спред в цене; по умолчанию из терминала")
    ap.add_argument("--commission", type=float, default=0.0,
                    help="USD за 1.0 лот round turn (0.01 лота = /100)")
    ap.add_argument("--scenarios", default="ideal,real,stress")
    ap.add_argument("--sweep", action="store_true",
                    help="перебрать dist/tp на первой неделе и проверить дальше")
    ap.add_argument("--fit-days", type=int, default=7)
    ap.add_argument("--sweep-dist", default="0.4,0.8,1.2,1.6")
    ap.add_argument("--sweep-tp", default="0.8,1.2,2.0,3.0")
    args = ap.parse_args()
    if not args.symbol.upper().startswith("XAU"):
        raise SystemExit("grid_probe: CONTRACT_OZ=100 рассчитан только на XAU; "
                         "для FX/индексов PnL и stop-out будут неверны")

    start = dt.datetime.strptime(args.start, "%Y-%m-%d")
    end = dt.datetime.strptime(args.end, "%Y-%m-%d")
    oos_start = dt.datetime.strptime(args.oos_start, "%Y-%m-%d")
    oos_end = dt.datetime.strptime(args.oos_end, "%Y-%m-%d")

    bars, spread = load_m5(args.terminal, args.symbol, start, oos_end)
    spread_used = spread if args.spread is None else max(0.0, args.spread)
    base = {
        "dist": args.dist, "tp": args.tp,
        "stop_loss": args.sl, "basket_stop": args.basket_stop,
        "equity_stop_pct": args.equity_stop,
        "base_lot": args.base_lot, "max_levels": args.max_levels,
        "max_lot": args.max_lot,
        "commission_per_lot": args.commission,
    }
    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]

    if args.sweep:
        dists = [float(x) for x in args.sweep_dist.split(",") if x.strip()]
        tps = [float(x) for x in args.sweep_tp.split(",") if x.strip()]
        fit_end = start + dt.timedelta(days=args.fit_days)
        report = sweep(bars, base, spread_used, start, fit_end, oos_end,
                       dists, tps, args.balance)
        report["config"] = {"symbol": args.symbol, "spread": round(spread_used, 4),
                            "commission": args.commission}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"\n{args.symbol}: перебор {len(report['rows'])} комбинаций; "
              f"fit {start.date()}..{fit_end.date()}, "
              f"test {fit_end.date()}..{oos_end.date()}", file=sys.stderr)
        print(f"{'dist':>5} {'tp':>5} {'fit ret':>8} {'fit DD':>7} "
              f"{'test ret':>9} {'test DD':>8} {'итог':>10}", file=sys.stderr)
        for r in sorted(report["rows"], key=lambda x: -x["fit_return_pct"]):
            print(f"{r['dist']:>5.1f} {r['tp']:>5.1f} "
                  f"{r['fit_return_pct']:>7.1f}% {r['fit_dd_pct']:>6.1f}% "
                  f"{r['test_return_pct']:>8.1f}% {r['test_dd_pct']:>7.1f}% "
                  f"{r['test_blowup'] or 'жив':>10}", file=sys.stderr)
        return 0

    windows = {
        "video": _in_window(bars, start, end),
        "oos": _in_window(bars, oos_start, oos_end),
        "full": bars,
    }
    report: dict = {
        "config": {**base, "symbol": args.symbol, "terminal": args.terminal,
                   "start_balance": args.balance,
                   "spread_terminal": round(spread, 4),
                   "spread_used": round(spread_used, 4),
                   "start": args.start, "end": oos_end.isoformat()},
        "windows": {},
    }
    for name, chunk in windows.items():
        if not chunk:
            continue
        report["windows"][name] = {
            "bars": len(chunk),
            "first": dt.datetime.fromtimestamp(chunk[0].t, dt.UTC).isoformat(),
            "last": dt.datetime.fromtimestamp(chunk[-1].t, dt.UTC).isoformat(),
            "scenarios": run_scenarios(chunk, base, spread_used, scenarios, args.balance),
        }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n{args.symbol}: M5 {len(bars)} баров, спред {spread_used:.3f} "
          f"(терминал {spread:.3f}), комиссия {args.commission:.2f} USD/лот",
          file=sys.stderr)
    for wname, w in report["windows"].items():
        print(f"\n[{wname}] {w['first'][:10]} .. {w['last'][:10]}", file=sys.stderr)
        print(f"{'сценарий':<9} {'сделки':>7} {'win%':>6} {'PF':>6} "
              f"{'баланс':>10} {'ret%':>8} {'maxDD':>7} {'floATL':>9} "
              f"{'лот':>5} {'поз':>5}", file=sys.stderr)
        for sname, s in w["scenarios"].items():
            pf = "—" if s["profit_factor"] is None else f"{s['profit_factor']:.2f}"
            print(f"{sname:<9} {s['trades']:>7} {s['win_rate'] * 100:>5.1f} "
                  f"{pf:>6} {s['balance']:>10.2f} {s['return_pct']:>7.1f}% "
                  f"{s['max_dd_pct']:>6.1f}% {s['max_floating_loss']:>9.2f} "
                  f"{s['max_lots']:>5.2f} {s['max_open']:>5}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
