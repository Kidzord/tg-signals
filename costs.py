"""Единая модель издержек исполнения для бэктестов, WFA и реестра идей.

До этапа 0 каждый путь считал по-своему: `tools/walk_forward.py` вычитал
плоские 0.05R, `bot.scan_*` не вычитал ничего, `crt.equity_stats` — спред/риск.
Из-за этого числа из разных отчётов нельзя было сравнивать.

Теперь одна формула на все стратегии:

    cost_r = max(MIN_COST_R, spread_price / risk_price) * stress

`spread_price` — спред символа в цене инструмента, `risk_price` — расстояние
от входа до стопа по конкретной сделке. Пол минимум 0.05R нужен, потому что
на закрытом рынке MT5 отдаёт нулевой спред, иначе издержки молча исчезают.
`stress` — множитель для cost stress (обычно 2.0: спред ×2).

Спред берётся в таком порядке:
  1. `WF_SPREAD_<SYMBOL>` / `WF_SPREAD` из окружения (детерминизм, тесты);
  2. текущий спред из `MetaTrader5.symbol_info` терминала;
  3. 0 — тогда работает только пол 0.05R.

Текущий спред — приближение: у M15-баров истории спреда нет, а на closed
market он нулевой. Поэтому пол 0.05R обязателен, а `WF_SPREAD` позволяет
зафиксировать спред для повторяемых прогонов.
"""
from __future__ import annotations

import os

MIN_COST_R = 0.05
SPREAD_ENV_PREFIX = "WF_SPREAD_"


def _env_float(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def stress_from_env() -> float:
    """Множитель cost stress: WF_COST_STRESS=2 — двойной спред."""
    value = _env_float("WF_COST_STRESS")
    return value if value and value > 0 else 1.0


def entry_delay_from_env() -> int:
    """Задержка входа в барах для cost/execution stress (WF_ENTRY_DELAY)."""
    raw = os.getenv("WF_ENTRY_DELAY", "").strip()
    try:
        return max(0, int(float(raw or 0)))
    except ValueError:
        return 0


def symbol_spread(symbol: str, mt5_module=None) -> float:
    """Спред символа в цене инструмента.

    env-переопределение важнее терминала: так прогон воспроизводится, даже
    когда рынок закрыт и MT5 показывает нулевой спред.
    """
    sym = str(symbol or "").strip()
    if sym:
        # Windows-окружение регистронезависимо, но на других ОС проверим оба
        for name in (SPREAD_ENV_PREFIX + sym, SPREAD_ENV_PREFIX + sym.upper()):
            override = _env_float(name)
            if override is not None:
                return override
    override = _env_float("WF_SPREAD")
    if override is not None:
        return override

    if not sym:
        return 0.0
    try:
        if mt5_module is None:
            import MetaTrader5 as mt5_module  # type: ignore[no-redef]
        if not mt5_module.symbol_select(sym, True):
            mt5_module.symbol_select(sym.upper(), True)
        info = mt5_module.symbol_info(sym) or mt5_module.symbol_info(sym.upper())
        if info is None:
            return 0.0
        point = float(getattr(info, "point", 0.0) or 0.0)
        spread = float(getattr(info, "spread", 0.0) or 0.0) * point
        if spread <= 0:
            bid = float(getattr(info, "bid", 0.0) or 0.0)
            ask = float(getattr(info, "ask", 0.0) or 0.0)
            spread = max(0.0, ask - bid)
        return spread
    except Exception:
        return 0.0


def cost_r(spread_price: float, risk_price: float, *, stress: float = 1.0,
           min_cost_r: float = MIN_COST_R) -> float:
    """Издержки одной сделки в R."""
    floor = max(0.0, float(min_cost_r))
    spread = max(0.0, float(spread_price)) * max(0.0, float(stress))
    risk = float(risk_price)
    if risk <= 0:
        return floor
    return max(floor, spread / risk)


def trade_risk(trade: dict) -> float:
    """Риск сделки в цене: поле risk, иначе |entry - stop|."""
    risk = float(trade.get("risk") or 0.0)
    if risk > 0:
        return risk
    try:
        return abs(float(trade["entry"]) - float(trade["stop"]))
    except (KeyError, TypeError, ValueError):
        return 0.0


def net_r(trade: dict, spread_price: float, *, stress: float = 1.0,
          min_cost_r: float = MIN_COST_R) -> float:
    """R сделки после издержек (спред вычитается один раз, на полный проход)."""
    return float(trade["r"]) - cost_r(spread_price, trade_risk(trade),
                                      stress=stress, min_cost_r=min_cost_r)


def net_rs(trades: list[dict], spread_price: float, *, stress: float = 1.0,
           min_cost_r: float = MIN_COST_R) -> list[float]:
    return [net_r(t, spread_price, stress=stress, min_cost_r=min_cost_r)
            for t in trades]


def summary(trades: list[dict], spread_price: float, *, stress: float = 1.0,
            min_cost_r: float = MIN_COST_R) -> dict:
    """Итоги сделок в одном формате для консоли, JSON и research.py."""
    rs = net_rs(trades, spread_price, stress=stress, min_cost_r=min_cost_r)
    gross = sum(float(t["r"]) for t in trades)
    net = sum(rs)
    costs = gross - net
    return {
        "trades": len(trades),
        "gross_r": round(gross, 4),
        "cost_r": round(costs, 4),
        "net_r": round(net, 4),
        "avg_r": round(net / len(rs), 4) if rs else 0.0,
        "wins": sum(1 for r in rs if r > 0),
        "spread": round(float(spread_price), 6),
        "stress": round(float(stress), 3),
    }
