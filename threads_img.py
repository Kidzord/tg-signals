"""Картинки к постам для Threads (RU) и X (EN).

Рисуем вертикальные карточки 1080×1350 в палитре индикатора (theme.py):
плоский фон, золотой акцент, DejaVu Sans. У части постов — крупная цифра
или схематичный график (диапазон, снятие ликвидности, сессии, EMA200, серия стопов).

Язык надписей задаётся параметром lang («ru» — Threads/VK/Дзен, «en» — X).

Запуск:
    python threads_img.py          # все карточки Threads (RU) в threads_img/
    python threads_img.py 5 26     # только выбранные номера
    python threads_img.py en       # англ. карточки для X в x_img/
    python threads_img.py en 5 26  # только выбранные номера (англ.)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import theme
from theme import Card

HERE = Path(__file__).resolve().parent
OUT = HERE / "threads_img"

W_IN, H_IN = 10.8, 13.5          # 4:5, как принято в Threads и ленте X
BRAND = "@KidzordFX"

# Надписи на карточке по языкам: шапка-подпись, футер и подписи схем.
LANGS: dict[str, dict[str, str]] = {
    "ru": {
        "tagline": "ЗОЛОТО · ФОРЕКС · ИНДЕКСЫ",
        "footer": "не является инвестиционной рекомендацией",
        "hi": "хай", "lo": "лоу", "edges": "торгуем края",
        "swept": "сняли стопы", "level": "уровень",
        "back": "возврат к зоне", "london": "ЛОНДОН", "ny": "НЬЮ-ЙОРК",
        "quiet": "вне сессий — тишина",
    },
    "en": {
        "tagline": "GOLD · FOREX · INDICES",
        "footer": "not investment advice",
        "hi": "high", "lo": "low", "edges": "trade the edges",
        "swept": "stops swept", "level": "level",
        "back": "back to the zone", "london": "LONDON", "ny": "NEW YORK",
        "quiet": "quiet outside sessions",
    },
}

# Крупная цифра и/или схема-график для части постов (номер поста → декор).
SPECS: dict[str, dict[int, dict]] = {
    "ru": {
        1: {"metric": "+2R", "mlabel": "неделя: 5 целей / 13 стопов"},
        3: {"chart": "range"},
        4: {"metric": "0.5%", "mlabel": "риск на сделку"},
        5: {"chart": "sweep"},
        8: {"metric": "5 000 → 6 050 $", "mlabel": "+21% с начала ведения счёта"},
        11: {"metric": "3", "mlabel": "стопа в день — и день закрыт"},
        12: {"metric": "3R", "mlabel": "цель при стопе в 1R"},
        13: {"metric": "0.5 · 3 · 10%", "mlabel": "риск · дневной · общий лимиты"},
        20: {"metric": "R", "mlabel": "один риск = один R"},
        22: {"chart": "sessions"},
        24: {"metric": "+38R", "mlabel": "за 30 дней при риске 0.5%"},
        26: {"chart": "ema"},
        28: {"chart": "stops"},
    },
    "en": {
        1: {"metric": "+2R", "mlabel": "week: 5 wins / 13 stops"},
        3: {"chart": "range"},
        4: {"metric": "0.5%", "mlabel": "risk per trade"},
        5: {"chart": "sweep"},
        8: {"metric": "$5,000 → $6,050", "mlabel": "+21% since the record began"},
        11: {"metric": "3", "mlabel": "stops a day — then the day is done"},
        12: {"metric": "3R", "mlabel": "target with a 1R stop"},
        13: {"metric": "0.5 · 3 · 10%", "mlabel": "risk · daily · overall limits"},
        20: {"metric": "R", "mlabel": "one risk equals one R"},
        22: {"chart": "sessions"},
        24: {"metric": "+38R", "mlabel": "over 30 days at 0.5% risk"},
        26: {"chart": "ema"},
        28: {"chart": "stops"},
    },
}


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if len(cand) <= width:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _hook_size(s: str) -> tuple[int, int]:
    """Размер шрифта заголовка и ширина переноса — по длине строки."""
    n = len(s)
    if n <= 20:
        return 50, 18
    if n <= 30:
        return 42, 22
    if n <= 42:
        return 35, 26
    return 29, 30


def _chart(ax, kind: str, L: dict[str, str]) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    g, a = theme.GRID, theme.ACCENT
    green, red, lbl3 = theme.GREEN, theme.RED, theme.LABEL3

    if kind == "range":
        ax.plot([0.04, 0.96], [0.84, 0.84], color=g, lw=1.2, ls=(0, (4, 3)))
        ax.plot([0.04, 0.96], [0.16, 0.16], color=g, lw=1.2, ls=(0, (4, 3)))
        xs = [0.04, 0.13, 0.22, 0.31, 0.40, 0.49, 0.58, 0.67, 0.76, 0.85, 0.96]
        ys = [0.50, 0.74, 0.28, 0.68, 0.34, 0.76, 0.40, 0.62, 0.26, 0.72, 0.46]
        ax.plot(xs, ys, color=a, lw=2.4)
        ax.text(0.96, 0.90, L["hi"], color=lbl3, fontsize=11, ha="right", va="bottom")
        ax.text(0.96, 0.10, L["lo"], color=lbl3, fontsize=11, ha="right", va="top")
        ax.text(0.05, 0.50, L["edges"], color=lbl3, fontsize=12, va="center")

    elif kind == "sweep":
        ax.plot([0.04, 0.96], [0.36, 0.36], color=g, lw=1.2, ls=(0, (4, 3)))
        xs = [0.04, 0.16, 0.28, 0.38, 0.45, 0.52, 0.64, 0.76, 0.88, 0.96]
        ys = [0.62, 0.50, 0.44, 0.30, 0.10, 0.52, 0.66, 0.60, 0.74, 0.70]
        ax.plot(xs, ys, color=a, lw=2.4)
        ax.plot([0.45], [0.10], marker="o", ms=7, color=red)
        ax.text(0.45, 0.06, L["swept"], color=lbl3, fontsize=12, ha="center", va="top")
        ax.text(0.96, 0.36, L["level"], color=lbl3, fontsize=11, ha="right", va="bottom")

    elif kind == "ema":
        ax.plot([0.04, 0.96], [0.52, 0.52], color=g, lw=1.4, ls=(0, (5, 4)))
        xs = [0.04, 0.18, 0.32, 0.44, 0.52, 0.60, 0.72, 0.84, 0.96]
        ys = [0.72, 0.64, 0.55, 0.53, 0.50, 0.62, 0.70, 0.78, 0.86]
        ax.plot(xs, ys, color=a, lw=2.4)
        ax.plot([0.52], [0.50], marker="o", ms=7, color=green)
        ax.text(0.52, 0.44, L["back"], color=lbl3, fontsize=12, ha="center", va="top")
        ax.text(0.96, 0.56, "EMA200 4H", color=lbl3, fontsize=11, ha="right", va="bottom")

    elif kind == "sessions":
        ax.axvspan(0.06, 0.40, color=theme.rgba(g, 0.55))
        ax.axvspan(0.56, 0.92, color=theme.rgba(g, 0.55))
        xs = [0.06, 0.16, 0.26, 0.36, 0.46, 0.56, 0.66, 0.76, 0.86, 0.94]
        ys = [0.55, 0.40, 0.62, 0.48, 0.66, 0.45, 0.70, 0.58, 0.76, 0.62]
        ax.plot(xs, ys, color=a, lw=2.4)
        ax.text(0.23, 0.86, L["london"], color=lbl3, fontsize=12, ha="center")
        ax.text(0.74, 0.86, L["ny"], color=lbl3, fontsize=12, ha="center")
        ax.text(0.50, 0.12, L["quiet"], color=lbl3, fontsize=12, ha="center")

    elif kind == "stops":
        for x in (0.20, 0.36, 0.52):
            ax.plot([x, x], [0.42, 0.20], color=red, lw=8, solid_capstyle="round")
        ax.plot([0.72, 0.72], [0.30, 0.86], color=green, lw=8, solid_capstyle="round")
        ax.text(0.36, 0.10, "−1R  −1R  −1R", color=lbl3, fontsize=12, ha="center")
        ax.text(0.72, 0.92, "+3R", color=green, fontsize=14, ha="center", va="bottom")


def render(idx: int, post: dict, lang: str = "ru", out_dir: Path | None = None) -> str:
    """Отрисовать карточку поста. lang: «ru» (Threads/VK/Дзен) или «en» (X)."""
    lang = lang if lang in LANGS else "ru"
    L = LANGS[lang]
    out = Path(out_dir) if out_dir else OUT
    out.mkdir(parents=True, exist_ok=True)
    spec = SPECS[lang].get(idx, {})
    c = Card(W_IN, H_IN)

    # шапка
    c.text(0.055, 0.955, theme.spaced("KIDZORDFX"), size=17, weight="bold",
           color=theme.ACCENT, va="center")
    c.text(0.945, 0.955, L["tagline"], size=12, color=theme.LABEL3, ha="right", va="center")
    c.hairline(0.055, 0.945, 0.925)

    # ---- содержимое
    hook = post["text"].split("\n")[0]
    rest = [ln for ln in post["text"].split("\n")[1:] if ln.strip()][:3]

    hsize, hwidth = _hook_size(hook)
    hook_lines = _wrap(hook, hwidth)
    body_lines: list[str] = []
    for ln in rest:
        body_lines += _wrap(ln, 46)

    metric, chart = spec.get("metric"), spec.get("chart")

    # высота блока текста в долях оси Y (1 pt ≈ 0.00136)
    LH = 0.00136
    block_h = len(hook_lines) * hsize * LH
    block_h += 0.030 if body_lines else 0.0
    block_h += len(body_lines) * 21 * LH
    block_h += max(0, len(rest) - 1) * 0.012

    bottom = 0.42 if chart else (0.46 if metric else 0.15)
    y = 0.885 - max(0.0, (0.885 - bottom - block_h)) / 2

    # золотая черта слева от заголовка — по высоте всего заголовка
    yline = y
    for _ in hook_lines:
        yline -= hsize * LH
    c.box(0.055, yline + 0.004, 0.006, (y - yline) + 0.016,
          c.r_in(0.03), fc=theme.ACCENT, z=4)

    tx = 0.085
    for ln in hook_lines:
        c.text(tx, y, ln, size=hsize, weight="bold", color=theme.LABEL, va="center")
        y -= hsize * LH
    y -= 0.030

    for ln in rest:
        for part in _wrap(ln, 46):
            c.text(tx, y, part, size=21, color=theme.LABEL2, va="center")
            y -= 21 * LH
        y -= 0.012

    # крупная цифра
    if metric:
        c.text(0.055, 0.300, metric, size=74 if len(metric) <= 6 else 50,
               weight="bold", color=theme.ACCENT, va="center")
        c.text(0.055, 0.205, spec.get("mlabel", ""), size=19,
               color=theme.LABEL3, va="center")

    # схема-график
    if chart:
        c.hairline(0.055, 0.945, 0.34)
        ax = c.fig.add_axes([0.06, 0.145, 0.88, 0.185])
        _chart(ax, chart, L)

    # низ
    c.hairline(0.055, 0.945, 0.105)
    c.text(0.055, 0.075, BRAND, size=17, weight="bold", color=theme.ACCENT, va="center")
    c.text(0.945, 0.075, L["footer"], size=11, color=theme.LABEL3, ha="right", va="center")

    path = out / f"{idx:02d}.png"
    return c.save(str(path))


def main() -> None:
    """python threads_img.py [ru|en] [номера...]  (по умолчанию ru — Threads)."""
    args = list(sys.argv[1:])
    lang = "ru"
    if args and args[0].lower() in LANGS:
        lang = args.pop(0).lower()

    if lang == "en":
        import x_post
        posts, out = x_post.POSTS, HERE / "x_img"
    else:
        import threads_post
        posts, out = threads_post.POSTS, OUT

    want = [int(a) for a in args if a.isdigit()]
    targets = want or list(range(1, len(posts) + 1))
    for i in targets:
        if 1 <= i <= len(posts):
            p = render(i, posts[i - 1], lang=lang, out_dir=out)
            print(f"[{i:>2}] {p}")


if __name__ == "__main__":
    main()
