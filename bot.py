"""Бот сигналов: считает логику индикатора по барам MT5 и публикует в Telegram.

Режимы:
    python bot.py            — цикл: опрос MT5, сигналы, ведение сделки
    python bot.py once       — одна проверка и выход
    python bot.py scan       — бэктест стратегии из STRATEGY (range/sonar/classic)

Переменные окружения (кроме .env):
    BOT_TF=15          рабочий таймфрейм
    BOT_POLL_SEC=20    период опроса
    BOT_DRY=1          ничего не отправлять, только печать
    BOT_BARS=1500      сколько баров истории грузить
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feeds
import render as card
import sonar
import strategy as st
import ta_core as ta
import tg
import threads
import voice

TF = os.getenv("BOT_TF", "15").strip() or "15"
POLL_SEC = int(os.getenv("BOT_POLL_SEC", "20"))
DRY = os.getenv("BOT_DRY", "0").strip() in ("1", "true", "yes")
BARS = int(os.getenv("BOT_BARS", "1500"))
SYMBOL = os.getenv("BOT_SYMBOL", "XAUUSD").strip()
STRATEGY = os.getenv("STRATEGY", "sonar").strip().lower()
SONAR_TOUCHES = int(os.getenv("SONAR_TOUCHES", "2"))
SONAR_TARGET_R = float(os.getenv("SONAR_TARGET_R", "2.0"))
RANGE_STOP_ATR = float(os.getenv("RANGE_STOP_ATR", "1.0"))
RANGE_STOP_PTS = float(os.getenv("RANGE_STOP_PTS", "10.0"))
RANGE_MAX_STOPS = int(os.getenv("RANGE_MAX_STOPS", "0"))
RANGE_TP_R = float(os.getenv("RANGE_TP_R", "2.0"))
RANGE_ZONE_TF = os.getenv("RANGE_ZONE_TF", "240").strip()
RANGE_FLIP_KEEP = int(os.getenv("RANGE_FLIP_KEEP", "60"))
RANGE_ER_MAX = float(os.getenv("RANGE_ER_MAX", "0.20"))
# Окно торговли по времени СЕРВЕРА (у этого брокера UTC+2), вид "0800-2300".
# Пусто — фильтр выключен. По бэктесту окно не улучшает ни прибыль, ни риск.
RANGE_SESSION = os.getenv("RANGE_SESSION", "").strip()
# Сколько стопов на счёте за сутки закрывают день (0 — лимит выключен).
MAX_DAILY_STOPS = int(os.getenv("MAX_DAILY_STOPS", "3"))
# SMT-дивергенция Gold/Silver как подтверждение входа: наш инструмент снял
# экстремум, а парный — нет. SMT_LOOK — окно баров рабочего ТФ (0 — выключено);
# SMT_FOR — для какого символа требуем подтверждение.
SMT_LOOK = int(os.getenv("SMT_LOOK", "0"))
SMT_SYMBOL = os.getenv("SMT_SYMBOL", "XAGUSD").strip()
SMT_FOR = os.getenv("SMT_FOR", "XAUUSD").strip()
# ICT 2022: свип ликвидности -> FVG -> вход на возврате в гэп (ict2022.py).
# Верифицировано в тестере MT5 на XAUUSD (+15.1% за 10 мес 2026, +22.6% за 2025);
# на других символах эджа нет — включать только для золота.
ICT_TP_R = float(os.getenv("ICT_TP_R", "4.0"))
ICT_SWING = int(os.getenv("ICT_SWING", "20"))
ICT_FVG_WINDOW = int(os.getenv("ICT_FVG_WINDOW", "20"))
ICT_RETEST_LOOK = int(os.getenv("ICT_RETEST_LOOK", "40"))
ICT_STOP_BUF = float(os.getenv("ICT_STOP_BUF", "0.25"))
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             f"bot_state_{SYMBOL.replace('.', '_')}.json")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "renders")
# Дневной риск-гард: один журнал на все процессы (9 символов пишут в него стопы).
RISK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_risk.log")
# Очередь опубликованных сигналов для premium-бота: он разошлёт их подписчикам сразу.
QUEUE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals_queue.jsonl")
# Журнал закрытых сделок: по нему autoposts.py собирает итог дня и итог недели.
TRADES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.jsonl")
# Задержка публикации сигнала в бесплатную группу, секунд (0 — без задержки).
# Premium-подписчики получают сигнал мгновенно через очередь.
FREE_DELAY_SEC = int(float(os.getenv("FREE_DELAY_SEC", "600") or 600))
# Тизер «готовится вход»: цена подошла к зоне ближе чем этот % — предупреждаем группу
TEASER_ENABLED = os.getenv("TEASER_ENABLED", "1").strip().lower() in ("1", "true", "yes")
TEASER_NEAR_PCT = float(os.getenv("TEASER_NEAR_PCT", "0.15") or 0.15)
# Пометка «поздно» у отложенного поста: цена ушла от входа в нашу сторону больше этого %
LATE_ENABLED = os.getenv("LATE_ENABLED", "1").strip().lower() in ("1", "true", "yes")
LATE_MOVE_PCT = float(os.getenv("LATE_MOVE_PCT", "0.2") or 0.2)
# Разбор к итогу сделки: почему стоп/цель — часть системы, а не случайность
POSTMORTEM_ENABLED = os.getenv("POSTMORTEM_ENABLED", "1").strip().lower() in ("1", "true", "yes")
# Подпись-дисклеймер (как в config.DISCLAIMER, но bot.py читает .env напрямую)
DISCLAIMER = os.getenv("DISCLAIMER", "Не является инвестиционной рекомендацией.").strip()
# «Пульс» бота: autoposts.py следит, что файл свежий (иначе алерт владельцу).
HEARTBEAT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              f"hb_{SYMBOL.replace('.', '_')}.json")
# Отложенные посты в бесплатную группу: (когда, файл, подпись, вход, сторона)
PENDING: list[tuple[float, str, str, float, str]] = []

PARAMS = st.Params(
    use_ib=False,
    side_allow="Обе",
    mode_soft=True,
    soft_need=2,
)


# ------------------------------------------------------------------ MT5

class MT5Data:
    """Одно подключение на процесс: initialize/shutdown стоят дорого."""

    def __init__(self) -> None:
        try:
            import MetaTrader5 as mt5
        except ImportError as e:
            raise RuntimeError("пакет MetaTrader5 не установлен") from e
        self.mt5 = mt5
        if not mt5.initialize(timeout=20000):
            raise RuntimeError(f"MT5 не отвечает: {mt5.last_error()}")
        self._off: dict[str, int] = {}

    def offset(self, symbol: str) -> int:
        """MT5 отдаёт время сервера как есть — не сдвигаем.

        Раньше смещение считалось как tick.time - now, но на закрытом рынке
        тик старый (пятничный), и сдвиг получался в минус сутки. Время бара
        показываем ровно так, как его показывает терминал.
        """
        return 0

    def frame(self, symbol: str, tf: str, count: int) -> st.Frame:
        self.mt5.symbol_select(symbol, True)
        rates = self.mt5.copy_rates_from_pos(symbol, feeds._mt5_timeframe(tf), 0, count)
        if rates is None or len(rates) < 50:
            raise RuntimeError(f"{symbol} {tf}: баров {0 if rates is None else len(rates)}")
        off = self.offset(symbol)
        return st.Frame(
            t=rates["time"].astype("int64") - off,
            o=rates["open"].astype(float),
            h=rates["high"].astype(float),
            l=rates["low"].astype(float),
            c=rates["close"].astype(float),
            v=rates["tick_volume"].astype(float),
        )

    def describe(self) -> str:
        ai = self.mt5.account_info()
        if ai is None:
            return "счёт не определён (терминал не залогинен)"
        kind = "демо" if getattr(ai, "trade_mode", 1) == 0 else "реальный"
        return f"{ai.server} · ****{str(ai.login)[-4:]} · {kind} · {ai.currency}"

    def close(self) -> None:
        try:
            self.mt5.shutdown()
        except Exception:
            pass


def load_frames(data: MT5Data, symbol: str, tf: str, bars: int):
    """График без незакрытого бара + старшие ТФ с ним (иначе сдвиг на бар)."""
    chart = data.frame(symbol, tf, bars)
    chart = st.Frame(chart.t[:-1], chart.o[:-1], chart.h[:-1], chart.l[:-1], chart.c[:-1], chart.v[:-1])

    # Глубина старшего ТФ должна покрывать весь период, иначе на длинном
    # бэктесте первые месяцы останутся без контекста (было ровно так: 800 баров H4 ≈ 133 дня).
    span_sec = float(chart.t[-1] - chart.t[0])

    def need(tf_name: str) -> int:
        mins = feeds.TF_MINUTES.get(tf_name.upper(), 240)
        return int(span_sec / (mins * 60)) + 320

    htf = {
        PARAMS.htf_tf: data.frame(symbol, PARAMS.htf_tf, need(PARAMS.htf_tf)),
        PARAMS.touch_tf: data.frame(symbol, PARAMS.touch_tf, need(PARAMS.touch_tf)),
        "1D": data.frame(symbol, "1D", need("1D")),
        "1W": data.frame(symbol, "1W", need("1W")),
    }
    return chart, htf


# ------------------------------------------------------------------ состояние

def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            return json.load(open(STATE_PATH, encoding="utf-8"))
        except Exception:
            pass
    return {"last_signal_bar": 0, "trade": None, "stats": {"n": 0, "wins": 0, "sum_r": 0.0}}


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _utc_day(bar_ts: float) -> str:
    return dt.datetime.fromtimestamp(float(bar_ts), dt.timezone.utc).strftime("%Y-%m-%d")


def risk_stops_today(bar_ts: float) -> int:
    """Сколько стопов уже было сегодня на счёте (по всем символам)."""
    day = _utc_day(bar_ts)
    try:
        with open(RISK_PATH, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip() == day)
    except FileNotFoundError:
        return 0


def risk_note_stop(bar_ts: float) -> None:
    """Записать стоп на счёт: строка-дата в общий журнал (append атомарен на уровне ОС)."""
    try:
        with open(RISK_PATH, "a", encoding="utf-8") as f:
            f.write(_utc_day(bar_ts) + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------ публикация

def make_signal(res: dict, chart: st.Frame, i: int, side: str) -> card.Signal:
    c = float(chart.c[i])
    risk = abs(c - float(res["stop_l"][i] if side == "LONG" else res["stop_s"][i]))
    sgn = 1.0 if side == "LONG" else -1.0
    when = dt.datetime.fromtimestamp(float(chart.t[i]) + feeds.TF_MINUTES.get(TF.upper(), 15) * 60, dt.timezone.utc)
    return card.Signal(
        side=side, symbol=SYMBOL, tf=TF,
        entry=c,
        sl=c - sgn * risk,
        tp1=c + sgn * risk * PARAMS.tp1_r,
        tp2=c + sgn * risk * PARAMS.tp2_r,
        tp3=c + sgn * risk * PARAMS.tp3_r,
        when=when,
    )


def threads_text(sig: card.Signal, source: str = "MT5") -> str:
    """Короткий текст для Threads: без HTML, до 500 символов."""
    lines = [f"{'LONG' if sig.is_long else 'SHORT'} {sig.symbol} {feeds.tf_label(sig.tf)}", "",
             f"Вход {card.money(sig.entry)}",
             f"Стоп {card.money(sig.sl)} · риск {sig.risk_pct:.2f}%"]
    for n, px in ((1, sig.tp1), (2, sig.tp2), (3, sig.tp3)):
        if px is not None:
            lines.append(f"Цель {card.money(px)} · {sig.r_multiple(px):.1f}R")
    lines += ["", f"{sig.when:%d.%m %H:%M} · {source}", "Не является инвестиционной рекомендацией."]
    return "\n".join(lines)


def queue_signal(path: str, caption: str) -> None:
    """Кладём сигнал в очередь — premium-бот разошлёт его подписчикам сразу."""
    rec = {"ts": time.time(), "path": os.path.abspath(path), "caption": caption}
    try:
        with open(QUEUE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        print("очередь premium недоступна:", e)


def flush_pending() -> None:
    """Посты в бесплатную группу, у которых вышла задержка."""
    now = time.time()
    keep = []
    for due, path, cap in PENDING:
        if now >= due:
            ok, info = tg.send_photo(path, cap)
            print("группа (с задержкой):", ok, info)
        else:
            keep.append((due, path, cap))
    PENDING[:] = keep


def post_signal(sig: card.Signal, chart: st.Frame, regime: str | None = None) -> bool:
    if DRY:
        print("[dry] сигнал:", sig.side, sig.entry, "SL", sig.sl, "TP", sig.tp1, sig.tp2, sig.tp3)
        return True
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"signal_{sig.when:%Y%m%d_%H%M%S}.png")
    bars = [feeds.Bar(dt.datetime.fromtimestamp(float(chart.t[j]), dt.timezone.utc),
                      float(chart.o[j]), float(chart.h[j]), float(chart.l[j]), float(chart.c[j]))
            for j in range(len(chart.t))]
    card.render_card(sig, bars, "MT5", False, path)
    caption = voice.signal_caption(sig, regime=regime, source="MT5")
    # premium-подписчики получают сигнал сразу, бесплатная группа — с задержкой
    queue_signal(path, caption)
    if FREE_DELAY_SEC > 0:
        PENDING.append((time.time() + FREE_DELAY_SEC, path, caption))
        print(f"в группу уйдёт через {FREE_DELAY_SEC} с")
        ok = True
    else:
        ok, info = tg.send_photo(path, caption)
        print("отправка:", ok, info)
    if threads.enabled():
        ok2, info2 = threads.post(threads_text(sig))
        print("threads:", ok2, info2)
    return ok


def note_trade(kind: str, side: str, r: float) -> None:
    """Записать итог закрытой сделки в журнал (autoposts.py считает по нему итоги).

    Частичные выходы (tp1/tp2) не пишем: сделка попадает в журнал один раз —
    когда закрылась целиком, и в r уже учтены все части.
    """
    if kind in ("tp1", "tp2"):
        return
    rec = {"ts": time.time(), "symbol": SYMBOL, "side": side,
           "kind": kind, "r": round(float(r), 4)}
    try:
        with open(TRADES_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        print("журнал сделок недоступен:", e)


def post_result(kind: str, side: str, r: float, *, entry: float | None = None,
                stop: float | None = None, exit_px: float | None = None,
                note: str = "") -> None:
    """Результат сделки: карточка-исход + подпись. Если рендер не удался — текст."""
    text = voice.event_text(kind, SYMBOL, side, r, note)
    if DRY:
        print("[dry]", text.replace("\n", " | "))
        return
    note_trade(kind, side, r)
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, f"result_{SYMBOL}_{int(time.time() * 1000)}.png")
        card.render_result_card(symbol=SYMBOL, side=side, kind=kind, r=r, entry=entry,
                                stop=stop, exit_px=exit_px, note=note, tf=TF,
                                out_path=path)
        ok, info = tg.send_photo(path, text)
    except Exception as e:
        print("карточка результата недоступна:", e)
        ok, info = tg.send_message(text)
    print("результат:", ok, info)


# ------------------------------------------------------------------ ведение сделки

def manage_trade(state: dict, chart: st.Frame, i: int) -> None:
    tr = state.get("trade")
    if not tr:
        return
    hi, lo = float(chart.h[i]), float(chart.l[i])
    is_long = tr["dir"] == 1
    side = "LONG" if is_long else "SHORT"
    p1 = float(tr.get("p1", PARAMS.p1_pct / 100.0))
    p2 = float(tr.get("p2", PARAMS.p2_pct / 100.0))
    # Множители целей берём из самой сделки: у sonar/range цель одна (2R/3R),
    # у classic их три (1R/2R/3R). PARAMS — только значение по умолчанию.
    r1 = float(tr.get("r1", PARAMS.tp1_r))
    r2 = float(tr.get("r2", PARAMS.tp2_r))
    r3 = float(tr.get("r3", PARAMS.tp3_r))
    single = bool(tr.get("single", False))
    done = float(tr.get("done", 0.0))
    rp = float(tr.get("r_partial", 0.0))

    def close(r: float, kind: str, note: str = "", exit_px: float | None = None) -> None:
        stats = state.setdefault("stats", {"n": 0, "wins": 0, "sum_r": 0.0})
        stats["n"] += 1
        stats["sum_r"] += r
        if r > 0:
            stats["wins"] += 1
        post_result(kind, side, r, entry=tr["entry"], stop=tr["stop"],
                    exit_px=exit_px, note=note)
        state["trade"] = None

    stop = tr["stop"]
    if (lo <= stop if is_long else hi >= stop):
        r = rp + (0.0 if tr["be"] else -(1.0 - done))
        # уровень, от которого вошли, «сломан» стопом: считаем промахи и
        # блокируем его, пока он не исчезнет из таблицы зон (структурная инвалидация).
        key = f"{round(float(tr['entry']), 2)}|{'sup' if is_long else 'res'}"
        stops = state.setdefault("stops", {})
        stops[key] = stops.get(key, 0) + 1
        risk_note_stop(float(chart.t[i]))
        close(r, "be" if tr["be"] else "sl", exit_px=tr["stop"])
        return

    tp1, tp2, tp3 = tr["tp1"], tr["tp2"], tr["tp3"]

    if (hi >= tp1 if is_long else lo <= tp1) and not tr["hit1"]:
        if single:
            # одна цель = полный выход, отдельного «tp1 с безубытком» нет
            close(p1 * r1, "tp", exit_px=tp1)
            return
        tr["hit1"] = True
        add = p1 * r1
        rp += add
        done += p1
        tr["r_partial"], tr["done"] = rp, done
        note = "стоп в безубыток" if PARAMS.be_after == 1 else ""
        post_result("tp1", side, add, entry=tr["entry"], stop=tr["stop"],
                    exit_px=tp1, note=note)
        if PARAMS.be_after == 1:
            tr["stop"], tr["be"] = tr["entry"], True

    if not single and (hi >= tp2 if is_long else lo <= tp2) and tr["hit1"] and not tr["hit2"]:
        tr["hit2"] = True
        add = p2 * r2
        rp += add
        done += p2
        tr["r_partial"], tr["done"] = rp, done
        post_result("tp2", side, add, entry=tr["entry"], stop=tr["stop"], exit_px=tp2)
        if PARAMS.be_after == 2:
            tr["stop"], tr["be"] = tr["entry"], True

    if not single and (hi >= tp3 if is_long else lo <= tp3) and tr["hit2"] and not tr["hit3"]:
        tr["hit3"] = True
        close(rp + (1.0 - done) * r3, "tp3", exit_px=tp3)
        return

    if i - tr["bar_idx"] > PARAMS.max_bars:
        c = float(chart.c[i])
        mtm = ((c - tr["entry"]) if is_long else (tr["entry"] - c)) / tr["risk"]
        close(rp + (1.0 - done) * mtm, "time", exit_px=c)


def smt_reference(data: MT5Data, chart: st.Frame):
    """Свечи парного инструмента для SMT-фильтра (None — фильтр выключен/нет данных)."""
    if SMT_LOOK <= 0 or SYMBOL != SMT_FOR:
        return None
    import range_v2
    try:
        return range_v2.smt_ref(chart, data.frame(SMT_SYMBOL, TF, BARS))
    except Exception as e:
        print(f"SMT: нет данных {SMT_SYMBOL} ({e}) — вход без подтверждения")
        return None


def step(data: MT5Data, state: dict) -> dict:
    """Один проход: ведение сделки + проверка нового события. Возвращает статус."""
    chart, htf = load_frames(data, SYMBOL, TF, BARS)
    if STRATEGY == "sonar":
        return step_sonar(state, chart, htf)
    if STRATEGY == "range":
        return step_range(state, chart, htf, smt_reference(data, chart))
    if STRATEGY == "ict":
        return step_ict(state, chart)
    res = st.evaluate(chart, htf, PARAMS)
    if not res.get("ok"):
        return {"ok": False, "reason": res.get("reason")}

    i = len(chart.c) - 1
    bar_ts = float(chart.t[i])
    bar_sec = feeds.TF_MINUTES.get(TF.upper(), 15) * 60
    when = dt.datetime.fromtimestamp(bar_ts + bar_sec, dt.timezone.utc)
    marks = "".join("✓" if res["r"][k][i] else "·" for k in range(12))
    info = {
        "ok": True,
        "bar": bar_ts,
        "when": when,
        "marks": marks,
        "missing": int(res["missing"][i]),
        "side": "LONG" if res["is_long"][i] else "SHORT",
        "posted": False,
        "trade": bool(state.get("trade")),
    }

    # ведём открытую сделку по закрытым барам
    if state.get("trade") and bar_ts > state["trade"]["bar_ts"]:
        manage_trade(state, chart, i)
        save_state(state)

    if state.get("trade"):
        return info

    if bar_ts <= state.get("last_signal_bar", 0):
        return info

    long_evt = bool(res["long_evt"][i])
    short_evt = bool(res["short_evt"][i])
    if not (long_evt or short_evt):
        return info

    side = "LONG" if long_evt else "SHORT"
    sweep_ago = float(res["since_sweep_l"][i] if long_evt else res["since_sweep_s"][i])
    if sweep_ago < PARAMS.sweep_delay:
        return info

    sig = make_signal(res, chart, i, side)
    if sig.risk <= 0:
        return info

    if post_signal(sig, chart):
        state["last_signal_bar"] = bar_ts
        state["trade"] = {
            "dir": 1 if long_evt else -1,
            "entry": sig.entry, "stop": sig.sl, "risk": sig.risk,
            "tp1": sig.tp1, "tp2": sig.tp2, "tp3": sig.tp3,
            "bar_idx": i, "bar_ts": bar_ts,
            "hit1": False, "hit2": False, "hit3": False, "be": False,
            "done": 0.0, "r_partial": 0.0,
        }
        save_state(state)
        info["posted"] = True
        info["trade"] = True
        print(f"СИГНАЛ {side} {sig.entry} · SL {sig.sl} · TP1 {sig.tp1}")
    return info


def simulate(res: dict, chart: st.Frame, p: st.Params) -> list[dict]:
    """Прогон по истории теми же правилами, что ведёт живой бот."""
    n = len(chart.c)
    p1 = p.p1_pct / 100.0
    p2 = p.p2_pct / 100.0
    trades: list[dict] = []
    tr = None

    for i in range(n):
        if tr:
            hi, lo = float(chart.h[i]), float(chart.l[i])
            is_long = tr["dir"] == 1
            hit_sl = lo <= tr["stop"] if is_long else hi >= tr["stop"]
            hit1 = hi >= tr["tp1"] if is_long else lo <= tr["tp1"]
            hit2 = hi >= tr["tp2"] if is_long else lo <= tr["tp2"]
            hit3 = hi >= tr["tp3"] if is_long else lo <= tr["tp3"]

            if hit_sl:
                total = tr["r"] + (0.0 if tr["be"] else -(1.0 - tr["done"]))
                trades.append({**tr, "r": total, "exit": "SL", "ei": i, "exit_price": tr["stop"]})
                tr = None
                continue
            if hit1 and not tr["h1"]:
                tr["h1"] = True
                tr["r"] += p1 * p.tp1_r
                tr["done"] += p1
                if p.be_after == 1:
                    tr["stop"], tr["be"] = tr["entry"], True
            if hit2 and tr["h1"] and not tr["h2"]:
                tr["h2"] = True
                tr["r"] += p2 * p.tp2_r
                tr["done"] += p2
                if p.be_after == 2:
                    tr["stop"], tr["be"] = tr["entry"], True
            if hit3 and tr["h2"] and not tr["h3"]:
                tr["r"] += (1.0 - tr["done"]) * p.tp3_r
                trades.append({**tr, "exit": "TP3", "ei": i, "exit_price": tr["tp3"]})
                tr = None
                continue
            if i - tr["i"] > p.max_bars:
                c = float(chart.c[i])
                mtm = ((c - tr["entry"]) if is_long else (tr["entry"] - c)) / tr["risk"]
                tr["r"] += (1.0 - tr["done"]) * mtm
                trades.append({**tr, "exit": "TIME", "ei": i, "exit_price": c})
                tr = None
                continue

        if not tr and (res["long_evt"][i] or res["short_evt"][i]):
            is_long = bool(res["long_evt"][i])
            c = float(chart.c[i])
            stop = float(res["stop_l"][i] if is_long else res["stop_s"][i])
            risk = abs(c - stop)
            if risk <= 0:
                continue
            sgn = 1 if is_long else -1
            tr = {
                "dir": sgn, "entry": c, "stop": stop, "risk": risk,
                "tp1": c + sgn * risk * p.tp1_r, "tp2": c + sgn * risk * p.tp2_r,
                "tp3": c + sgn * risk * p.tp3_r,
                "i": i, "h1": False, "h2": False, "h3": False, "be": False,
                "r": 0.0, "done": 0.0,
            }
    return trades


def print_trades_stats(trades: list[dict], chart: st.Frame, key: str = "i") -> None:
    """Общая печать итогов бэктеста для scan_classic/scan_range/scan_sonar."""
    bar_sec = feeds.TF_MINUTES.get(TF.upper(), 15) * 60
    rs = [t["r"] for t in trades]
    wins = sum(1 for r in rs if r > 0)
    longs = sum(1 for t in trades if (t["dir"] if "dir" in t else (1 if t["side"] == "LONG" else -1)) == 1)
    print(f"сделок: {len(trades)} (лонг {longs}, шорт {len(trades) - longs}) · "
          f"прибыльных {wins} ({wins / len(trades) * 100:.0f}%)")
    print(f"сумма {sum(rs):+.2f}R · средняя {sum(rs) / len(rs):+.2f}R · "
          f"лучшая {max(rs):+.2f}R · худшая {min(rs):+.2f}R")
    by_exit: dict[str, list[float]] = {}
    for t in trades:
        by_exit.setdefault(t["exit"], []).append(t["r"])
    for k, v in sorted(by_exit.items(), key=lambda x: -len(x[1])):
        print(f"  {k:4}: {len(v):3} шт · сумма {sum(v):+.2f}R")
    print("последние:")
    for t in trades[-6:]:
        when = dt.datetime.fromtimestamp(float(chart.t[t[key]]) + bar_sec, dt.timezone.utc)
        side = t["side"] if "side" in t else ("LONG " if t["dir"] == 1 else "SHORT")
        print(f"  {when:%d.%m %H:%M}  {side:5}  {t['entry']:.2f}  ->  {t['exit']:4}  {t['r']:+.2f}R")


def _trades_bar_key(trades: list[dict]) -> str:
    """classic-сделки хранят индекс в 'i', range/sonar — в 'entry_i'."""
    return "entry_i" if trades and "entry_i" in trades[0] else "i"


def scan(data: MT5Data) -> None:
    """История сигналов и сделок — для сверки с графиком.
    Тестирует ту стратегию, которая выбрана в STRATEGY
    (range/sonar/classic/pdl/ict)."""
    if STRATEGY == "range":
        return scan_range(data)
    if STRATEGY == "sonar":
        return scan_sonar(data)
    if STRATEGY == "pdl":
        return scan_pdl(data)
    if STRATEGY == "ict":
        return scan_ict(data)
    return scan_classic(data)


def scan_generic(title: str, trades: list[dict], chart: st.Frame) -> None:
    """Общая шапка и печать для простых стратегий (pdl/ict)."""
    span_days = (chart.t[-1] - chart.t[0]) / 86400.0
    print(f"{SYMBOL} {feeds.tf_label(TF)} · баров {len(chart.c)} · период {span_days:.1f} дн. · {title}")
    if not trades:
        print("сделок нет")
        return
    print_trades_stats(trades, chart, key="entry_i")


def scan_pdl(data: MT5Data) -> None:
    """Бэктест PDH/PDL sweep: снятие экстремума предыдущего дня с возвратом."""
    import pdl_sweep
    chart, _htf = load_frames(data, SYMBOL, TF, BARS)
    scan_generic("стратегия pdl (снятие экстремума предыдущего дня)",
                 pdl_sweep.backtest(chart), chart)


def scan_ict(data: MT5Data) -> None:
    """Бэктест ICT 2022: свип ликвидности -> FVG -> вход на возврате в гэп."""
    import ict2022
    chart, _htf = load_frames(data, SYMBOL, TF, BARS)
    scan_generic("стратегия ict (свип -> FVG -> ретест)",
                 ict2022.backtest(chart), chart)


def scan_classic(data: MT5Data) -> None:
    """Бэктест classic (перенос KidzordFx V1) теми же правилами, что ведёт живой бот."""
    chart, htf = load_frames(data, SYMBOL, TF, BARS)
    res = st.evaluate(chart, htf, PARAMS)
    if not res.get("ok"):
        print("стратегия:", res.get("reason"))
        return

    bar_sec = feeds.TF_MINUTES.get(TF.upper(), 15) * 60
    span_days = (chart.t[-1] - chart.t[0]) / 86400.0
    n_evt = int(np.sum(res["long_evt"] | res["short_evt"]))
    print(f"{SYMBOL} {feeds.tf_label(TF)} · баров {len(chart.c)} · период {span_days:.1f} дн.")
    print(f"событий входа: {n_evt}")

    trades = simulate(res, chart, PARAMS)
    if trades:
        print_trades_stats(trades, chart)
    else:
        print("сделок нет")

    last = len(chart.c) - 1
    now_bar = dt.datetime.fromtimestamp(float(chart.t[last]) + bar_sec, dt.timezone.utc)
    print(f"\nтекущий бар: {now_bar:%d.%m %H:%M}")
    print("чек-лист:", " ".join(f"r{k+1}={'✓' if res['r'][k][last] else '·'}" for k in range(12)),
          f"| не хватает: {int(res['missing'][last])}")


def scan_range(data: MT5Data) -> None:
    """Бэктест range-стратегии (MSNR) с живыми параметрами по одному символу."""
    import range_v2

    chart, htf = load_frames(data, SYMBOL, TF, BARS)
    H = htf.get(RANGE_ZONE_TF)
    if H is None:
        print("стратегия: нет данных", RANGE_ZONE_TF)
        return
    span_days = (chart.t[-1] - chart.t[0]) / 86400.0
    print(f"{SYMBOL} {feeds.tf_label(TF)} · баров {len(chart.c)} · период {span_days:.1f} дн. · "
          f"стратегия range (зоны {feeds.tf_label(RANGE_ZONE_TF)})")

    zones = range_v2.zones_for_chart(chart, H, touches=2, flips_keep=RANGE_FLIP_KEEP)
    smt = None
    if SMT_LOOK > 0 and SYMBOL == SMT_FOR:
        try:
            ref = data.frame(SMT_SYMBOL, TF, BARS)
            smt = range_v2.smt_ref(chart, ref)
        except Exception as e:
            print(f"SMT: нет данных {SMT_SYMBOL} ({e})")
    trades = range_v2.backtest(chart, zones, H, stop_atr=RANGE_STOP_ATR,
                               tp_r=RANGE_TP_R, max_r=RANGE_TP_R + 2.0,
                               session=RANGE_SESSION, smt=smt, smt_look=SMT_LOOK,
                               max_bars=PARAMS.max_bars, er_max=RANGE_ER_MAX)
    if trades:
        print_trades_stats(trades, chart, key=_trades_bar_key(trades))
    else:
        print("сделок нет")


def scan_sonar(data: MT5Data) -> None:
    """Бэктест sonar-стратегии (реакция от уровня на рабочем ТФ)."""
    chart, htf = load_frames(data, SYMBOL, TF, BARS)
    span_days = (chart.t[-1] - chart.t[0]) / 86400.0
    print(f"{SYMBOL} {feeds.tf_label(TF)} · баров {len(chart.c)} · период {span_days:.1f} дн. · "
          f"стратегия sonar")
    atr = sonar.build_atr(chart)
    levels = sonar.find_levels(chart, atr)
    trades = sonar.backtest(chart, atr, levels)
    if trades:
        print_trades_stats(trades, chart, key=_trades_bar_key(trades))
    else:
        print("сделок нет")


def step_sonar(state: dict, chart: st.Frame, htf: dict) -> dict:
    """Вход по реакции от уровня (Sonar) с фильтром тренда 4H."""
    i = len(chart.c) - 1
    bar_ts = float(chart.t[i])
    bar_sec = feeds.TF_MINUTES.get(TF.upper(), 15) * 60
    when = dt.datetime.fromtimestamp(bar_ts + bar_sec, dt.timezone.utc)

    atr = sonar.build_atr(chart)
    lv = sonar.find_levels(chart, atr, min_touches=SONAR_TOUCHES)
    levels = lv[i]["levels"]

    info = {"ok": True, "bar": bar_ts, "when": when, "marks": f"{len(levels)} ур.",
            "missing": 0, "side": "-", "posted": False,
            "trade": bool(state.get("trade"))}

    if state.get("trade") and bar_ts > state["trade"]["bar_ts"]:
        manage_trade(state, chart, i)
        save_state(state)
    if state.get("trade"):
        return info
    if bar_ts <= state.get("last_signal_bar", 0):
        return info

    sig = sonar.detect_at(chart, atr, lv, i)
    if not sig:
        return info
    side, stop = sig

    H = htf.get(PARAMS.htf_tf)
    ema = st._htf(chart.t, H, ta.ema(H.c, PARAMS.htf_ema_len), 1)[i]
    if np.isnan(ema):
        return info
    if (side == "LONG" and chart.c[i] <= ema) or (side == "SHORT" and chart.c[i] >= ema):
        return info

    entry = float(chart.c[i])
    risk = abs(entry - stop)
    if risk <= 0:
        return info
    sgn = 1 if side == "LONG" else -1
    tp = entry + sgn * risk * SONAR_TARGET_R
    info["side"] = side

    csig = card.Signal(side=side, symbol=SYMBOL, tf=TF,
                       entry=entry, sl=stop, tp1=tp, when=when)
    if not post_signal(csig, chart):
        return info

    state["last_signal_bar"] = bar_ts
    state["trade"] = {
        "dir": sgn, "entry": csig.entry, "stop": csig.sl,
        "risk": abs(csig.entry - csig.sl),
        "tp1": tp, "tp2": tp, "tp3": tp,
        "bar_idx": i, "bar_ts": bar_ts,
        "hit1": False, "hit2": False, "hit3": False, "be": False,
        "done": 0.0, "r_partial": 0.0, "p1": 1.0, "p2": 0.0,
        "single": True, "r1": SONAR_TARGET_R,
    }
    save_state(state)
    info["posted"] = True
    info["trade"] = True
    print(f"СИГНАЛ {side} {csig.entry} · SL {csig.sl} · цель {tp}")
    return info


def step_range(state: dict, chart: st.Frame, htf: dict, smt=None) -> dict:
    """Вход от границы зоны старшего ТФ (MSNR): лимитка у уровня, короткий стоп."""
    import range_v2

    i = len(chart.c) - 1
    bar_ts = float(chart.t[i])
    bar_sec = feeds.TF_MINUTES.get(TF.upper(), 15) * 60
    when = dt.datetime.fromtimestamp(bar_ts + bar_sec, dt.timezone.utc)

    H = htf.get(RANGE_ZONE_TF)
    if H is None:
        return {"ok": False, "reason": f"нет данных {RANGE_ZONE_TF}"}
    zones = range_v2.zones_for_chart(chart, H, touches=2, flips_keep=RANGE_FLIP_KEEP)
    levels = zones[i]["levels"]

    info = {"ok": True, "bar": bar_ts, "when": when, "marks": f"{len(levels)} зон",
            "missing": 0, "side": "-", "posted": False,
            "trade": bool(state.get("trade"))}

    if state.get("trade") and bar_ts > state["trade"]["bar_ts"]:
        manage_trade(state, chart, i)
        save_state(state)
    if state.get("trade"):
        return info
    if bar_ts <= state.get("last_signal_bar", 0):
        return info

    # фильтр режима: во флэте преимущество, в тренде его почти нет
    er = ta.efficiency_ratio(chart.c)[i]
    if np.isnan(er) or er > RANGE_ER_MAX:
        return info

    # сессионное окно (killzone): пустое значение — фильтр выключен
    if RANGE_SESSION and not st.in_session(bar_ts, RANGE_SESSION):
        return info

    # дневной риск-гард: после N стопов на счёте день закрыт, новых входов нет
    if MAX_DAILY_STOPS > 0 and risk_stops_today(bar_ts) >= MAX_DAILY_STOPS:
        info["marks"] += " · дневной лимит стопов"
        return info

    # структурная инвалидация: уровень, от которого уже ловили стопы, не берём,
    # пока он не исчезнет из таблицы зон (т.е. пока его не сломали по структуре).
    # RANGE_MAX_STOPS=0 — механизм выключен.
    stops = state.get("stops", {})
    present = {f"{round(float(l['price']), 2)}|{l['kind']}" for l in levels}
    state["stops"] = {k: v for k, v in stops.items() if k in present}
    stops = state["stops"]
    sig = None
    for lv in levels:
        price = float(lv["price"])
        if RANGE_MAX_STOPS > 0 and stops.get(f"{round(price, 2)}|{lv['kind']}", 0) >= RANGE_MAX_STOPS:
            continue
        if lv["kind"] == "sup" and chart.l[i] <= price and chart.c[i] > price:
            sig = ("LONG", price)
            break
        if lv["kind"] == "res" and chart.h[i] >= price and chart.c[i] < price:
            sig = ("SHORT", price)
            break
    if not sig:
        return info

    side, entry = sig
    sgn = 1 if side == "LONG" else -1

    # SMT-подтверждение: парный инструмент (серебро) не снял тот же экстремум
    if not range_v2.smt_ok(chart, smt, i, side, SMT_LOOK):
        info["marks"] += " · нет SMT"
        return info

    if RANGE_STOP_ATR > 0:
        a_c = ta.atr(chart.h, chart.l, chart.c, 14)[i]
        if np.isnan(a_c) or a_c <= 0:
            return info
        risk = float(a_c) * RANGE_STOP_ATR
    else:
        risk = RANGE_STOP_PTS
    stop = entry - sgn * risk
    # стоп мог выбить на баре входа — тогда сделки не было
    if (side == "LONG" and chart.l[i] <= stop) or (side == "SHORT" and chart.h[i] >= stop):
        return info

    # цель та же, что в бэктесте: тянем до противоположной зоны, если она рядом
    target = range_v2.pick_target(levels, side, entry, risk, RANGE_TP_R,
                                  max_r=RANGE_TP_R + 2.0)
    info["side"] = side

    csig = card.Signal(side=side, symbol=SYMBOL, tf=TF,
                       entry=entry, sl=stop, tp1=target, when=when)
    regime = "тихий флэт" if er <= 0.08 else "флэт"
    if SMT_LOOK > 0 and smt is not None:
        regime += " · SMT"
    csig_regime = regime
    if not post_signal(csig, chart, regime=csig_regime):
        return info

    state["last_signal_bar"] = bar_ts
    state["trade"] = {
        "dir": sgn, "entry": csig.entry, "stop": csig.sl,
        "risk": abs(csig.entry - csig.sl),
        "tp1": csig.tp1, "tp2": csig.tp1, "tp3": csig.tp1,
        "bar_idx": i, "bar_ts": bar_ts,
        "hit1": False, "hit2": False, "hit3": False, "be": False,
        "done": 0.0, "r_partial": 0.0, "p1": 1.0, "p2": 0.0,
        "single": True, "r1": abs(target - entry) / risk,
    }
    save_state(state)
    info["posted"] = True
    info["trade"] = True
    print(f"СИГНАЛ {side} {csig.entry} · SL {csig.sl} · цель {csig.tp1}")
    return info


def step_ict(state: dict, chart: st.Frame) -> dict:
    """Вход по ICT 2022 (ict2022.signal_at): свип -> FVG -> возврат в гэп.

    Отличие от бэктеста: карточка публикуется на закрытии сигнального бара
    с входом по его цене закрытия (подписчики входят по рынку), а не по
    открытию следующего — расхождение в пределах спреда.
    """
    import ict2022

    i = len(chart.c) - 1
    bar_ts = float(chart.t[i])
    bar_sec = feeds.TF_MINUTES.get(TF.upper(), 15) * 60
    when = dt.datetime.fromtimestamp(bar_ts + bar_sec, dt.timezone.utc)

    info = {"ok": True, "bar": bar_ts, "when": when, "marks": "ICT",
            "missing": 0, "side": "-", "posted": False,
            "trade": bool(state.get("trade"))}

    if state.get("trade") and bar_ts > state["trade"]["bar_ts"]:
        manage_trade(state, chart, i)
        save_state(state)

    # машина состояний идёт по каждому закрытому бару, даже пока открыта
    # сделка; сигнал на таком баре теряется — как в бэктесте
    ict_state = state.setdefault("ict", {"l": None, "s": None})
    atr = sonar.build_atr(chart)
    sig = ict2022.signal_at(ict_state, chart, i, float(atr[i]),
                            swing_len=ICT_SWING, fvg_window=ICT_FVG_WINDOW,
                            retest_look=ICT_RETEST_LOOK, stop_buf_atr=ICT_STOP_BUF,
                            bar_sec=bar_sec)

    if state.get("trade"):
        return info
    if bar_ts <= state.get("last_signal_bar", 0):
        return info

    # дневной риск-гард: как у range, общий на счёт
    if MAX_DAILY_STOPS > 0 and risk_stops_today(bar_ts) >= MAX_DAILY_STOPS:
        info["marks"] += " · дневной лимит стопов"
        return info

    if not sig:
        return info

    side, stop_raw = sig
    sgn = 1 if side == "LONG" else -1
    entry = float(chart.c[i])
    risk = abs(entry - stop_raw)
    if risk <= 0:
        return info
    stop = entry - sgn * risk
    tp = entry + sgn * risk * ICT_TP_R
    info["side"] = side

    csig = card.Signal(side=side, symbol=SYMBOL, tf=TF,
                       entry=entry, sl=stop, tp1=tp, when=when)
    if not post_signal(csig, chart, regime="ICT · свип → FVG"):
        return info

    state["last_signal_bar"] = bar_ts
    state["trade"] = {
        "dir": sgn, "entry": csig.entry, "stop": csig.sl,
        "risk": abs(csig.entry - csig.sl),
        "tp1": tp, "tp2": tp, "tp3": tp,
        "bar_idx": i, "bar_ts": bar_ts,
        "hit1": False, "hit2": False, "hit3": False, "be": False,
        "done": 0.0, "r_partial": 0.0, "p1": 1.0, "p2": 0.0,
        "single": True, "r1": ICT_TP_R,
    }
    save_state(state)
    info["posted"] = True
    info["trade"] = True
    print(f"СИГНАЛ {side} {csig.entry} · SL {csig.sl} · цель {csig.tp1}")
    return info


def show(info: dict) -> None:
    if not info.get("ok"):
        print("ошибка:", info.get("reason"))
        return
    print(f"бар {info['when']:%d.%m %H:%M} · {info['side']} · {info['marks']} · "
          f"не хватает {info['missing']}" + (" · сделка открыта" if info["trade"] else ""))


# ------------------------------------------------------------------ запуск

def connect() -> MT5Data | None:
    """Подключение к терминалу; None — если он недоступен."""
    try:
        return MT5Data()
    except Exception as e:
        print("НЕ УДАЛОСЬ ПОДКЛЮЧИТЬСЯ К MT5:", e)
        return None


def main() -> None:
    state = load_state()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    data = connect()
    if data is None:
        print("проверь: терминал MT5 запущен и залогинен, Python и терминал на одном ПК")
        print("диагностика: python app.py mt5test")
        return

    try:
        print(f"MT5: {data.describe()}")
        if cmd == "scan":
            scan(data)
            return
        if cmd == "once":
            show(step(data, state))
            return

        print(f"бот запущен: {SYMBOL} {feeds.tf_label(TF)} · опрос {POLL_SEC} с · "
              f"{'ТЕСТ (ничего не отправляю)' if DRY else 'постит в Telegram'}")
        print("дальше будет видно каждый новый бар; Ctrl+C — остановить")
        last_bar = None
        last_beat = 0.0
        while True:
            if data is None:
                data = connect()
                if data is None:
                    time.sleep(POLL_SEC)
                    continue
                print(f"переподключение к MT5: {data.describe()}")

            try:
                info = step(data, state)
            except Exception as e:
                print("ошибка шага:", repr(e), "— переподключаюсь к MT5")
                try:
                    data.close()
                except Exception:
                    pass
                data = None
                info = {"ok": False}

            now = time.time()
            if info.get("ok"):
                if info["bar"] != last_bar:
                    last_bar = info["bar"]
                    print(f"новый бар {info['when']:%d.%m %H:%M} · {info['side']} · "
                          f"{info['marks']} · не хватает {info['missing']}")
                    last_beat = now
                elif info.get("posted"):
                    print("сигнал отправлен")
                    last_beat = now
                elif now - last_beat > 300:
                    print(f"жив · бар {info['when']:%d.%m %H:%M} · {info['marks']} · "
                          f"не хватает {info['missing']}")
                    last_beat = now
            else:
                last_beat = now
            flush_pending()
            time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        print("остановлено вручную")
    finally:
        if data is not None:
            data.close()


if __name__ == "__main__":
    main()
