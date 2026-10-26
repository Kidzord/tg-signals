"""Примитивы Pine Script на numpy.

Семантика повторяет ta.* из Pine:
  * ema/rma сеются через SMA первых n значений;
  * окна highest/lowest включают текущий бар;
  * barssince возвращает nan, если условие ещё не выполнялось.

Везде, где Pine отдаёт na, здесь np.nan.
"""
from __future__ import annotations

import numpy as np


def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n or n <= 0:
        return out
    csum = np.cumsum(np.insert(x, 0, 0.0))
    out[n - 1:] = (csum[n:] - csum[:-n]) / n
    return out


def ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n or n <= 0:
        return out
    alpha = 2.0 / (n + 1.0)
    out[n - 1] = float(np.mean(x[:n]))
    for i in range(n, len(x)):
        out[i] = alpha * x[i] + (1.0 - alpha) * out[i - 1]
    return out


def rma(x: np.ndarray, n: int) -> np.ndarray:
    """Сглаживание Уайлдера — то же, что ta.rma."""
    out = np.full(len(x), np.nan)
    if len(x) < n or n <= 0:
        return out
    out[n - 1] = float(np.mean(x[:n]))
    for i in range(n, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def true_range(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    out = np.full(len(h), np.nan)
    if len(h) == 0:
        return out
    out[0] = h[0] - l[0]
    for i in range(1, len(h)):
        out[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    return out


def atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    return rma(true_range(h, l, c), n)


def dmi(h: np.ndarray, l: np.ndarray, c: np.ndarray, di_len: int = 14, adx_len: int = 14):
    """Возвращает (di_plus, di_minus, adx) как ta.dmi."""
    n = len(h)
    tr_ = true_range(h, l, c)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        up = h[i] - h[i - 1]
        dn = l[i - 1] - l[i]
        if up > dn and up > 0:
            plus_dm[i] = up
        if dn > up and dn > 0:
            minus_dm[i] = dn

    tr_s = rma(tr_, di_len)
    plus_s = rma(plus_dm, di_len)
    minus_s = rma(minus_dm, di_len)

    with np.errstate(divide="ignore", invalid="ignore"):
        di_plus = 100.0 * plus_s / tr_s
        di_minus = 100.0 * minus_s / tr_s
        dx = 100.0 * np.abs(di_plus - di_minus) / (di_plus + di_minus)

    # Ранние бары дают 0/0 — Pine там тоже na, но na «отравила» бы весь RMA.
    di_plus = np.nan_to_num(di_plus, nan=0.0)
    di_minus = np.nan_to_num(di_minus, nan=0.0)
    dx = np.nan_to_num(dx, nan=0.0)
    return di_plus, di_minus, rma(dx, adx_len)


def rolling_max(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    for i in range(n - 1, len(x)):
        out[i] = np.max(x[i - n + 1:i + 1])
    return out


def rolling_min(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    for i in range(n - 1, len(x)):
        out[i] = np.min(x[i - n + 1:i + 1])
    return out


def rolling_sum(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    for i in range(n - 1, len(x)):
        out[i] = np.sum(x[i - n + 1:i + 1])
    return out


def barssince(cond: np.ndarray) -> np.ndarray:
    out = np.full(len(cond), np.nan)
    cnt = np.nan
    for i in range(len(cond)):
        if cond[i]:
            cnt = 0.0
        elif not np.isnan(cnt):
            cnt += 1.0
        out[i] = cnt
    return out


def valuewhen(cond: np.ndarray, src: np.ndarray) -> np.ndarray:
    out = np.full(len(cond), np.nan)
    last = np.nan
    for i in range(len(cond)):
        if cond[i]:
            last = src[i]
        out[i] = last
    return out


def crossover(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    out = np.zeros(len(a), dtype=bool)
    for i in range(1, len(a)):
        if not (np.isnan(a[i]) or np.isnan(b[i]) or np.isnan(a[i - 1]) or np.isnan(b[i - 1])):
            out[i] = a[i - 1] <= b[i - 1] and a[i] > b[i]
    return out


def crossunder(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    out = np.zeros(len(a), dtype=bool)
    for i in range(1, len(a)):
        if not (np.isnan(a[i]) or np.isnan(b[i]) or np.isnan(a[i - 1]) or np.isnan(b[i - 1])):
            out[i] = a[i - 1] >= b[i - 1] and a[i] < b[i]
    return out


def cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return crossover(a, b) | crossunder(a, b)


def pivot_high(h: np.ndarray, left: int, right: int) -> np.ndarray:
    """Как ta.pivothigh: значение появляется на баре обнаружения (i = пик + right)."""
    out = np.full(len(h), np.nan)
    for i in range(left + right, len(h)):
        j = i - right
        w = h[j - left: j + right + 1]
        if not np.isnan(w).any() and h[j] >= np.max(w):
            out[i] = h[j]
    return out


def pivot_low(l: np.ndarray, left: int, right: int) -> np.ndarray:
    out = np.full(len(l), np.nan)
    for i in range(left + right, len(l)):
        j = i - right
        w = l[j - left: j + right + 1]
        if not np.isnan(w).any() and l[j] <= np.min(w):
            out[i] = l[j]
    return out


def rsi(x: np.ndarray, n: int = 14) -> np.ndarray:
    up = np.zeros(len(x))
    dn = np.zeros(len(x))
    for i in range(1, len(x)):
        d = x[i] - x[i - 1]
        up[i] = max(d, 0.0)
        dn[i] = max(-d, 0.0)
    ru = rma(up, n)
    rd = rma(dn, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.nan_to_num(100.0 - 100.0 / (1.0 + ru / rd), nan=50.0)


def efficiency_ratio(close: np.ndarray, n: int = 50) -> np.ndarray:
    """Kaufman ER: 1 — чистый тренд, 0 — пила. Меряет «прямизну» движения."""
    out = np.full(len(close), np.nan)
    for i in range(n, len(close)):
        net = abs(close[i] - close[i - n])
        path = np.sum(np.abs(np.diff(close[i - n:i + 1])))
        out[i] = net / path if path > 0 else np.nan
    return out
