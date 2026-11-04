"""Размер позиции: риск в валюте счёта → объём в лотах под стоп сигнала.

`loss_per_lot` считается через `mt5.order_calc_profit`, а не через
`trade_tick_value`: у XAUUSD tick_value на демо-счёте занижен в 10 раз
(0.1 вместо 1.0), и формула с ним даёт объём в 10 раз меньше нужного.
"""
from __future__ import annotations

import math

import config as cfg


def _fallback_loss(info, entry: float, stop: float) -> float:
    """Потеря на 1 лот по спецификации контракта, если order_calc_profit недоступен."""
    try:
        import MetaTrader5 as mt5

        account = mt5.account_info()
        if account is None or info.currency_profit != account.currency:
            return 0.0
        return abs(float(stop) - float(entry)) * float(info.trade_contract_size)
    except Exception:
        return 0.0


def measure(symbol: str, entry: float, stop: float) -> dict | None:
    """Спецификация объёма для сигнала: потеря на 1 лот, шаг, минимум и максимум."""
    if not symbol or not entry or not stop or float(entry) == float(stop):
        return None
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None
    try:
        if not cfg.mt5_init():
            return None
        info = mt5.symbol_info(symbol)
        if info is None:
            mt5.symbol_select(symbol, True)
            info = mt5.symbol_info(symbol)
        if info is None:
            return None
        profit = mt5.order_calc_profit(mt5.ORDER_TYPE_SELL, symbol, 1.0,
                                       float(entry), float(stop))
        loss = abs(float(profit)) if profit is not None else 0.0
        if loss <= 0:
            loss = _fallback_loss(info, entry, stop)
        if loss <= 0:
            return None
        return {
            "loss_per_lot": round(loss, 2),
            "step": float(info.volume_step or 0.01),
            "min": float(info.volume_min or 0.01),
            "max": float(info.volume_max or 100.0),
        }
    except Exception:
        return None


def size_for_risk(risk_money: float, size: dict | None) -> dict | None:
    """Объём под заданный риск: округление вниз по шагу, но не ниже минимума."""
    if not size or risk_money <= 0:
        return None
    try:
        loss_per_lot = float(size.get("loss_per_lot") or 0)
        step = float(size.get("step") or 0.01)
        minimum = float(size.get("min") or step)
        maximum = float(size.get("max") or 0) or 1e9
    except (TypeError, ValueError):
        return None
    if loss_per_lot <= 0 or step <= 0:
        return None
    raw = float(risk_money) / loss_per_lot
    lots = math.floor((raw + 1e-9) / step) * step
    exceeds = lots < minimum
    lots = maximum if lots > maximum else (minimum if exceeds else lots)
    actual = lots * loss_per_lot
    return {
        "lots": round(lots, 8),
        "risk": round(actual, 2),
        "exceeds": bool(exceeds or actual > float(risk_money) + 1e-6),
    }


def fmt_lots(lots: float, step: float = 0.01) -> str:
    """0.05, 0.003 — столько знаков, сколько в шаге объёма."""
    decimals = 2
    if 0 < step < 1:
        decimals = max(2, int(round(-math.log10(step))))
    return f"{float(lots):.{decimals}f}"
