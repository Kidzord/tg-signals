"""Единый вердикт walk-forward: одна формула для tools/walk_forward.py и research.py.

До этапа 0 WFA считал долю ВСЕХ фолдов (включая пустые), а docs и реестр
цитировали долю АКТИВНЫХ — числа были несопоставимы. Теперь правило одно:

    net_R > 5  и  folds_active >= 8  и
    folds_active_positive / folds_active >= 0.6  и  avg_R > 0.05

Пустые фолды не попадают в знаменатель: они говорят о частоте сделок, а не
о качестве сигнала.
"""
from __future__ import annotations

MIN_NET_R = 5.0
MIN_ACTIVE_FOLDS = 8
MIN_ACTIVE_POS_SHARE = 0.6
MIN_AVG_R = 0.05

PASS = "ПРОХОДИТ"
FAIL = "НЕ ПРОХОДИТ"


def active_share(folds_active: int, folds_active_positive: int) -> float:
    return folds_active_positive / folds_active if folds_active > 0 else 0.0


def verdict(net_r: float, folds_active: int, folds_active_positive: int,
            avg_r: float) -> str:
    """ПРОХОДИТ / НЕ ПРОХОДИТ по нормализованной метрике активных фолдов."""
    if folds_active <= 0:
        return FAIL
    if (float(net_r) > MIN_NET_R
            and folds_active >= MIN_ACTIVE_FOLDS
            and active_share(folds_active, folds_active_positive) >= MIN_ACTIVE_POS_SHARE
            and float(avg_r) > MIN_AVG_R):
        return PASS
    return FAIL


def reasons(net_r: float, folds_active: int, folds_active_positive: int,
            avg_r: float) -> list[str]:
    """Почему вердикт не пройден — для консоли и реестра идей."""
    out: list[str] = []
    if float(net_r) <= MIN_NET_R:
        out.append(f"net {float(net_r):+.2f}R (нужно > {MIN_NET_R:g}R)")
    if folds_active < MIN_ACTIVE_FOLDS:
        out.append(f"активных фолдов {folds_active} (нужно ≥ {MIN_ACTIVE_FOLDS})")
    share = active_share(folds_active, folds_active_positive)
    if share < MIN_ACTIVE_POS_SHARE:
        out.append(f"активных в плюсе {folds_active_positive}/{folds_active} "
                   f"({share:.0%}, нужно ≥ {MIN_ACTIVE_POS_SHARE:.0%})")
    if float(avg_r) <= MIN_AVG_R:
        out.append(f"средняя {float(avg_r):+.3f}R (нужно > {MIN_AVG_R:g}R)")
    return out
