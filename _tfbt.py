"""Временный прогон: сравнение стратегии range на разных ТФ (вход + ТФ модели).

Аргумент — список пар "вход:модель" через запятую, напр. "15:240,1:15".
По умолчанию: текущая боевая (15:240) против идеи (1:15).
"""
import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot
import feeds
import strategy as st
import range_v2 as r2
import ta_core as ta

DAYS = int(os.getenv("BT_DAYS", "30"))
RAW = sys.argv[1] if len(sys.argv) > 1 else "15:240,1:15"
PAIRS = []
for item in RAW.split(","):
    item = item.strip()
    if not item:
        continue
    a, _, b = item.partition(":")
    PAIRS.append((a.strip(), (b.strip() or a.strip())))
START = float(os.getenv("PREVIEW_START", "5000"))
RISK_PCT = float(os.getenv("PREVIEW_RISK", "0.5"))
TP_R = float(os.getenv("RANGE_TP_R", "2.0"))
SYMBOLS = ["XAUUSD", "AUDUSD", "USDCAD", "GBPUSD", "US500",
           "EUSTX50", "UK100", "DE40", "JPN225"]


def load_chart(data, sym, tf, bars):
    """График без незакрытого бара (как в bot.load_frames)."""
    f = data.frame(sym, tf, bars)
    return st.Frame(f.t[:-1], f.o[:-1], f.h[:-1], f.l[:-1], f.c[:-1], f.v[:-1])


def collect(data, entry, zone, bars, max_bars):
    out, ws, we, miss, spans = [], None, None, [], {}
    # 1) грузим все символы, отбрасываем «протухшие» (данные кончаются заметно
    #    раньше остальных — у брокера по части тикеров нет свежей истории).
    loaded = {}
    for sym in SYMBOLS:
        try:
            chart = load_chart(data, sym, entry, bars)
            loaded[sym] = chart
        except Exception as e:
            miss.append(f"{sym}({e})")
    if loaded:
        ref = max(float(c.t[-1]) for c in loaded.values())
        stale = {s for s, c in loaded.items()
                 if ref - float(c.t[-1]) > 3 * 86400}
        for s in stale:
            miss.append(f"{s}(устаревшие данные)")
            loaded.pop(s)
    # 2) считаем сделки по актуальным символам
    for sym, chart in loaded.items():
        try:
            span_sec = float(chart.t[-1] - chart.t[0])
            zmin = feeds.TF_MINUTES.get(zone.upper(), 15)
            zone_bars = int(span_sec / (zmin * 60)) + 320
            H = data.frame(sym, zone, zone_bars)
        except Exception as e:
            miss.append(f"{sym}({e})")
            continue
        spans[sym] = (float(chart.t[0]), float(chart.t[-1]), len(chart.c))
        ws = float(chart.t[0]) if ws is None else min(ws, float(chart.t[0]))
        we = float(chart.t[-1]) if we is None else max(we, float(chart.t[-1]))
        zones = r2.zones_for_chart(chart, H, touches=2, flips_keep=bot.RANGE_FLIP_KEEP)
        trades = r2.backtest(chart, zones, H, stop_atr=bot.RANGE_STOP_ATR,
                             max_stops=bot.RANGE_MAX_STOPS, tp_r=TP_R,
                             max_r=TP_R + 2.0, session=bot.RANGE_SESSION,
                             max_bars=max_bars, smt=None, smt_look=0)
        er = ta.efficiency_ratio(chart.c)
        for t in trades:
            e = er[t["entry_i"]]
            if np.isnan(e) or e > bot.RANGE_ER_MAX:
                continue
            t["symbol"] = sym
            t["t_in"] = float(chart.t[t["entry_i"]])
            t["t_out"] = float(chart.t[t["exit_i"]])
            out.append(t)
    out.sort(key=lambda x: x["t_in"])
    return out, ws, we, miss, spans


def curve(sel):
    bal, rows, day_stops, skipped = START, [], {}, 0
    for t in sel:
        day = dt.datetime.fromtimestamp(t["t_in"], dt.timezone.utc).strftime("%Y-%m-%d")
        if bot.MAX_DAILY_STOPS > 0 and day_stops.get(day, 0) >= bot.MAX_DAILY_STOPS:
            skipped += 1
            continue
        risk = bal * RISK_PCT / 100.0
        pnl = t["r"] * risk
        bal += pnl
        if t["r"] < 0:
            day_stops[day] = day_stops.get(day, 0) + 1
        rows.append((t, pnl, bal))
    return rows, skipped


def stats(name, rows, skipped, days):
    if not rows:
        print(f"  {name}: сделок нет")
        return
    rs = [t["r"] for t, _, _ in rows]
    wins = sum(1 for r in rs if r > 0)
    bal = rows[-1][2]
    peak, dd = START, 0.0
    for _, _, b in rows:
        peak = max(peak, b)
        dd = min(dd, (b - peak) / peak * 100.0)
    print(f"  {name:>10}: сделок {len(rs):>4} ({len(rs)/max(days,1):.1f}/дн) · "
          f"прибыльных {wins} ({wins/len(rs)*100:.0f}%) · сумма {sum(rs):+.1f}R · "
          f"баланс {START:.0f}->{bal:.0f}$ ({(bal-START)/START*100:+.1f}%) · "
          f"макс.просадка {dd:.1f}%" + (f" · пропущено {skipped}" if skipped else ""))


def main():
    data = bot.MT5Data()
    res = {}
    try:
        for entry, zone in PAIRS:
            mins = feeds.TF_MINUTES.get(entry.upper(), int(entry))
            bars = int(DAYS * 24 * 60 / mins) + 400
            max_bars = int(24 * 60 / mins)
            tr, ws, we, miss, spans = collect(data, entry, zone, bars, max_bars)
            res[(entry, zone)] = (tr, ws, we, spans)
            print(f"\n=== вход {entry}m / модель {zone}m ===  баров {bars} · сделок {len(tr)}")
            for s, (a, b, n) in spans.items():
                print(f"    {s:<8} {n:>6} бар · "
                      f"{dt.datetime.utcfromtimestamp(a):%d.%m.%y} - "
                      f"{dt.datetime.utcfromtimestamp(b):%d.%m.%y}")
            if miss:
                print("    нет данных: " + "; ".join(miss))
    finally:
        data.close()

    print("\n--- сравнение на общем окне ---")
    ws = max(v[1] for v in res.values() if v[1])
    we = min(v[2] for v in res.values() if v[2])
    days = max((we - ws) / 86400.0, 1)
    print(f"окно: {dt.datetime.utcfromtimestamp(ws):%d.%m.%Y} - "
          f"{dt.datetime.utcfromtimestamp(we):%d.%m.%Y} ({days:.0f} дн.)")
    print(f"правила: риск {RISK_PCT}% · лимит стопов {bot.MAX_DAILY_STOPS}/день · "
          f"TP {TP_R}R · ER<={bot.RANGE_ER_MAX} · стоп {bot.RANGE_STOP_ATR} ATR")
    for (entry, zone), (tr, *_rest) in res.items():
        sel = [t for t in tr if t["t_in"] >= ws and t["t_out"] <= we]
        rows, skipped = curve(sel)
        stats(f"{entry}m/{zone}m", rows, skipped, days)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
