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

1. **Classic XAUUSD не переживает смену фида.** На native +12…+25R, на Exness
   −22…−38R за те же два года. Причина — чувствительность входов/стопов к
   точным high/low и сессиям: система с редкими TP3 и тесными стопами
   переворачивается от небольшого сдвига микроструктуры. Live остановлен.
2. **EURUSD остаётся выведенным.** Native даёт сильный плюс, Exness — около
   нуля/минуса; последние 2.5 месяца отрицательны на обоих фидах.
3. **AUDUSD и USDCAD — единственные, кто в плюсе на обоих фидах.** Их live-
   конфигурации (`range_trend_aud`, `range_live`) дают +6…+19R. Консистентность
   фолдов низкая (32–44%), поэтому риск не повышаем.
4. **ICT gold3 — единственная золотая стратегия с плюсом на обоих фидах**
   (+14.58R native, +8.81R Exness). Кандидат на форвард/тень, не на live:
   фолды 59% и 46%, порог не пройден.
5. Ни одна стратегия не проходит формальный порог 60% фолдов. Для
   низкочастотных систем это ожидаемо, но означает: решение принимается по
   совокупности net R, двух фидов и форварда, а не по одному вердикту.

## Решение по флоту

- live: **AUDUSD (er=0.10, tp=3, trend=with)** и **USDCAD (er=0.20, tp=3)**;
- **XAUUSD classic — пауза** до объяснения расхождения и форварда;
- **EURUSD — вне live** (см. `docs/EURUSD_DECISION_2026-11-04.md`);
- **ICT gold3 — следующий кандидат** на теневой/демо-форвард.

## Воспроизведение

```powershell
python tools/validate_symbol.py --symbol XAUUSD --feed native --bars 30000
MT5_TERMINAL_PATH="C:/MT5-Exness/terminal64.exe" `
  python tools/validate_symbol.py --symbol XAUUSDm --feed exness --bars 50000 `
  --strategies classic,classic_nopart
```

JSON-результаты: `renders/validation/`.
