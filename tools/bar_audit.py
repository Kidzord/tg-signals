"""Аудит баров: сверка таймфрейма с M1 и с независимым источником.

Зачем: live, research и карточки берут OHLC из одного терминала MT5. Если
брокер теряет бары, отдаёт нулевой диапазон или расходится с другим фидом,
это видно только при сравнении. Инструмент не торгует и ничего не пишет в
очереди — только читает и печатает отчёт.

Режимы:
    python tools/bar_audit.py                         # M15 против M15 из M1
    python tools/bar_audit.py --symbol EURUSD --bars 800
    python tools/bar_audit.py --mode csv --file bars.csv
    python tools/bar_audit.py --mode twelvedata --key ...   # или TWELVEDATA_API_KEY
    python tools/bar_audit.py --dump bars.csv         # выгрузить бары MT5

Код возврата 1 — найдены пропуски или расхождение close больше допуска.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TF_MINUTES = {"1": 1, "5": 5, "15": 15, "30": 30, "60": 60,
              "240": 240, "1D": 1440, "1W": 10080}
TD_INTERVAL = {"1": "1min", "5": "5min", "15": "15min", "30": "30min",
               "60": "1h", "240": "4h", "1D": "1day", "1W": "1week"}


@dataclass
class Bar:
    t: int          # открытие бара, epoch в серверном времени MT5
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


def aggregate(bars: list[Bar], minutes: int) -> list[Bar]:
    """Собрать старший ТФ из младшего: open первого, close последнего."""
    bucket = minutes * 60
    out: dict[int, Bar] = {}
    for b in bars:
        key = int(b.t) // bucket * bucket
        cur = out.get(key)
        if cur is None:
            out[key] = Bar(key, b.o, b.h, b.l, b.c, b.v)
            continue
        cur.h = max(cur.h, b.h)
        cur.l = min(cur.l, b.l)
        cur.c = b.c
        cur.v += b.v
    return [out[k] for k in sorted(out)]


def _index(bars: list[Bar]) -> dict[int, Bar]:
    return {int(b.t): b for b in bars}


def best_shift(native: list[Bar], other: list[Bar], max_hours: int = 12) -> int:
    """Подобрать сдвиг часов: MT5 отдаёт серверное время, API — UTC."""
    have = {int(b.t) for b in native}
    best, best_n = 0, -1
    for shift in range(-max_hours, max_hours + 1):
        n = sum(1 for b in other if int(b.t) + shift * 3600 in have)
        if n > best_n:
            best, best_n = shift, n
    return best


def compare(native: list[Bar], other: list[Bar], *, point: float = 0.0,
            tf_min: int = 15, shift_hours: int | None = None,
            max_report: int = 10) -> dict:
    """Сравнить два набора баров; вернуть отчёт словарём."""
    if shift_hours is None:
        shift_hours = best_shift(native, other)
    shifted = [Bar(b.t + shift_hours * 3600, b.o, b.h, b.l, b.c, b.v) for b in other]
    a, b_map = _index(native), _index(shifted)
    common = sorted(set(a) & set(b_map))
    only_native = sorted(set(a) - set(b_map))
    only_other = sorted(set(b_map) - set(a))

    close_bad: list[tuple[int, float, float]] = []
    high_bad: list[tuple[int, float, float]] = []
    low_bad: list[tuple[int, float, float]] = []
    for t in common:
        x, y = a[t], b_map[t]
        dc = abs(x.c - y.c) / point if point else abs(x.c - y.c)
        dh = abs(x.h - y.h) / point if point else abs(x.h - y.h)
        dl = abs(x.l - y.l) / point if point else abs(x.l - y.l)
        if dc > 0.5:
            close_bad.append((t, x.c, y.c))
        if dh > 1.0:
            high_bad.append((t, x.h, y.h))
        if dl > 1.0:
            low_bad.append((t, x.l, y.l))

    times = sorted(a)
    gaps = [int(times[i + 1] - times[i]) // 60 for i in range(len(times) - 1)]
    max_gap = max(gaps) if gaps else 0
    gap_detail = [(times[i + 1], int(times[i + 1] - times[i]) // 60)
                  for i in range(len(times) - 1)
                  if (times[i + 1] - times[i]) // 60 > tf_min]
    weekend_gaps = [g for _, g in gap_detail if g >= 1440]
    short_gaps = [(t, g) for t, g in gap_detail if g < 1440]
    # ежедневная пауза брокера (ролловер/обслуживание) даёт один и тот же час
    hhmm = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%H:%M")
    seen = {}
    for t, _ in short_gaps:
        seen[hhmm(t)] = seen.get(hhmm(t), 0) + 1
    regular_gaps = [(t, g) for t, g in short_gaps if seen[hhmm(t)] >= 2]
    unexpected_gaps = [(t, g) for t, g in short_gaps if seen[hhmm(t)] < 2]
    zero_times = [t for t in times if a[t].h <= a[t].l]
    duplicates = len(native) - len(a)

    return {
        "native": len(native), "other": len(other), "shift_hours": shift_hours,
        "common": len(common), "only_native": only_native, "only_other": only_other,
        "close_bad": close_bad, "high_bad": high_bad, "low_bad": low_bad,
        "max_gap_min": max_gap, "weekend_gaps": weekend_gaps,
        "regular_gaps": regular_gaps, "unexpected_gaps": unexpected_gaps,
        "zero_times": zero_times, "zero_range": len(zero_times),
        "duplicates": duplicates, "tf_min": tf_min, "max_report": max_report,
    }


def report_text(res: dict) -> str:
    tf = res["tf_min"]
    lines = [
        f"общих баров {res['common']} · native {res['native']} · other {res['other']}"
        f" · сдвиг {res['shift_hours']:+d} ч",
        f"только в native: {len(res['only_native'])}"
        f" · только в other: {len(res['only_other'])} · дублей: {res['duplicates']}",
        f"расхождение close > 0.5 пункта: {len(res['close_bad'])}"
        f" · high {len(res['high_bad'])} · low {len(res['low_bad'])}",
        f"разрывы: выходных {len(res['weekend_gaps'])}"
        f" · регулярных {len(res['regular_gaps'])}"
        f" · прочих {len(res['unexpected_gaps'])} · макс {res['max_gap_min']} мин"
        f" (норма {tf})",
        f"нулевой диапазон: {res['zero_range']}",
    ]
    fmt = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%d.%m %H:%M")
    for title, rows in (("только native", res["only_native"]),
                        ("только other", res["only_other"]),
                        ("close", res["close_bad"]),
                        ("разрыв", res["unexpected_gaps"]),
                        ("нулевой", res["zero_times"])):
        if rows:
            head = rows[:res["max_report"]]
            if title == "close":
                shown = ", ".join(f"{fmt(t)} {x:.5f}≠{y:.5f}" for t, x, y in head)
            elif title == "разрыв":
                shown = ", ".join(f"{fmt(t)} +{g}м" for t, g in head)
            else:
                shown = ", ".join(fmt(t) for t in head)
            lines.append(f"  {title}: {shown}")
    return "\n".join(lines)


# ---------------------------------------------------------------- MT5

def _tf_const(tf: str):
    import MetaTrader5 as mt5

    return {
        1: mt5.TIMEFRAME_M1, 5: mt5.TIMEFRAME_M5, 15: mt5.TIMEFRAME_M15,
        30: mt5.TIMEFRAME_M30, 60: mt5.TIMEFRAME_H1, 240: mt5.TIMEFRAME_H4,
        1440: mt5.TIMEFRAME_D1, 10080: mt5.TIMEFRAME_W1,
    }.get(TF_MINUTES[tf], mt5.TIMEFRAME_M15)


def read_mt5(symbol: str, tf: str, bars: int, path: str | None = None) -> tuple[list[Bar], float]:
    import MetaTrader5 as mt5

    ok = mt5.initialize(path=path) if path else mt5.initialize()
    if not ok:
        raise SystemExit(f"MT5 не инициализировался{f' ({path})' if path else ''}: "
                         "терминал запущен и залогинен?")
    info = mt5.symbol_info(symbol)
    if info is None:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
    if info is None:
        raise SystemExit(f"нет символа {symbol}")
    rates = mt5.copy_rates_from_pos(symbol, _tf_const(tf), 0, bars)
    if rates is None:
        raise SystemExit(f"MT5 не отдал бары {symbol}: {mt5.last_error()}")
    out = [Bar(int(r["time"]), float(r["open"]), float(r["high"]),
               float(r["low"]), float(r["close"]),
               float(r["tick_volume"])) for r in rates]
    return out, 10.0 ** (-int(info.digits))


def self_audit(symbol: str, tf: str, bars: int) -> tuple[dict, float]:
    """M15 (или другой ТФ) против того же ТФ, собранного из M1."""
    native, point = read_mt5(symbol, tf, bars)
    minutes = TF_MINUTES[tf]
    if minutes <= 1:
        raise SystemExit("для self-аудита нужен ТФ старше M1")
    m1, _ = read_mt5(symbol, "1", bars * minutes + 60)
    start = native[0].t
    rebuilt = [b for b in aggregate(m1, minutes) if b.t >= start]
    res = compare(native, rebuilt, point=point, tf_min=minutes, shift_hours=0)
    return res, point


# ---------------------------------------------------------------- внешние

def read_csv(path: str) -> list[Bar]:
    out: list[Bar] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            raw = (row.get("time") or row.get("datetime") or "").strip()
            if not raw:
                continue
            try:
                t = int(float(raw))
            except ValueError:
                t = int(dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
            out.append(Bar(t, float(row["open"]), float(row["high"]),
                           float(row["low"]), float(row["close"]),
                           float(row.get("volume") or row.get("tick_volume") or 0)))
    return sorted(out, key=lambda b: b.t)


def td_symbol(symbol: str) -> str:
    s = symbol.upper()
    if len(s) == 6 and s.isalpha():
        return f"{s[:3]}/{s[3:]}"
    if s.startswith("XAU"):
        return "XAU/USD"
    if s.startswith("XAG"):
        return "XAG/USD"
    return s


def read_twelvedata(symbol: str, tf: str, bars: int, key: str) -> list[Bar]:
    import requests

    r = requests.get("https://api.twelvedata.com/time_series", params={
        "symbol": td_symbol(symbol), "interval": TD_INTERVAL[tf],
        "outputsize": max(1, min(int(bars), 5000)), "apikey": key,
        "timezone": "UTC",
    }, timeout=30)
    data = r.json()
    if isinstance(data, dict) and (data.get("status") == "error" or data.get("code")):
        raise SystemExit(f"Twelve Data: {data.get('message') or data}")
    values = data.get("values") or []
    out = []
    for row in values:
        t = int(dt.datetime.fromisoformat(row["datetime"]).replace(
            tzinfo=dt.timezone.utc).timestamp())
        out.append(Bar(t, float(row["open"]), float(row["high"]),
                       float(row["low"]), float(row["close"]),
                       float(row.get("volume") or 0)))
    return sorted(out, key=lambda b: b.t)


def dump_csv(bars: list[Bar], path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        for b in bars:
            w.writerow([b.t, b.o, b.h, b.l, b.c, b.v])


def main() -> int:
    ap = argparse.ArgumentParser(description="Сверка баров MT5 с M1 или внешним фидом")
    ap.add_argument("--symbol", default=os.getenv("BOT_SYMBOL", "XAUUSD"))
    ap.add_argument("--tf", default="15", choices=sorted(TF_MINUTES))
    ap.add_argument("--bars", type=int, default=500)
    ap.add_argument("--mode", choices=("self", "csv", "twelvedata", "mt5"), default="self")
    ap.add_argument("--file", help="CSV для --mode csv")
    ap.add_argument("--key", default=os.getenv("TWELVEDATA_API_KEY", ""))
    ap.add_argument("--mt5-path", help="второй терминал MT5 (terminal64.exe) для --mode mt5")
    ap.add_argument("--dump", help="выгрузить бары MT5 в CSV и выйти")
    args = ap.parse_args()

    if args.dump:
        bars, _ = read_mt5(args.symbol, args.tf, args.bars, path=args.mt5_path)
        dump_csv(bars, args.dump)
        print(f"выгружено {len(bars)} баров в {args.dump}")
        return 0

    if args.mode == "self":
        res, _ = self_audit(args.symbol, args.tf, args.bars)
    else:
        native, point = read_mt5(args.symbol, args.tf, args.bars)
        if args.mode == "csv":
            if not args.file:
                raise SystemExit("--mode csv требует --file")
            other = read_csv(args.file)
        elif args.mode == "mt5":
            if not args.mt5_path:
                raise SystemExit("--mode mt5 требует --mt5-path")
            import MetaTrader5 as mt5

            mt5.shutdown()
            other, _ = read_mt5(args.symbol, args.tf, args.bars, path=args.mt5_path)
            mt5.shutdown()
        else:
            if not args.key:
                raise SystemExit("нужен --key или TWELVEDATA_API_KEY")
            other = read_twelvedata(args.symbol, args.tf, args.bars, args.key)
        res = compare(native, other, point=point, tf_min=TF_MINUTES[args.tf])

    print(f"{args.symbol} {args.tf} · режим {args.mode}")
    print(report_text(res))
    bad = (len(res["only_native"]) or len(res["only_other"])
           or len(res["close_bad"]) or len(res["unexpected_gaps"]))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
