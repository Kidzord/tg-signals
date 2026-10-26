"""Тексты постов: живая подача, но все цифры на месте.

Вариативность детерминированная (по времени бара), чтобы один и тот же сигнал
не менял текст при переотправке.
"""
from __future__ import annotations

import hashlib

import config as cfg
import feeds
import render as card


def _pick(options: list[str], seed: str) -> str:
    h = int(hashlib.md5(seed.encode()).hexdigest()[:8], 16)
    return options[h % len(options)]


INTRO_LONG = [
    "Сработала зона на {sym} — смотрю вверх.",
    "{sym}: отбой от уровня, вход в лонг.",
    "По {sym} дали реакцию от поддержки — беру лонг.",
    "{sym} вернулся к зоне и оттолкнулся. Лонг.",
    "На {sym} сняли низ и выкупили — лонг от зоны.",
    "{sym}: цена пришла к уровню, отскок. Работаю в лонг.",
]

INTRO_SHORT = [
    "Сработала зона на {sym} — смотрю вниз.",
    "{sym}: отбой от уровня, вход в шорт.",
    "По {sym} дали реакцию от сопротивления — беру шорт.",
    "{sym} вернулся к зоне и оттолкнулся. Шорт.",
    "На {sym} сняли хай и продавили — шорт от зоны.",
    "{sym}: цена пришла к уровню, отскок. Работаю в шорт.",
]

CLOSE = [
    "Держу по плану, стоп не двигаю.",
    "Стоп за зоной, цель на противоположной границе.",
    "Работаю до цели, риск фиксированный.",
]


def signal_caption(sig: card.Signal, *, zone: float | None = None,
                   regime: str | None = None, source: str = "MT5") -> str:
    seed = f"{sig.symbol}{sig.when:%Y%m%d%H%M}{sig.side}"
    intro = _pick(INTRO_LONG if sig.is_long else INTRO_SHORT, seed).format(sym=sig.symbol)
    close = _pick(CLOSE, seed + "c")

    last_tp = sig.tp3 if sig.tp3 is not None else (sig.tp2 if sig.tp2 is not None else sig.tp1)
    lines = [intro, ""]

    arrow = "↑" if sig.is_long else "↓"
    lines.append(f"{arrow} вход <b>{card.money(sig.entry)}</b>")
    lines.append(f"стоп {card.money(sig.sl)} — {sig.risk_pct:.2f}% от входа")

    targets = [(n, px) for n, px in ((1, sig.tp1), (2, sig.tp2), (3, sig.tp3)) if px is not None]
    for n, px in targets:
        label = "цель" if len(targets) == 1 else f"цель {n}"
        lines.append(f"{label}: {card.money(px)}  ({sig.r_multiple(px):.1f}R)")

    if zone is not None:
        lines.append(f"зона: {card.money(zone)}")
    if regime:
        lines.append(f"режим: {regime}")

    lines += ["", close, ""]
    lines.append(f"{feeds.tf_label(sig.tf)} · {sig.when:%d.%m %H:%M} {cfg.TZ_LABEL} · {source}")
    lines.append(f"<i>{cfg.DISCLAIMER}</i>")
    return "\n".join(lines)


EVENT = {
    "tp1": [
        "Первая цель взята: {r}. Остаток перевёл в безубыток.",
        "TP1 есть, {r}. Дальше веду без риска.",
        "Забрал первую часть, {r}. Стоп в безубытке.",
    ],
    "tp2": [
        "Вторая цель взята, {r}. Остаток держу.",
        "TP2 закрыт, {r}. Осталась третья.",
        "Ещё одна цель, {r}. Иду дальше.",
    ],
    "tp3": [
        "Дошли до конечной цели, {r}. Сделка закрыта.",
        "TP3, {r}. Полный выход.",
        "Цель отработана полностью, {r}.",
    ],
    "tp": [
        "Цель взята, {r}. Фиксирую полностью.",
        "Дошли до цели, {r}. Сделка закрыта.",
        "Цель отработана, {r}. Выхожу целиком.",
    ],
    "sl": [
        "Стоп. {r}. Отработал по правилам, идём дальше.",
        "Выбило по стопу, {r}. Без эмоций, это часть системы.",
        "Стоп сработал, {r}. Следующий сигнал по плану.",
        "Минус, {r}. Ничего не поделаешь — риск был ограничен.",
    ],
    "be": [
        "Закрыло в безубытке, {r}. Цель не дошли.",
        "Вышло ровно, {r}. Сделка в ноль.",
    ],
    "time": [
        "Закрыл по времени, {r}. Идея выдохлась.",
        "Снял сделку по таймауту, {r}.",
    ],
}


# Эмодзи исхода: подсказка глазу, что произошло, ещё до чтения текста.
KIND_EMOJI = {
    "tp1": "🎯", "tp2": "🎯", "tp3": "🏁", "tp": "✅",
    "sl": "🛑", "be": "➖", "time": "⏱",
}


def event_text(kind: str, symbol: str, side: str, r: float, note: str = "") -> str:
    opts = EVENT.get(kind) or EVENT["sl"]
    line = _pick(opts, f"{symbol}{kind}{r:.2f}")
    rtxt = f"{r:+.2f}R" if abs(r) > 0.001 else "0R"
    txt = line.format(r=rtxt)
    if note:
        txt += " " + note[0].upper() + note[1:] + "."
    emoji = KIND_EMOJI.get(kind, "•")
    return f"{emoji} <b>{symbol}</b> · {'лонг' if side == 'LONG' else 'шорт'}\n{txt}"
