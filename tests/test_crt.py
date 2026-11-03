"""Тесты CRT: детект C1/C2, фильтры, варианты classic/failed, расчёт R."""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import crt  # noqa: E402
import strategy as st  # noqa: E402
import ta_core as ta  # noqa: E402


def atr_at(L, i):
    return float(ta.atr(L.h, L.l, L.c, 14)[i])

T0 = 1_700_000_000
H_TF = 4 * 3600       # старший ТФ (H4)
L_TF = 3600           # рабочий ТФ (H1), 4 бара на свечу H4
NEUTRAL = (104.0, 110.0, 100.0, 104.0)   # диапазон 100–110, тело 0
C1_AT = 19            # C1 — свеча перед C2
C2_AT = 20
SIGNAL_I = 21 * (H_TF // L_TF)           # первый бар рабочего ТФ после закрытия C2


def make_frames(hbars):
    """Собрать из списка свечей старшего ТФ обе серии (H4 и H1)."""
    n = len(hbars)
    t_h = np.array([T0 + j * H_TF for j in range(n)], dtype=np.int64)
    H = st.Frame(
        t=t_h,
        o=np.array([b[0] for b in hbars], float),
        h=np.array([b[1] for b in hbars], float),
        l=np.array([b[2] for b in hbars], float),
        c=np.array([b[3] for b in hbars], float),
        v=np.ones(n),
    )
    rows = [b for b in hbars for _ in range(H_TF // L_TF)]
    t_l = np.array([T0 + j * L_TF for j in range(len(rows))], dtype=np.int64)
    L = st.Frame(
        t=t_l,
        o=np.array([b[0] for b in rows], float),
        h=np.array([b[1] for b in rows], float),
        l=np.array([b[2] for b in rows], float),
        c=np.array([b[3] for b in rows], float),
        v=np.ones(len(rows)),
    )
    return L, H


def bars(c2=NEUTRAL, after=NEUTRAL):
    seq = [NEUTRAL] * 40
    seq[C2_AT] = c2
    if after is not None:
        seq[C2_AT + 1] = after
    return seq


def test_bullish_setup_detected_with_midpoint_and_boundary():
    c2 = (104.0, 107.0, 99.0, 102.5)     # свип низа C1 и закрытие внутрь
    L, H = make_frames(bars(c2))
    S = crt.scan(L, H, crt.CrtParams())

    assert bool(S["long"][SIGNAL_I]) and not bool(S["short"][SIGNAL_I])
    assert S["crh"][SIGNAL_I] == pytest.approx(110.0)
    assert S["crl"][SIGNAL_I] == pytest.approx(100.0)
    assert S["eq"][SIGNAL_I] == pytest.approx(105.0)
    # стоп — за свипом C2 (99) минус 0.25 ATR рабочего ТФ
    assert S["stop"][SIGNAL_I] == pytest.approx(99.0 - 0.25 * atr_at(L, SIGNAL_I))
    assert S["tp1"][SIGNAL_I] == pytest.approx(105.0)
    assert S["tp2"][SIGNAL_I] == pytest.approx(110.0)


def test_short_setup_is_mirror():
    c2 = (106.0, 111.0, 103.0, 107.0)    # свип верха C1 и закрытие внутрь
    L, H = make_frames(bars(c2))
    S = crt.scan(L, H, crt.CrtParams())

    assert bool(S["short"][SIGNAL_I]) and not bool(S["long"][SIGNAL_I])
    assert S["stop"][SIGNAL_I] == pytest.approx(111.0 + 0.25 * atr_at(L, SIGNAL_I))
    assert S["tp2"][SIGNAL_I] == pytest.approx(100.0)


def test_c2_close_outside_is_not_classic_but_failed_variant_trades_it():
    outside = (104.0, 112.0, 100.5, 111.0)   # закрытие выше C1, свип только верха
    L, H = make_frames(bars(outside))

    classic = crt.scan(L, H, crt.CrtParams(variant="classic"))
    assert not classic["long"][SIGNAL_I] and not classic["short"][SIGNAL_I]

    failed = crt.scan(L, H, crt.CrtParams(variant="failed"))
    assert bool(failed["long"][SIGNAL_I])
    stop = 110.0 - 0.25 * atr_at(L, SIGNAL_I)
    # стоп — за пробитым уровнем CRH (110) минус буфер, цель 1R от входа (111)
    assert failed["stop"][SIGNAL_I] == pytest.approx(stop)
    assert failed["tp2"][SIGNAL_I] == pytest.approx(111.0 + (111.0 - stop))


def test_dual_sweep_rejected_by_default_and_kept_without_filter():
    c2 = (104.0, 111.0, 99.0, 103.0)     # пробиты обе границы, закрытие внутри
    L, H = make_frames(bars(c2))

    assert not crt.scan(L, H, crt.CrtParams())["long"][SIGNAL_I]
    loose = crt.scan(L, H, crt.CrtParams(require_dual_filter=False))
    assert bool(loose["long"][SIGNAL_I]) or bool(loose["short"][SIGNAL_I])


def test_deep_sweep_filter_skips_wide_stop():
    # прокол низа на 6 из 10 (60% диапазона) — стоп слишком широкий
    c2 = (104.0, 107.0, 94.0, 103.0)
    L, H = make_frames(bars(c2))
    assert not crt.scan(L, H, crt.CrtParams())["long"][SIGNAL_I]
    assert crt.scan(L, H, crt.CrtParams(max_sweep_frac=0.70, min_rr=0.0))["long"][SIGNAL_I]


def test_backtest_partial_then_boundary():
    c2 = (104.0, 107.0, 99.0, 102.5)
    up = (103.0, 115.0, 102.0, 114.0)    # следующий бар проходит обе цели
    L, H = make_frames(bars(c2, up))
    trades = crt.backtest(L, H, crt.CrtParams())

    assert trades and trades[0]["side"] == "LONG" and trades[0]["entry_i"] == SIGNAL_I
    t = trades[0]
    assert t["entry"] == pytest.approx(103.0)      # открытие следующего бара
    assert t["exit"] == "TP2"
    risk = 103.0 - (99.0 - 0.25 * atr_at(L, SIGNAL_I))
    assert t["r"] == pytest.approx(0.5 * (105.0 - 103.0) / risk
                                   + 0.5 * (110.0 - 103.0) / risk, rel=1e-6)


def test_backtest_stop_loss_costs_one_r():
    c2 = (104.0, 107.0, 99.0, 102.5)
    down = (103.0, 104.0, 80.0, 92.0)    # бар после входа пробивает стоп
    L, H = make_frames(bars(c2, down))
    trades = crt.backtest(L, H, crt.CrtParams())

    assert trades and trades[0]["exit"] == "SL"
    assert trades[0]["r"] == pytest.approx(-1.0)


def test_params_from_env_reads_switches():
    p = crt.params_from_env({
        "CRT_VARIANT": "failed", "CRT_MIN_RR": "2.5",
        "CRT_DUAL_FILTER": "0", "CRT_C1_COLOR": "1", "CRT_SESSION": "0800-1700",
        "CRT_SL_MODE": "level", "CRT_TP_MODE": "zone",
        "CRT_STOP_MAX_ATR": "4.5", "CRT_LEVEL_TOUCHES": "3",
    })
    assert p.variant == "failed"
    assert p.min_rr == pytest.approx(2.5)
    assert p.require_dual_filter is False
    assert p.c1_color is True
    assert p.session == "0800-1700"
    assert p.sl_mode == "level" and p.tp_mode == "zone"
    assert p.stop_max_atr == pytest.approx(4.5)
    assert p.level_touches == 3


LEVELS = [
    {"kind": "sup", "price": 101.0, "touches": 2},
    {"kind": "sup", "price": 105.0, "touches": 3},
    {"kind": "res", "price": 109.0, "touches": 2},
    {"kind": "res", "price": 112.0, "touches": 4},
]


def test_level_stop_takes_nearest_level_in_band():
    p = crt.CrtParams(stop_min_atr=0.1, stop_max_atr=5.0)
    # лонг: ближайшая поддержка ниже входа — 105, а не 101
    assert crt.pick_level_stop(LEVELS, "LONG", 107.0, 2.0, p) == 105.0
    # шорт: ближайшее сопротивление выше входа — 109
    assert crt.pick_level_stop(LEVELS, "SHORT", 107.0, 2.0, p) == 109.0
    # слишком близкий уровень отбрасывается как шум
    assert crt.pick_level_stop([{"kind": "sup", "price": 105.0}],
                               "LONG", 105.1, 2.0, p) is None
    # слишком далёкий уровень тоже не годится
    assert crt.pick_level_stop([{"kind": "sup", "price": 101.0}],
                               "LONG", 130.0, 2.0, p) is None


def test_level_target_takes_nearest_zone_within_r_band():
    p = crt.CrtParams(tp_min_r=1.0, tp_max_r=4.0)
    # entry 102.5, risk 3.5 → коридор [3.5, 14]: ближайшее сопротивление 109
    assert crt.pick_level_target(LEVELS, "LONG", 102.5, 3.5, p) == 109.0
    # широкий стоп risk 10 → коридор [10, 40]: 109 уже не дотягивает
    assert crt.pick_level_target(LEVELS, "LONG", 102.5, 10.0, p) is None
    # шорт: ближайшая поддержка ниже входа
    assert crt.pick_level_target(LEVELS, "SHORT", 111.0, 3.0, p) == 105.0


def test_min_risk_filter_skips_microscopic_stop():
    c2 = (104.0, 107.0, 99.0, 102.5)
    L, H = make_frames(bars(c2))
    # риск ~6.5 при ATR ~9.6 — обычный сетап проходит
    assert crt.scan(L, H, crt.CrtParams())["long"][SIGNAL_I]
    # порог 10 ATR отсекает любой сетап
    assert not crt.scan(L, H, crt.CrtParams(min_risk_atr=10.0))["long"][SIGNAL_I]


def test_equity_stats_compounds_and_tracks_drawdown():
    tr = [
        {"entry": 100.0, "stop": 99.0, "risk": 1.0, "r": 1.0},
        {"entry": 100.0, "stop": 99.0, "risk": 1.0, "r": -1.0},
    ]
    s = crt.equity_stats(tr, bank=1000.0, risk_pct=10.0)
    assert s["final"] == pytest.approx(990.0)        # 1000 → 1100 → 990
    assert s["return_pct"] == pytest.approx(-1.0)
    assert s["max_dd_pct"] == pytest.approx(10.0)
    assert s["worst_trade_pct"] == pytest.approx(-10.0)


def test_equity_stats_subtracts_spread_in_r():
    tr = [{"entry": 100.0, "stop": 99.0, "risk": 1.0, "r": 1.0}]
    s = crt.equity_stats(tr, bank=1000.0, risk_pct=10.0, spread=0.1)
    assert s["final"] == pytest.approx(1090.0)       # net R 0.9 → +9%


def test_structural_modes_use_strong_levels(monkeypatch):
    import range_v2

    c2 = (104.0, 107.0, 99.0, 102.5)
    L, H = make_frames(bars(c2))
    rows = [{"bar": -1, "levels": LEVELS} for _ in range(len(L.c))]
    monkeypatch.setattr(range_v2, "zones_for_chart", lambda *a, **k: rows)

    p = crt.CrtParams(sl_mode="level", tp_mode="zone",
                      stop_min_atr=0.1, stop_max_atr=5.0)
    S = crt.scan(L, H, p)

    atr = atr_at(L, SIGNAL_I)
    assert S["stop"][SIGNAL_I] == pytest.approx(101.0 - 0.25 * atr)
    assert S["tp2"][SIGNAL_I] == pytest.approx(109.0)
    assert S["tp1"][SIGNAL_I] == pytest.approx(105.0)

