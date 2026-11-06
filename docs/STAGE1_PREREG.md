# Этап 1: pre-registration осей (2026-11-05)

> **Результаты:** `docs/STAGE1_2026-11-05.md`. Список осей и значений ниже
> не менялся после прогонов; все отступления (например, неприменимость
> `RANGE_STOP_PTS` на FX) отмечены в самом файле до запуска.

Файл создан **до** прогонов. Здесь зафиксированы оси, значения, базы и
правило решения; менять список после просмотра результатов нельзя — иначе
это уже data mining. Полный протокол — `docs/RESEARCH_PLAN_2026-11-04.md`,
метрология — `docs/METROLOGY_2026-11-04.md`.

## Протокол

- Фиды: native (MetaQuotes-Demo, UTC+2), M15, 30 000 баров. Второй фид —
  только для значений, прошедших native.
- OOS: те же окна, что в WFA — 15 дней, шаг 15 дней; параметры **фиксированы**
  (без подбора на каждом фолде), чтобы видеть эффект самой оси.
- Издержки: `max(0.05R, спред/риск)`, спред из терминала.
- Вердикт: `net_R > 5`, `folds_active ≥ 8`, доля активных в плюсе ≥ 60%,
  `avg_R > 0.05` (`wfa_verdict.py`).
- Ось считается **нестабильной**, если соседнее значение меняет знак net R
  относительно live-базы.
- Кандидат этапа: значение проходит вердикт на native, не переворачивает
  знак относительно базы, затем проходит Exness (`--shift-hours 2
  --rebuild-htf`) и cost stress (`--stress 2`). Иначе ось закрывается, live
  не меняется.
- Ограничение: не больше 12 значений на ось. Всё, что уже отклонено ранее
  (§2 «не трогаем» плана), не перепроверяется.

Инструмент: `tools/axis_sweep.py` — один параметр за прогон, live-база,
фиксированные OOS-окна. Команды приведены в разделах ниже.

## 1. classic, XAUUSD

База — `bot.PARAMS` (live). `er_min`, `adx_min`, `use_vwap/stack/macd/rsi`
в live **не входят в ворота входа** (`cl_gate` использует r1,r2,r3,r5,r12 и
r6,r7,r8); чтобы проверить их как фильтры, прогон идёт с `er_hard=1`,
`filters_hard=1`, `adx_min>0`. Это research-режим, live по умолчанию не
меняется.

| # | Ось | Значения | База/режим |
|---|---|---|---|
| C1 | `er_min` | 0.10, 0.15, 0.20, 0.25 | `er_hard=1` |
| C2 | `chop_len` | 50, 100, 150 | `er_hard=1` |
| C3 | `stop_mode` | За манипуляцией, За свингом | — |
| C4 | `swing_len` | 10, 20, 30 | `stop_mode=За свингом` |
| C5 | `manip_look` | 10, 20, 30 | — |
| C6 | `max_bars` | 60, 120, 240 | — |
| C7 | `sess_mode` | v16, london, ny, both, none | — |
| C8 | `htf_tf` | 240, 1D | — |
| C9 | `touch_tf` | 60, 15 | — |
| C10 | `tp3_r` | 3.0, 4.5 | — |
| C11 | `use_vwap` | 0, 1 | `filters_hard=1` |
| C12 | `use_stack` | 0, 1 | `filters_hard=1` |
| C13 | `use_macd` | 0, 1 | `filters_hard=1` |
| C14 | `use_rsi` | 0, 1 | `filters_hard=1` |
| C15 | `adx_min` | 0, 20, 25 | — |

```bash
python tools/axis_sweep.py --strategy classic --symbol XAUUSD \
  --axis er_min --values 0.10,0.15,0.20,0.25 --base "er_hard=1"
```

## 2. range, AUDUSD / USDCAD / EURUSD

База — live-настройки флота: AUDUSD `er=0.10, tp=3, trend=with`;
EURUSD `er=0.0, tp=3, trend=with`; USDCAD `er=0.20, tp=3, trend=off`.
`stop_buf` и `max_stop_pts` в `range_v2` заданы **в цене**, поэтому для FX
берутся FX-масштабы, а не 0.1/20 из плана (это золотые величины).
`RANGE_STOP_PTS` (10/15/20) — тоже цена; на FX этот путь не применяется,
ось помечена как неприменимая и не перебирается.

| # | Ось | Значения | База/режим |
|---|---|---|---|
| R1 | `stop_atr` | 0.8, 1.0, 1.2 | — |
| R2 | `stop_mode` | points, swing | — |
| R3 | `stop_buf` (FX) | 0, 0.0002, 0.0005 | `stop_mode=swing` |
| R4 | `swing_len` | 8, 12, 16 | `stop_mode=swing` |
| R5 | `max_stop_pts` (FX) | 0.003, 0.006, 0.010 | `stop_mode=swing` |
| R6 | `tol_mult` | 0.15, 0.20, 0.25 | — |
| R7 | `frac` | 2, 3, 4 | — |
| R8 | `max_r` | 3, 4, 5 | — |
| R9 | `max_stops` | 0, 1, 2 | — |
| R10 | `adx_max` | 0, 20, 25 | — |

```bash
python tools/axis_sweep.py --strategy range --symbol AUDUSD \
  --axis stop_atr --values 0.8,1.0,1.2
```

## 3. ict2022, XAUUSD

База — кандидат `ict_gold3` (`tp_r=3.0`, `stop_buf=0.2`), не live.

| # | Ось | Значения |
|---|---|---|
| I1 | `retest_look` | 20, 40, 60 |
| I2 | `atr_len` | 10, 14, 20 |
| I3 | `max_bars` | 60, 120, 180 |
| I4 | `er_min` | 0, 0.10, 0.15 |
| I5 | `adx_min` | 0, 20, 25 |
| I6 | `session` | "", "0700-2100" |
| I7 | `trend` | off, with |

## 4. pdl_sweep, AUDUSD / USDCAD

PDL закрыт на XAUUSD эталонным WFA этапа 0 (−11.51R). Здесь проверяется
перенос на FX, где идея изначально и предлагалась.

| # | Ось | Значения |
|---|---|---|
| P1 | `tp_r` | 1.5, 2.0, 3.0 |
| P2 | `stop_buf_atr` | 0.15, 0.25, 0.4 |
| P3 | `max_bars` | 48, 96, 144 |
| P4 | `er_min` | 0, 0.10, 0.15 |
| P5 | `adx_min` | 0, 20 |
| P6 | `session` | "", "0700-2100" |

## 5. sonar: закрыть или архивировать

Один честный WFA на AUDUSD и USDCAD (`tools/walk_forward.py sonar`).
На XAUUSD этапа 0: −22.65R, 10/27. Если на FX тоже минус — стратегия
уходит в rejected, модуль остаётся только для истории.

## Что НЕ перебирается

Повторно: `risk_max_atr`, `min_rr`, `trail_k`, `be_after`, `P1/P2` у
classic; `ER`, `side`, `trend`, сессии, BE, trailing, возраст зоны,
D1-конфлюэнс, лимитки, `body/touches` у range. Причина — уже проверено и
зафиксировано в `docs/RANGE_WFA_2026-11-03.md` и `docs/GOLD_VARIANTS_2026-11-03.md`.
