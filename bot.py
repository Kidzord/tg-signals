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
import mt5_screenshot
import render as card
import sonar
import store
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
                             f"bot_state_{SYMBOL.replace('.', '_')}_{STRATEGY}.json")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "renders")
# Дневной риск-гард: один журнал на все процессы (9 символов пишут в него стопы).
RISK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_risk.log")
# Очередь опубликованных сигналов для premium-бота: он разошлёт их подписчикам сразу.
QUEUE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals_queue.jsonl")
# События закрытия/частичного выхода: premium-бот обновляет ими карточку входа.
RESULTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results_queue.jsonl")
# Журнал закрытых сделок: по нему autoposts.py собирает итог дня и итог недели.
TRADES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.jsonl")
# data_path терминала: через него ScreenshotEA и Python обмениваются файлами.
MT5_DATA_PATH = ""
# 1 — при выходе просить PNG у ScreenshotEA; 0 — всегда рисовать карточку сами.
MT5_SCREENSHOT = os.getenv("MT5_SCREENSHOT", "1").strip().lower() in ("1", "true", "yes")
# Группы коррелированных символов: 4 индекса — почти один рынок, мажоры доллара
# тоже ходят вместе. Кросс-лок не даёт группе набрать стек одновременных ставок.
RISK_GROUP = {
    "US500": "indices", "EUSTX50": "indices", "UK100": "indices", "DE40": "indices",
    "AUDUSD": "usd_fx", "USDCAD": "usd_fx", "GBPUSD": "usd_fx",
    "XAUUSD": "metals",
}
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
# CTA в бесплатной группе: premium-бот без задержки и с расчётом под счёт.
PREMIUM_URL = os.getenv("PREMIUM_URL", "").strip()
PREMIUM_CTA = (f'\n\n⚡ Без задержки и под твой депозит — <a href="{PREMIUM_URL}">Premium</a>'
               if PREMIUM_URL else "")
# «Пульс» бота: monitor.py следит, что файл свежий (иначе алерт владельцу).
HEARTBEAT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              f"hb_{SYMBOL.replace('.', '_')}_{STRATEGY}.json")

PARAMS = st.Params(
    use_ib=False,
    side_allow="Обе",
    mode_soft=True,
    soft_need=2,
    # комбо из честного бэктеста KidzordEA (XAUUSD M15: +12%/90д против +4.8% базы):
    # узкий стоп-коридор и R:R >= 2; env-ручки на случай отката
    risk_max_atr=float(os.getenv("CL_RISK_MAX_ATR", "2.0")),
    min_rr=float(os.getenv("CL_MIN_RR", "2.0")),
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
        global MT5_DATA_PATH
        try:
            info = mt5.terminal_info()
            self.data_path = info.data_path if info else ""
        except Exception:
            self.data_path = ""
        MT5_DATA_PATH = self.data_path

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


def write_heartbeat(info: dict) -> None:
    """Отметка «бот жив»: monitor.py сравнивает mtime и шлёт алерт при простое."""
    try:
        with open(HEARTBEAT_PATH, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "pid": os.getpid(),
                       "symbol": SYMBOL, "strategy": STRATEGY,
                       "bar": info.get("bar"), "ok": bool(info.get("ok"))}, f)
    except OSError:
        pass


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


def append_jsonl(path: str, rec: dict) -> None:
    """Добавить запись в общий jsonl под блокировкой.

    В файл пишут 9+ процессов; обычный append в Windows не гарантирует
    атомарность строки, а по signals_queue.jsonl premium-бот разносит
    сигналы подписчикам. msvcrt.locking даёт взаимное исключение.
    """
    import msvcrt
    line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    try:
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        try:
            os.write(fd, line)
        finally:
            try:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
    except OSError as e:
        print("журнал недоступен:", e)
    finally:
        os.close(fd)


def trade_id_for(sig: card.Signal, bar_ts: float | None = None) -> str:
    """Стабильный ID сделки: по нему premium-бот связывает вход и результат."""
    ts = int(bar_ts if bar_ts is not None else sig.when.timestamp())
    return f"{sig.symbol}_{ts}"


def queue_signal(path: str, caption: str, *, trade_id: str, sig: card.Signal) -> None:
    """Кладём сигнал в очередь — premium-бот разошлёт его подписчикам сразу."""
    rec = {
        "ts": time.time(), "kind": "entry", "trade_id": trade_id,
        "path": os.path.abspath(path), "caption": caption,
        "symbol": sig.symbol, "side": sig.side, "tf": sig.tf,
        "entry": sig.entry, "sl": sig.sl,
        "tp1": sig.tp1, "tp2": sig.tp2, "tp3": sig.tp3,
        "strategy": STRATEGY,
    }
    append_jsonl(QUEUE_PATH, rec)


def queue_result(*, trade_id: str, kind: str, side: str, r: float,
                 total_r: float, entry: float, stop: float | None,
                 exit_px: float | None, note: str = "",
                 symbol: str | None = None) -> None:
    """Событие сделки для premium-бота: он обновит подпись карточки входа."""
    rec = {
        "ts": time.time(), "kind": "result", "event": kind, "trade_id": trade_id,
        "symbol": symbol or SYMBOL, "side": side, "r": round(float(r), 4),
        "total_r": round(float(total_r), 4), "entry": entry, "stop": stop,
        "exit": exit_px, "note": note, "strategy": STRATEGY,
    }
    append_jsonl(RESULTS_PATH, rec)


def _late_note(row, data: "MT5Data | None") -> str:
    """Пометка «цена ушла» для отложенного поста в бесплатную группу."""
    if not LATE_ENABLED or data is None or row["entry"] is None:
        return ""
    try:
        sym = str(row["symbol"] or SYMBOL)
        frame = data.frame(sym, TF, 3)
        price = float(frame.c[-1])
        entry = float(row["entry"])
    except Exception:
        return ""
    if entry <= 0:
        return ""
    side = str(row["side"] or "")
    if side == "LONG" and price < entry * (1 - LATE_MOVE_PCT / 100.0):
        pass
    elif side == "SHORT" and price > entry * (1 + LATE_MOVE_PCT / 100.0):
        pass
    else:
        return ""
    return "\n⚠️ Цена ушла от входа — входить по текущей, не по карточке."


def flush_pending(data: "MT5Data | None" = None) -> None:
    """Доставить посты в бесплатную группу, у которых вышла задержка.

    Очередь в SQLite, поэтому перезапуск бота не теряет отложенный сигнал.
    429 уважаем, сетевые ошибки повторяем с нарастающей паузой.
    """
    for row in store.claim_group():
        cap = str(row["caption"] or "")
        if row["kind"] == "entry":
            cap += _late_note(row, data)
        path = str(row["path"] or "")
        if path and os.path.exists(path):
            res = tg.send_photo_result(path, cap)
        else:
            res = tg.send_message_result(cap)
        if res.ok:
            store.mark_group_sent(row["id"])
            print("группа:", "ок", row["trade_id"], row["kind"])
        elif res.retry_after:
            store.mark_group_retry(row["id"], res.error, res.retry_after)
            print(f"группа: 429, повтор через {res.retry_after:.0f} с")
        elif int(row["attempts"]) + 1 >= 6:
            store.mark_group_terminal(row["id"], "failed", res.error)
            print("группа: не доставлено", row["trade_id"], res.error)
        else:
            delay = min(300, 15 * (2 ** int(row["attempts"])))
            store.mark_group_retry(row["id"], res.error, delay)
            print(f"группа: ошибка ({res.error[:80]}), повтор через {delay} с")


def open_trades_in_group(group: str) -> int:
    """Открытые сделки соседних ботов той же группы (по их state-файлам).

    Сделка считается живой, если она моложе 36 ч (максимум 120 баров M15 +
    запас на простой бота) — иначе state умершего бота не блокирует группу.
    """
    root = os.path.dirname(os.path.abspath(__file__))
    now = time.time()
    n = 0
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    for name in names:
        if not (name.startswith("bot_state_") and name.endswith(".json")):
            continue
        parts = name[len("bot_state_"):-len(".json")].split("_")
        sym = parts[0] if parts else ""
        if sym == SYMBOL or RISK_GROUP.get(sym, sym) != group:
            continue
        try:
            with open(os.path.join(root, name), encoding="utf-8") as f:
                other = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        tr = other.get("trade") if isinstance(other, dict) else None
        if not tr:
            continue
        age_h = (now - float(tr.get("bar_ts", 0))) / 3600.0
        if 0 <= age_h <= 36:
            n += 1
    return n


def post_signal(sig: card.Signal, chart: st.Frame, regime: str | None = None,
                trade_id: str | None = None) -> bool:
    # кросс-символьный риск-лок: не больше CROSS_MAX_OPEN открытых сделок
    # в группе коррелированных символов
    max_open = int(os.getenv("CROSS_MAX_OPEN", "2"))
    group = RISK_GROUP.get(SYMBOL, SYMBOL)
    if max_open > 0:
        same = open_trades_in_group(group)
        if same >= max_open:
            print(f"кросс-лок: группа {group} уже несёт {same} открытых — сигнал пропущен")
            return False
    if DRY:
        print("[dry] сигнал:", sig.side, sig.entry, "SL", sig.sl, "TP", sig.tp1, sig.tp2, sig.tp3)
        return True
    tid = trade_id or trade_id_for(sig)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"signal_{sig.when:%Y%m%d_%H%M%S}.png")
    bars = [feeds.Bar(dt.datetime.fromtimestamp(float(chart.t[j]), dt.timezone.utc),
                      float(chart.o[j]), float(chart.h[j]), float(chart.l[j]), float(chart.c[j]))
            for j in range(len(chart.t))]
    card.render_card(sig, bars, "MT5", False, path)
    caption = voice.signal_caption(sig, regime=regime, source="MT5")
    if STRATEGY not in ("range", "sonar"):
        caption += "\nстратегия: " + STRATEGY
    # premium-подписчики получают сигнал сразу, бесплатная группа — с задержкой.
    # CTA добавляем только в групповую версию: подписчику бота она не нужна.
    queue_signal(path, caption, trade_id=tid, sig=sig)
    due = time.time() + max(0, FREE_DELAY_SEC)
    store.enqueue_group(trade_id=tid, kind="entry", path=path,
                        caption=caption + PREMIUM_CTA,
                        due_ts=due, entry=sig.entry, side=sig.side, symbol=sig.symbol)
    if FREE_DELAY_SEC > 0:
        print(f"в группу уйдёт через {FREE_DELAY_SEC} с")
    else:
        flush_pending()
    if threads.enabled():
        ok2, info2 = threads.post(threads_text(sig))
        print("threads:", ok2, info2)
    return True


def note_trade(kind: str, side: str, r: float, *, trade_id: str = "",
               total_r: float | None = None, entry: float | None = None,
               stop: float | None = None, exit_px: float | None = None,
               note: str = "", image: str | None = None) -> None:
    """Записать итог закрытой сделки в журнал (autoposts.py считает по нему итоги).

    Частичные выходы (tp1/tp2) не пишем: сделка попадает в журнал один раз —
    когда закрылась целиком, и в r уже учтены все части.
    """
    if kind in ("tp1", "tp2"):
        return
    rec = {"ts": time.time(), "symbol": SYMBOL, "side": side, "kind": kind,
           "r": round(float(r), 4), "strategy": STRATEGY,
           "trade_id": trade_id, "tf": TF,
           "total_r": round(float(total_r if total_r is not None else r), 4),
           "entry": entry, "stop": stop, "exit": exit_px, "note": note,
           "image": image}
    append_jsonl(TRADES_PATH, rec)


def post_result(kind: str, side: str, r: float, *, entry: float | None = None,
                stop: float | None = None, exit_px: float | None = None,
                note: str = "", trade_id: str = "",
                total_r: float | None = None,
                chart: "st.Frame | None" = None,
                entry_ts: float | None = None, exit_idx: int | None = None,
                tp1: float | None = None, tp2: float | None = None,
                tp3: float | None = None) -> None:
    """Результат сделки: premium обновляет карточку, группа получает исход."""
    text = voice.event_text(kind, SYMBOL, side, r, note) + PREMIUM_CTA
    if DRY:
        print("[dry]", text.replace("\n", " | "))
        return
    total = float(total_r if total_r is not None else r)
    tid = trade_id or f"{SYMBOL}_{int(time.time())}"
    # Сначала premium: событие durable в jsonl, subbot разложит его по outbox.
    queue_result(trade_id=tid, kind=kind, side=side, r=r, total_r=total,
                 entry=float(entry or 0), stop=stop, exit_px=exit_px, note=note)

    path = ""
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, f"result_{SYMBOL}_{int(time.time() * 1000)}.png")
        rendered = False
        # 1) настоящий скриншот терминала через ScreenshotEA (если он запущен)
        if MT5_SCREENSHOT and MT5_DATA_PATH and entry is not None and stop is not None:
            shot = mt5_screenshot.request(
                SYMBOL, TF, data_path=MT5_DATA_PATH, side=side,
                entry=float(entry), stop=float(stop),
                tp1=tp1, tp2=tp2, tp3=tp3, exit_px=exit_px,
                label=kind, r=r, timeout=12)
            if shot:
                path = shot
                rendered = True
        # 2) иначе — свой график по данным MT5
        if not rendered and chart is not None and entry is not None and stop is not None and exit_idx is not None:
            bars_all = [
                feeds.Bar(dt.datetime.fromtimestamp(float(chart.t[j]), dt.timezone.utc),
                          float(chart.o[j]), float(chart.h[j]),
                          float(chart.l[j]), float(chart.c[j]))
                for j in range(len(chart.t))
            ]
            n = len(bars_all)
            ex_i = max(0, min(int(exit_idx), n - 1))
            if entry_ts is not None:
                en_i = min(range(n), key=lambda j: abs(float(chart.t[j]) - float(entry_ts)))
            else:
                en_i = max(0, ex_i - 60)
            lo = max(0, min(en_i, ex_i) - 10)
            hi = min(n, max(en_i, ex_i) + 10)
            window = bars_all[lo:hi]
            if window:
                card.render_trade_chart(
                    symbol=SYMBOL, side=side, kind=kind, r=r,
                    entry=float(entry), stop=float(stop), exit_px=exit_px,
                    bars=window, tf=TF, entry_idx=en_i - lo, exit_idx=ex_i - lo,
                    out_path=path, tp1=tp1, tp2=tp2, tp3=tp3, note=note)
                rendered = True
        if not rendered:
            card.render_result_card(symbol=SYMBOL, side=side, kind=kind, r=r,
                                    entry=entry, stop=stop, exit_px=exit_px,
                                    note=note, tf=TF, out_path=path)
    except Exception as e:
        print("карточка результата недоступна:", e)
        path = ""
    note_trade(kind, side, r, trade_id=tid, total_r=total,
               entry=entry, stop=stop, exit_px=exit_px, note=note,
               image=path or None)
    # Результат в группе не должен обгонять отложенный вход.
    entry_due = store.group_entry_due(tid) or 0.0
    due = max(time.time(), entry_due + 1.0)
    store.enqueue_group(trade_id=tid, kind=kind, path=path, caption=text,
                        due_ts=due, entry=entry, side=side, symbol=SYMBOL)
    flush_pending()


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

    def postmortem() -> str:
        """Короткий разбор к результату: был ли вход по чек-листу."""
        if not POSTMORTEM_ENABLED or tr.get("marks") is None:
            return ""
        try:
            missing = int(tr.get("missing", 0))
        except (TypeError, ValueError):
            return ""
        if missing <= 0:
            return "Разбор: вход по чек-листу без пропусков."
        return f"Разбор: вход с пропусками чек-листа ({missing}) — сделка по правилам, сетап не идеальный."

    def close(r: float, kind: str, note: str = "", exit_px: float | None = None) -> None:
        stats = state.setdefault("stats", {"n": 0, "wins": 0, "sum_r": 0.0})
        stats["n"] += 1
        stats["sum_r"] += r
        if r > 0:
            stats["wins"] += 1
        full_note = (note + " " + postmortem()).strip()
        post_result(kind, side, r, entry=tr["entry"], stop=tr["stop"],
                    exit_px=exit_px, note=full_note,
                    trade_id=str(tr.get("trade_id", "")), total_r=r,
                    chart=chart, entry_ts=tr.get("bar_ts"), exit_idx=i,
                    tp1=tr.get("tp1"), tp2=tr.get("tp2"), tp3=tr.get("tp3"))
        state["trade"] = None

    # трейлинг UT Bot: подтяжка по закрытию предыдущего бара, со следующего после TP1
    if (PARAMS.use_trail and tr.get("hit1") and PARAMS.trail_after == 1
            and i > 0 and not np.isnan(ta.atr(chart.h, chart.l, chart.c, 14)[i - 1])):
        a_prev = float(ta.atr(chart.h, chart.l, chart.c, 14)[i - 1])
        c_prev = float(chart.c[i - 1])
        if is_long:
            tr["stop"] = max(float(tr["stop"]), c_prev - PARAMS.trail_k * a_prev)
        else:
            tr["stop"] = min(float(tr["stop"]), c_prev + PARAMS.trail_k * a_prev)

    stop = tr["stop"]
    if (lo <= stop if is_long else hi >= stop):
        sgn_t = 1.0 if is_long else -1.0
        stop_r = sgn_t * (float(stop) - float(tr["entry"])) / float(tr["risk"])
        if tr["be"]:
            r = rp
        elif PARAMS.use_trail and tr.get("hit1"):
            r = rp + (1.0 - done) * stop_r
        else:
            r = rp - (1.0 - done)
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
                    exit_px=tp1, note=note,
                    trade_id=str(tr.get("trade_id", "")), total_r=rp,
                    chart=chart, entry_ts=tr.get("bar_ts"), exit_idx=i,
                    tp1=tr.get("tp1"), tp2=tr.get("tp2"), tp3=tr.get("tp3"))
        if PARAMS.be_after == 1:
            tr["stop"], tr["be"] = tr["entry"], True

    if not single and (hi >= tp2 if is_long else lo <= tp2) and tr["hit1"] and not tr["hit2"]:
        tr["hit2"] = True
        add = p2 * r2
        rp += add
        done += p2
        tr["r_partial"], tr["done"] = rp, done
        post_result("tp2", side, add, entry=tr["entry"], stop=tr["stop"], exit_px=tp2,
                    trade_id=str(tr.get("trade_id", "")), total_r=rp,
                    chart=chart, entry_ts=tr.get("bar_ts"), exit_idx=i,
                    tp1=tr.get("tp1"), tp2=tr.get("tp2"), tp3=tr.get("tp3"))
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
    cl = state.setdefault("cl", {"state": 0, "side": 0, "last_exit_ts": 0.0, "last_bar": 0.0})
    if state.get("trade") and bar_ts > state["trade"]["bar_ts"]:
        manage_trade(state, chart, i)
        if not state.get("trade"):
            cl["last_exit_ts"] = float(bar_ts)   # кулдаун после выхода
        save_state(state)
    if state.get("trade"):
        return info

    # автомат сетапа: один переход на новый закрытый бар
    entry = False
    prev_state = int(cl["state"])
    if bar_ts > cl["last_bar"]:
        cl["last_bar"] = float(bar_ts)
        m = {"state": int(cl["state"]), "side": int(cl["side"]),
             "last_exit": None, "in_trade": bool(state.get("trade"))}
        entry, m = cl_gate(res, i, m, PARAMS)
        cl["state"], cl["side"] = int(m["state"]), int(m["side"])

    # тизер: сетап вооружился, но триггера ещё нет — предупреждаем группу
    if (TEASER_ENABLED and not entry and not state.get("trade")
            and prev_state == 0 and int(cl["state"]) == 1
            and bar_ts > float(state.get("last_teaser_bar", 0))):
        t_side = "LONG" if bool(res["is_long"][i]) else "SHORT"
        stop_lvl = float(res["stop_l"][i] if t_side == "LONG" else res["stop_s"][i])
        px = float(chart.c[i])
        dist = abs(px - stop_lvl) / max(px, 1e-9) * 100.0
        if dist <= TEASER_NEAR_PCT:
            ok, info_t = tg.send_message(
                f"⏳ <b>Готовится вход {t_side} {SYMBOL}</b>\n"
                "Сетап собран, ждём триггер. Это не вход, а предупреждение.")
            print("тизер:", ok, info_t)
            state["last_teaser_bar"] = bar_ts
            save_state(state)

    if bar_ts <= state.get("last_signal_bar", 0):
        return info
    bar_sec = feeds.TF_MINUTES.get(TF.upper(), 15) * 60
    if (bar_ts - cl["last_exit_ts"]) < PARAMS.re_entry_bars * bar_sec:
        return info
    if not entry:
        return info

    side = "LONG" if bool(res["is_long"][i]) else "SHORT"
    sig = make_signal(res, chart, i, side)
    if sig.risk <= 0:
        return info

    tid = trade_id_for(sig, bar_ts)
    if post_signal(sig, chart, trade_id=tid):
        state["last_signal_bar"] = bar_ts
        state["trade"] = {
            "trade_id": tid,
            "marks": info.get("marks", ""),
            "missing": info.get("missing", 0),
            "dir": 1 if side == "LONG" else -1,
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


def cl_gate(res: dict, i: int, m: dict, p: "st.Params") -> tuple[bool, dict]:
    """Автомат сетапа KidzordFx (Pine): IDLE -> SETUP -> ENTRY.

    Вход только когда сетап (r1,r2,r3,r5,r12) вооружился на предыдущих барах
    (состояние 1), на текущем баре есть триггер+риск+цель (r6,r7,r8), нет
    открытой сделки, прошёл кулдаун и задержка после свипа. m = {"state",
    "side", "last_exit", "in_trade"}; мутируется на каждом баре.
    """
    is_l = bool(res["is_long"][i])
    side = 1 if is_l else -1
    if side != m["side"]:
        m["state"], m["side"] = 0, side
    r = res["r"]
    setup_now = bool(r[0][i] and r[1][i] and r[2][i] and r[4][i] and r[11][i])
    trig_now = bool(r[5][i] and r[6][i] and r[7][i])
    if not setup_now:
        m["state"] = 0
    elif m["state"] == 0:
        m["state"] = 1
    if m["in_trade"]:
        m["state"] = 2
    cool_ok = m["last_exit"] is None or (i - m["last_exit"]) >= p.re_entry_bars
    sweep_ago = float(res["since_sweep_l"][i] if is_l else res["since_sweep_s"][i])
    sweep_ok = sweep_ago >= p.sweep_delay
    entry = setup_now and trig_now and m["state"] == 1 and not m["in_trade"] and cool_ok and sweep_ok
    if entry:
        m["state"] = 2
    return entry, m


def simulate(res: dict, chart: st.Frame, p: st.Params) -> list[dict]:
    """Прогон по истории теми же правилами, что ведёт живой бот."""
    n = len(chart.c)
    p1 = p.p1_pct / 100.0
    p2 = p.p2_pct / 100.0
    trades: list[dict] = []
    tr = None
    m = {"state": 0, "side": 0, "last_exit": None, "in_trade": False}
    atr = ta.atr(chart.h, chart.l, chart.c, 14)

    for i in range(n):
        if tr:
            hi, lo = float(chart.h[i]), float(chart.l[i])
            is_long = tr["dir"] == 1
            # трейлинг UT Bot: подтяжка стопа по закрытию ПРЕДЫДУЩЕГО бара
            # (эффектен с текущего бара, стоп никогда не отдаляется)
            if p.use_trail and tr.get("h1") and p.trail_after == 1 and i > tr["i"] and not np.isnan(atr[i - 1]):
                if is_long:
                    tr["stop"] = max(tr["stop"], float(chart.c[i - 1]) - p.trail_k * float(atr[i - 1]))
                else:
                    tr["stop"] = min(tr["stop"], float(chart.c[i - 1]) + p.trail_k * float(atr[i - 1]))
            hit_sl = lo <= tr["stop"] if is_long else hi >= tr["stop"]
            hit1 = hi >= tr["tp1"] if is_long else lo <= tr["tp1"]
            hit2 = hi >= tr["tp2"] if is_long else lo <= tr["tp2"]
            hit3 = hi >= tr["tp3"] if is_long else lo <= tr["tp3"]

            if hit_sl:
                sgn_t = 1.0 if is_long else -1.0
                stop_r = sgn_t * (tr["stop"] - tr["entry"]) / tr["risk"]
                if tr["be"]:
                    total = tr["r"]
                elif p.use_trail and tr.get("h1"):
                    total = tr["r"] + (1.0 - tr["done"]) * stop_r
                else:
                    total = tr["r"] - (1.0 - tr["done"])
                trades.append({**tr, "r": total, "exit": "SL", "ei": i, "exit_price": tr["stop"]})
                m["last_exit"] = i
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
                m["last_exit"] = i
                tr = None
                continue
            if i - tr["i"] > p.max_bars:
                c = float(chart.c[i])
                mtm = ((c - tr["entry"]) if is_long else (tr["entry"] - c)) / tr["risk"]
                tr["r"] += (1.0 - tr["done"]) * mtm
                trades.append({**tr, "exit": "TIME", "ei": i, "exit_price": c})
                m["last_exit"] = i
                tr = None
                continue

        m["in_trade"] = tr is not None
        entry, m = cl_gate(res, i, m, p)
        if entry and not tr:
            is_long = bool(res["is_long"][i])
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
    tid = trade_id_for(csig, bar_ts)
    if not post_signal(csig, chart, trade_id=tid):
        return info

    state["last_signal_bar"] = bar_ts
    state["trade"] = {
        "trade_id": tid,
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
    tid = trade_id_for(csig, bar_ts)
    if not post_signal(csig, chart, regime=csig_regime, trade_id=tid):
        return info

    state["last_signal_bar"] = bar_ts
    state["trade"] = {
        "trade_id": tid,
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
    tid = trade_id_for(csig, bar_ts)
    if not post_signal(csig, chart, regime="ICT · свип → FVG", trade_id=tid):
        return info

    state["last_signal_bar"] = bar_ts
    state["trade"] = {
        "trade_id": tid,
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
    store.init_db()
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
            info = step(data, state)
            write_heartbeat(info)
            show(info)
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
                write_heartbeat(info)
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
            flush_pending(data)
            time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        print("остановлено вручную")
    finally:
        if data is not None:
            data.close()


if __name__ == "__main__":
    main()
