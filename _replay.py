"""Разовая публикация «прожитой недели» в канал.

    python _replay.py        — показать ленту, ничего не отправлять
    python _replay.py send   — отправить в группу по порядку

Лента собирается из реального бэктеста (month.collect + month.balance_curve):
утренние планы, сигналы (вход/стоп/цель), результаты, итоги дня, итог недели, промо.
Каждый пост — карточкой, текстом идёт только подпись.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot
import config as cfg
import feeds
import month
import render as card
import tg
import voice

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "renders", "replay")
MSK = dt.timezone(dt.timedelta(hours=3))
DAYS = 30
SEND = len(sys.argv) > 1 and sys.argv[1] == "send"
DELAY = 3.5

PREMIUM_URL = "https://t.me/KidzordPremim_bot"

# Эмодзи на карточках не отрисуются шрифтом DejaVu — вырезаем их из текста.
EMOJI = re.compile("[\U0001F300-\U0001FAFF\uFE0F]")

PLANS = {
    "19.10": (
        "Утро доброе 👋\n"
        "Неделя открылась без разрывов, рынок вялый. Смотрю <b>AUDUSD</b> — третий день ходит "
        "у нижней границы диапазона. Если вернётся и оттолкнётся от уровня — беру лонг.\n"
        "По индексам интересен <b>японец</b>: стоит у верхней границы, покупатели не пробивают. "
        "Не пробьют — шорт от уровня.\n"
        "Золото пока в стороне, присмотрюсь ближе к вечеру.\n"
        "Сигналы будут здесь, по одному, как войду."
    ),
    "20.10": (
        "Доброе утро.\n"
        "Австралиец снова у той же поддержки — вчера уровень отработал на цель, значит покупатель "
        "здесь живой. Жду ещё один подход.\n"
        "По японцу цена не смогла уйти выше уровня — держу в голове шорт, но входить буду только "
        "от чёткой реакции.\n"
        "День тихий, работаю по правилам: нет реакции — нет сделки."
    ),
    "21.10": (
        "Среда.\n"
        "Главное за ночь — стоп по японцу, который вёл со вчера. Ничего страшного, риск был ограничен.\n"
        "Сегодня основной интерес — <b>GBPUSD</b>: подошёл к уровню, реакции пока нет, жду отбой.\n"
        "Японец сейчас в самой активной фазе, могу взять его ещё раз, но только от уровня и с "
        "подтверждением."
    ),
    "22.10": (
        "Четверг, утро.\n"
        "Ночью по фунту был стоп, но структура не сломана — уровень держит. Жду возврат и беру лонг "
        "снова.\n"
        "Это тот самый момент, когда система говорит «ещё раз», а эмоции — «хватит». Работаю по системе.\n"
        "Японец у нижней границы, но реакции нет — не лезу."
    ),
    "23.10": (
        "Пятница, последний торговый день недели.\n"
        "Ночь была тяжёлой: два стопа по сделкам, которые вёл со вчера. Спокойно — это часть работы.\n"
        "Сегодня торгую аккуратно, без разгона. Нет реакции — закрываю неделю как есть, этого достаточно.\n"
        "Вечером подведу итог недели, посчитаю честно, со стопами."
    ),
}

DAY_SUMS = {
    "19.10": "4 сделки закрылось: 1 цель, 3 стопа.\nПо R: <b>0.0R</b> · по счёту ≈ <b>-1$</b>.\n"
             "Ровно, в ноль. Такие дни тоже нужны: главное — риск на каждой сделке был ограничен, "
             "и день не выбил из колеи.",
    "20.10": "2 сделки закрылось: 1 цель, 1 стоп.\nПо R: <b>+2.0R</b> · по счёту ≈ <b>+60$</b>.\n"
             "Австралиец отработал как задумано — вернулся к уровню и дошёл до цели.",
    "21.10": "Ночь забрала японца — минус. Дальше ещё два срабатывания по нему же, и только "
             "четвёртый подход дал цель. Плюс стоп по фунту.\nПо R: <b>-1.0R</b> · по счёту ≈ <b>-32$</b>.\n"
             "Минус небольшой — размер позиции и лимиты своё дело сделали.",
    "22.10": "Лучший день за неделю. 3 сделки закрылось: 2 цели, 1 стоп.\n"
             "По R: <b>+5.0R</b> · по счёту ≈ <b>+153$</b>.\n"
             "Фунт отработал ровно по плану: возврат к уровню и два движения на цель подряд.",
    "23.10": "Тяжёлая пятница: 4 стопа, ни одной цели.\nПо R: <b>-4.0R</b> · по счёту ≈ <b>-124$</b>.\n"
             "Обидно, но это часть системы: у нас цель втрое больше стопа, поэтому серия стопов — "
             "нормальная плата за редкие крупные цели. Правила не меняю.",
}

PROMO_A = (
    "⚡ <b>Сигналы без задержки</b>\n\n"
    "В бесплатной группе сигнал появляется с задержкой. В Premium он приходит сразу — вход, стоп "
    "и цель, пока цена ещё у уровня.\n"
    "Плюс бот считает твой счёт: вбиваешь депозит и риск — получаешь карточку с лимитами под себя.\n\n"
    "Подписка — 500 ⭐ в месяц."
)

PROMO_B = (
    "📊 <b>Неделя: 13 стопов и всего 5 целей. И всё равно в плюсе.</b>\n\n"
    "Потому что цель втрое больше стопа. Это и есть риск-менеджмент: не угадывать, а считать.\n"
    "Хочешь видеть сигналы в момент появления и считать риск под свой депозит — Premium в боте."
)


def week_messages() -> list[dict]:
    os.makedirs(OUT, exist_ok=True)
    data = bot.MT5Data()
    try:
        all_trades = month.collect(data)
        last = max(t["t_out"] for t in all_trades)
        sel = [t for t in all_trades if t["t_in"] >= last - DAYS * 86400]
        pairs, _ = month.balance_curve(sel)
        rows = [{"t": t, "pnl": pnl, "bal": b} for t, pnl, b in pairs]
        # кадры графиков нужны для карточек сигналов
        frames = {sym: bot.load_frames(data, sym, "15", int(os.getenv("BOT_BARS", "20000")))
                  for sym in {t["symbol"] for t in all_trades}}
    finally:
        data.close()

    monday = (dt.datetime.fromtimestamp(last, MSK)
              - dt.timedelta(days=dt.datetime.fromtimestamp(last, MSK).weekday())
              ).replace(hour=0, minute=0, second=0, microsecond=0)
    ws = monday.timestamp()

    bal_before = month.START
    week = []
    for r in rows:
        if r["t"]["t_in"] < ws:
            bal_before = r["bal"]
            continue
        week.append(r)

    msgs: list[dict] = []

    def add(day: str, hhmm: str, text: str, markup=None, photo: str | None = None) -> None:
        key = dt.datetime.strptime(f"{day}.2026 {hhmm}", "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
        msgs.append({"key": key, "text": text, "markup": markup, "photo": photo})

    def signal_png(t: dict, idx: int) -> str:
        chart, _ = frames[t["symbol"]]
        i = int(np.searchsorted(chart.t, t["t_in"], side="left"))
        i = min(max(i, 5), len(chart.c) - 1)
        sig = card.Signal(side=t["side"], symbol=t["symbol"], tf="15",
                          entry=float(t["entry"]), sl=float(t["stop"]),
                          tp1=float(t["target"]),
                          when=dt.datetime.fromtimestamp(t["t_in"], dt.timezone.utc))
        bars = [feeds.Bar(dt.datetime.fromtimestamp(float(chart.t[j]), dt.timezone.utc),
                          float(chart.o[j]), float(chart.h[j]), float(chart.l[j]),
                          float(chart.c[j])) for j in range(max(0, i - 119), i + 1)]
        path = os.path.join(OUT, f"sig_{idx:02d}_{t['symbol']}.png")
        card.render_card(sig, bars, "MT5", False, path)
        return path

    # утренние планы — карточкой
    for day, plan in PLANS.items():
        path = os.path.join(OUT, f"plan_{day}.png")
        body = [EMOJI.sub("", p) for p in plan.split("\n")]
        card.render_plan_card(title="План на день", subtitle=day, body=body, out_path=path)
        add(day, "04:00", f"🌅 <b>План на день · {day}</b>", photo=path)

    # сигналы и результаты — каждый карточкой
    for k, r in enumerate(week, 1):
        t = r["t"]
        sym, side = t["symbol"], t["side"]
        long = side == "LONG"
        ent, sl, tp = float(t["entry"]), float(t["stop"]), float(t["target"])
        risk_pct = abs(ent - sl) / ent * 100
        rr = abs(tp - ent) / abs(ent - sl)
        intro = voice._pick(voice.INTRO_LONG if long else voice.INTRO_SHORT,
                            f"{sym}{t['t_in']}{side}").format(sym=sym)
        close = voice._pick(voice.CLOSE, f"{sym}{t['t_in']}c")
        arrow = "↑" if long else "↓"
        sig = (f"{intro}\n\n"
               f"{arrow} вход <b>{card.money(ent)}</b>\n"
               f"стоп {card.money(sl)} — {risk_pct:.2f}% от входа\n"
               f"цель: {card.money(tp)}  ({rr:.1f}R)\n\n"
               f"{close}\n\n"
               f"15 мин · {dt.datetime.fromtimestamp(t['t_in'], MSK):%d.%m %H:%M} МСК\n"
               f"<i>{cfg.DISCLAIMER}</i>")
        add(dt.datetime.fromtimestamp(t["t_in"], MSK).strftime("%d.%m"),
            dt.datetime.fromtimestamp(t["t_in"], MSK).strftime("%H:%M"), sig,
            photo=signal_png(t, k))

        exit_s = str(t["exit"])
        if exit_s.startswith("SL"):
            kind, exit_px = "sl", sl
        elif exit_s == "TP":
            kind, exit_px = "tp", tp
        else:
            kind, exit_px = "time", None
        res = voice.event_text(kind, sym, side, float(t["r"]))
        res_path = os.path.join(OUT, f"res_{k:02d}_{sym}.png")
        card.render_result_card(symbol=sym, side=side, kind=kind, r=float(t["r"]),
                                entry=ent, stop=sl, exit_px=exit_px, tf="15",
                                when=dt.datetime.fromtimestamp(t["t_out"], MSK),
                                out_path=res_path)
        add(dt.datetime.fromtimestamp(t["t_out"], MSK).strftime("%d.%m"),
            dt.datetime.fromtimestamp(t["t_out"], MSK).strftime("%H:%M"), res,
            photo=res_path)

    # итоги дней — карточкой
    by_day: dict[str, list[dict]] = {}
    for r in week:
        by_day.setdefault(dt.datetime.fromtimestamp(r["t"]["t_in"], MSK).strftime("%d.%m"),
                          []).append(r)
    for day in sorted(by_day, key=lambda d: by_day[d][0]["t"]["t_in"]):
        drs = by_day[day]
        rs_d = [r["t"]["r"] for r in drs]
        prose = [p for p in DAY_SUMS.get(day, "").split("\n") if p.strip()]
        note = EMOJI.sub("", card.plain(prose[-1])).strip() if prose else ""
        path = os.path.join(OUT, f"day_{day}.png")
        card.render_summary_card(card.SummaryPlan(
            title="Итог дня", period=day, trades=len(rs_d),
            wins=sum(1 for x in rs_d if x > 0), sum_r=sum(rs_d),
            bal_start=drs[0]["bal"] - drs[0]["pnl"], bal_end=drs[-1]["bal"],
            note=note, when=dt.datetime.fromtimestamp(drs[-1]["t"]["t_out"], MSK)), path)
        cap = f"📋 <b>Итог дня · {day}</b>"
        if note:
            cap += f"\n\n{note}"
        add(day, "23:30", cap, photo=path)

    # промо
    add("20.10", "10:00", PROMO_A, {"inline_keyboard": [[{"text": "⚡ Оформить Premium",
                                                          "url": PREMIUM_URL}]]})

    # итог недели — карточкой
    rs = [r["t"]["r"] for r in week]
    wins = sum(1 for x in rs if x > 0)
    bal_end = week[-1]["bal"]

    def usd(v: float) -> str:
        return f"{v:,.0f}".replace(",", " ")

    path = os.path.join(OUT, "week.png")
    card.render_summary_card(card.SummaryPlan(
        title="Итог недели", period="19–23 октября", trades=len(rs), wins=wins,
        sum_r=sum(rs), bal_start=bal_before, bal_end=bal_end,
        note=f"С начала ведения счёта (с {usd(month.START)} $): "
             f"{(bal_end / month.START - 1) * 100:+.1f}%",
        when=dt.datetime.fromtimestamp(week[-1]["t"]["t_out"], MSK)), path)
    week_note = (
        "Лучший день — четверг, <b>+5R</b>. Худший — пятница, <b>−4R</b>.\n\n"
        "Честно: неделя слабая по количеству сделок, но система сделала главное — "
        "не дала уйти в минус и не дала разогнаться. Дисциплина дороже эффектных цифр.\n"
        "Новая неделя — с понедельника. Сигналы здесь, по одному."
    )
    add("23.10", "23:50", f"🏁 <b>Итог недели · 19–23 октября</b>\n\n{week_note}", photo=path)

    add("24.10", "10:00", PROMO_B, {"inline_keyboard": [[{"text": "⚡ Оформить Premium",
                                                         "url": PREMIUM_URL}]]})

    msgs.sort(key=lambda m: m["key"])
    return msgs


def main() -> None:
    msgs = week_messages()
    print(f"сообщений в ленте: {len(msgs)}\n" + "=" * 60)
    for i, m in enumerate(msgs, 1):
        kind = "· фото" if m.get("photo") else "· текст"
        print(f"[{i:>2}] {m['key']:%d.%m %H:%M} {kind}")
        print(m["text"])
        print("-" * 60)

    if not SEND:
        print("\n(dry-run: ничего не отправлено)")
        return

    print("\nотправляю...")
    ok_n = 0
    for i, m in enumerate(msgs, 1):
        if m.get("photo"):
            ok, info = tg.send_photo(m["photo"], m["text"], markup=m["markup"])
        else:
            ok, info = tg.send_message(m["text"], markup=m["markup"])
        print(f"[{i:>2}/{len(msgs)}] {ok} {info if not ok else ''}")
        if ok:
            ok_n += 1
        time.sleep(DELAY)
    print(f"готово: {ok_n} из {len(msgs)}")


if __name__ == "__main__":
    main()
