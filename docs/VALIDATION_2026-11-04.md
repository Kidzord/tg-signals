# Валидация стратегий на двух фидах (2026-11-04)

Прогон: M15, WFA 60 дней подбор → 15 дней OOS, стоимость 0.05R на сделку.
Native — MetaQuotes-Demo. Exness — отдельный демо-терминал, тикеры с `m`.
Классический порог вердикта: net > 5R, ≥60% фолдов в плюсе, средняя > 0.05R.

## Матрица 30 000 баров

| Фид | Стратегия | Сделок | Net R | Фолды+ |
|---|---|---:|---:|---:|
| native XAUUSD | classic | 181 | +12.80 | 14/27 |
| native XAUUSD | classic_nopart | 183 | +19.58 | 13/27 |
| native XAUUSD | ict_gold3 | 305 | +14.58 | 16/27 |
| Exness XAUUSDm | classic | 169 | **−24.78** | 8/26 |
| Exness XAUUSDm | classic_nopart | 164 | **−21.97** | 11/26 |
| Exness XAUUSDm | ict_gold3 | 286 | +8.81 | 12/26 |
| native AUDUSD | range_live | 83 | +0.85 | 8/25 |
| native AUDUSD | range_er10_tp3 | 72 | +10.10 | 11/25 |
| native AUDUSD | range_trend_aud | 52 | +19.10 | 11/25 |
| Exness AUDUSDm | range_live | 35 | +7.25 | 9/25 |
| Exness AUDUSDm | range_er10_tp3 | 26 | +12.70 | 6/25 |
| Exness AUDUSDm | range_trend_aud | 18 | +9.10 | 5/25 |
| native USDCAD | range_live | 56 | +7.41 | 10/25 |
| native USDCAD | range_er20_tp4 | 55 | +19.46 | 10/25 |
| native USDCAD | range_cad_trail | 53 | +7.26 | 9/25 |
| Exness USDCADm | range_live | 46 | +6.26 | 8/25 |
| Exness USDCADm | range_er20_tp4 | 44 | +6.36 | 7/25 |
| Exness USDCADm | range_cad_trail | 45 | +7.55 | 9/25 |
| native EURUSD | range_er0_tp3 | 109 | +21.55 | 11/25 |
| native EURUSD | range_trend_eur | 76 | +40.20 | 11/25 |
| Exness EURUSDm | range_er0_tp3 | 37 | −2.85 | 6/25 |
| Exness EURUSDm | range_trend_eur | 21 | +1.95 | 5/25 |

## Classic на длинной истории (50 000 баров)

| Фид | Вариант | Сделок | Net R | Фолды+ |
|---|---|---:|---:|---:|
| native XAUUSD | classic | 306 | +11.64 | 22/48 |
| native XAUUSD | classic_nopart | 305 | +24.59 | 23/48 |
| Exness XAUUSDm | classic | 291 | **−37.79** | 17/47 |
| Exness XAUUSDm | classic_nopart | 284 | **−25.51** | 19/47 |

Исключение аномального окна 20–25.10 на Exness: classic −20.39R (было −24.78R).
То есть расхождение не объясняется одним участком.

## Выводы

1. **Classic: −38R на Exness — в основном артефакт старших ТФ, а не стратегии.**
   Диагностика `tools/classic_feed_diag.py` (live-параметры, общее окно
   30.07.2025–04.11.2026):

   | Вариант | Сделок | Net R |
   |---|---:|---:|
   | N: native как есть | 206 | +14.81 |
   | N/rebuild: native M15 + HTF из M15 | 117 | +7.62 |
   | E: Exness как есть | 194 | −28.12 |
   | E+2h: сдвиг времени сервера | 213 | −11.30 |
   | E+2h/rebuild: HTF пересобраны из M15 | 76 | +0.24 |
   | E+2h/nogap: убраны ночные 00:00–01:59 | 70 | +2.64 |
   | E+2h/Nhtf: Exness M15 + native HTF | 196 | −9.37 |
   | N/Ehtf: native M15 + Exness rebuilt HTF | 89 | +15.50 |

   У Exness сервер UTC+0, у native UTC+2; брокерские H1/H4/D1/W1 имеют
   другие границы сессий, и classic получает неверный дневной контекст.
   Пересборка HTF из M15 возвращает Exness с −28R к нулю. Native M15 с
   пересобранными Exness HTF даёт +15.5R — то есть сам HTF корректен,
   остаточная разница в микроструктуре M15 Exness (0…+7R).

   Вывод: classic остаётся live **на native**, где M15 и HTF согласованы.
   При переезде на другого брокера старшие ТФ обязательно пересобирать
   из M15, иначе стратегия ломается.
2. **EURUSD остаётся выведенным.** Native даёт сильный плюс, Exness — около
   нуля/минуса; последние 2.5 месяца отрицательны на обоих фидах.
3. **AUDUSD и USDCAD — range-стратегии в плюсе на обоих фидах.** Их live-
   конфигурации (`range_trend_aud`, `range_live`) дают +6…+19R. Консистентность
   фолдов низкая (32–44%), поэтому риск не повышаем.
4. **ICT gold3 — кандидат на форвард/тень**, не на live: +14.58R native,
   +8.81R Exness, но фолды 59% и 46%. Его Exness-прогон тоже надо повторить
   с пересобранными HTF, как classic.
5. Ни одна стратегия не проходит формальный порог 60% фолдов. Для
   низкочастотных систем это ожидаемо, но означает: решение принимается по
   совокупности net R, двух фидов и форварда, а не по одному вердикту.

## Решение по флоту

- live: **AUDUSD (er=0.10, tp=3, trend=with)**, **USDCAD (er=0.20, tp=3)** и
  **XAUUSD classic** — только на native, где HTF согласованы с M15;
- **EURUSD — вне live** (см. `docs/EURUSD_DECISION_2026-11-04.md`);
- **ICT gold3 — следующий кандидат** на теневой/демо-форвард, с проверкой
  на пересобранных HTF;
- обязательное условие для любого нового брокера/переезда: **пересборка
  H1/H4/D1/W1 из M15** (`tools/classic_feed_diag.py` показывает разницу).

## Воспроизведение

```powershell
python tools/validate_symbol.py --symbol XAUUSD --feed native --bars 30000
MT5_TERMINAL_PATH="C:/MT5-Exness/terminal64.exe" `
  python tools/validate_symbol.py --symbol XAUUSDm --feed exness --bars 50000 `
  --strategies classic,classic_nopart
```

JSON-результаты: `renders/validation/`.
