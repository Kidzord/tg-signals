"""Отрисовка учебных карточек 1080×1350 для TikTok-карусели.

Каждый урок — обложка + 4–5 слайдов. Текст короткий, как в карусели:
одна мысль на слайд. Схемы простые: свечи, уровни, стрелки, зоны.
Это оригинальные объяснения публичных концепций SMC/ICT и MSNR,
а не пересказ платных курсов.
"""
from __future__ import annotations

import os
import textwrap

import theme
from matplotlib.patches import Rectangle

W_IN, H_IN = 10.8, 13.5


def _wrap(text: str, width: int) -> list[str]:
    return textwrap.wrap(text, width=width)


def _header(c: theme.Card, n: int, total: int, label: str = "SMART MONEY · MSNR") -> None:
    c.text(0.06, 0.945, "@KIDZORDFX", size=12, weight="bold",
           display=True, color=theme.ACCENT)
    c.text(0.06, 0.905, theme.spaced(label), size=8, color=theme.LABEL3)
    c.text(0.94, 0.945, f"{n}/{total}", size=12, color=theme.LABEL3, ha="right")
    c.hairline(0.06, 0.94, 0.875)


def _footer(c: theme.Card) -> None:
    c.hairline(0.06, 0.94, 0.105)
    c.text(0.06, 0.068, "Полный журнал — t.me/KidzordFX",
           size=9.5, color=theme.ACCENT)
    c.text(0.94, 0.068, "не инвестрекомендация", size=8,
           color=theme.LABEL3, ha="right")


def _body(c: theme.Card, text: str, y: float = 0.745, size: float = 18,
          width: int = 28, color: str = theme.LABEL2) -> float:
    lines = _wrap(text, width)[:10]
    for line in lines:
        c.text(0.06, y, line, size=size, color=color, va="top")
        y -= 0.052
    return y


def _candle(ax, x: float, o: float, h: float, l: float, cl: float,
            color: str, width: float = 0.055) -> None:
    ax.add_patch(Rectangle((x - width / 2, min(o, cl)), width,
                           max(abs(cl - o), 0.006),
                           facecolor=color, edgecolor=color, lw=0))
    ax.plot([x, x], [l, h], color=color, lw=1.2)


def _label(ax, x: float, y: float, text: str, color: str = theme.LABEL2,
           size: float = 10, ha: str = "center") -> None:
    ax.text(x, y, text, color=color, fontsize=size,
            fontproperties=theme.font("semibold"), ha=ha, va="center")


def _diagram(ax, kind: str) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_facecolor("none")
    ax.patch.set_alpha(0.0)

    if kind == "liquidity":
        ax.add_patch(Rectangle((0.08, 0.36), 0.84, 0.28, facecolor=theme.PANEL,
                               edgecolor=theme.GRID, lw=1))
        ax.plot([0.08, 0.92], [0.36, 0.36], color=theme.RED, lw=2, ls="--")
        ax.plot([0.08, 0.92], [0.64, 0.64], color=theme.GREEN, lw=2, ls="--")
        ax.plot([0.38, 0.38], [0.36, 0.22], color=theme.RED, lw=2)
        ax.annotate("", xy=(0.47, 0.52), xytext=(0.38, 0.24),
                    arrowprops=dict(arrowstyle="->", color=theme.GREEN, lw=2))
        _label(ax, 0.72, 0.30, "сняли стопы", theme.RED)
        _label(ax, 0.55, 0.70, "возврат", theme.GREEN)
        _label(ax, 0.18, 0.40, "поддержка", theme.LABEL3, 9, "left")

    elif kind == "msnr":
        ax.plot([0.08, 0.92], [0.52, 0.52], color=theme.ACCENT, lw=2, ls="--")
        for i, (o, cl) in enumerate(((0.62, 0.55), (0.55, 0.47),
                                     (0.47, 0.54), (0.54, 0.49), (0.49, 0.60))):
            x = 0.16 + i * 0.14
            _candle(ax, x, o, max(o, cl) + 0.06, min(o, cl) - 0.06, cl,
                    theme.GREEN if cl >= o else theme.RED)
        _label(ax, 0.70, 0.60, "уровень по телу", theme.ACCENT)
        _label(ax, 0.70, 0.40, "тени не считаем", theme.LABEL3, 9)

    elif kind == "structure":
        pts = [(0.08, 0.30), (0.22, 0.48), (0.34, 0.38), (0.48, 0.66),
               (0.60, 0.55), (0.74, 0.80), (0.88, 0.70)]
        ax.plot([p[0] for p in pts], [p[1] for p in pts],
                color=theme.ACCENT, lw=2)
        for (x, y), t in zip(pts, ("", "HH", "HL", "HH", "HL", "HH", "HL")):
            if t:
                _label(ax, x, y + 0.05, t, theme.GREEN, 10)
        _label(ax, 0.50, 0.16, "тренд = серия HH и HL", theme.LABEL2, 9)

    elif kind == "bos_choch":
        ax.plot([0.08, 0.25, 0.38, 0.52, 0.66, 0.82], [0.30, 0.50, 0.40, 0.62, 0.52, 0.44],
                color=theme.ACCENT, lw=2)
        ax.plot([0.08, 0.92], [0.62, 0.62], color=theme.GREEN, lw=1.5, ls="--")
        ax.plot([0.08, 0.92], [0.44, 0.44], color=theme.RED, lw=1.5, ls="--")
        _label(ax, 0.78, 0.66, "BOS", theme.GREEN)
        _label(ax, 0.78, 0.38, "CHoCH", theme.RED)
        _label(ax, 0.50, 0.18, "слом в сторону — CHoCH, продолжение — BOS", theme.LABEL2, 8.5)

    elif kind == "ob":
        for i, (o, cl, col) in enumerate((
                (0.60, 0.54, theme.RED), (0.54, 0.46, theme.RED),
                (0.46, 0.58, theme.GREEN), (0.58, 0.64, theme.GREEN),
                (0.64, 0.70, theme.GREEN))):
            x = 0.18 + i * 0.13
            _candle(ax, x, o, max(o, cl) + 0.05, min(o, cl) - 0.05, cl, col)
        ax.add_patch(Rectangle((0.24, 0.43), 0.16, 0.08, facecolor=theme.ACCENT,
                               alpha=0.22, edgecolor=theme.ACCENT, lw=1))
        _label(ax, 0.58, 0.36, "order block: последняя свеча перед импульсом", theme.LABEL2, 8.5)

    elif kind == "fvg":
        for i, (o, cl, col) in enumerate(((0.46, 0.52, theme.GREEN),
                                          (0.56, 0.72, theme.GREEN),
                                          (0.64, 0.70, theme.GREEN))):
            x = 0.22 + i * 0.16
            _candle(ax, x, o, max(o, cl) + 0.04, min(o, cl) - 0.04, cl, col)
        ax.add_patch(Rectangle((0.16, 0.52), 0.56, 0.04, facecolor=theme.ACCENT,
                               alpha=0.22, edgecolor=theme.ACCENT, lw=1))
        _label(ax, 0.62, 0.44, "FVG: гэп между свечами", theme.ACCENT, 9)

    elif kind == "premium":
        ax.add_patch(Rectangle((0.10, 0.25), 0.80, 0.50, facecolor=theme.PANEL,
                               edgecolor=theme.GRID, lw=1))
        ax.plot([0.10, 0.90], [0.50, 0.50], color=theme.ACCENT, lw=2, ls="--")
        _label(ax, 0.50, 0.64, "premium — продаём", theme.RED, 10)
        _label(ax, 0.50, 0.36, "discount — покупаем", theme.GREEN, 10)
        _label(ax, 0.50, 0.50, "50% диапазона", theme.ACCENT, 9)

    elif kind == "entry":
        ax.plot([0.08, 0.92], [0.40, 0.40], color=theme.RED, lw=1.5, ls="--")
        ax.plot([0.38, 0.38], [0.40, 0.24], color=theme.RED, lw=2)
        ax.plot([0.38, 0.48], [0.24, 0.44], color=theme.GREEN, lw=2)
        ax.plot([0.48, 0.56], [0.44, 0.52], color=theme.GREEN, lw=2)
        ax.plot([0.08, 0.92], [0.58, 0.58], color=theme.GREEN, lw=1.5, ls="--")
        ax.annotate("", xy=(0.60, 0.55), xytext=(0.56, 0.46),
                    arrowprops=dict(arrowstyle="->", color=theme.ACCENT, lw=2))
        _label(ax, 0.70, 0.34, "вход", theme.ACCENT)
        _label(ax, 0.70, 0.62, "цель", theme.GREEN)
        _label(ax, 0.18, 0.28, "стоп за свип", theme.RED, 9, "left")

    elif kind == "risk":
        for y, t, col in ((0.66, "TP2  +3R", theme.GREEN), (0.54, "TP1  +1.5R", theme.GREEN),
                          (0.42, "Вход", theme.ACCENT), (0.28, "Стоп  −1R", theme.RED)):
            ax.plot([0.10, 0.90], [y, y], color=col, lw=2, ls="--")
            _label(ax, 0.14, y + 0.035, t, col, 9, "left")
        _label(ax, 0.50, 0.14, "риск 1R → цель 1.5–3R", theme.LABEL2, 9)

    elif kind == "journal":
        rows = (("Вход по правилам?", theme.GREEN),
                ("Стоп стоял где надо?", theme.GREEN),
                ("Что сделал бы иначе?", theme.ACCENT))
        for i, (t, col) in enumerate(rows):
            y = 0.66 - i * 0.16
            ax.add_patch(Rectangle((0.10, y - 0.05), 0.80, 0.10,
                                   facecolor=theme.PANEL, edgecolor=theme.GRID, lw=1))
            _label(ax, 0.14, y, "☐", col, 14, "left")
            _label(ax, 0.22, y, t, theme.LABEL2, 10, "left")


def _slide(lesson: dict, idx: int, total: int, slide: dict, out_path: str) -> str:
    c = theme.Card(W_IN, H_IN)
    _header(c, idx, total)
    y = 0.80
    for line in _wrap(slide.get("title", ""), 18)[:3]:
        c.text(0.06, y, line, size=28, weight="bold", display=True)
        y -= 0.065
    y -= 0.025
    y = _body(c, slide.get("text", ""), y=y, size=18, width=28)
    kind = slide.get("diagram")
    if kind:
        # Схема начинается под последней строкой текста, иначе длинный текст
        # наезжает на верхнюю линию диаграммы.
        bottom = 0.145
        top = max(bottom + 0.17, min(0.50, y - 0.005))
        ax = c.fig.add_axes([0.06, bottom, 0.88, top - bottom])
        _diagram(ax, kind)
    _footer(c)
    c.save(out_path)
    return out_path


def _cover(lesson: dict, idx: int, total: int, out_path: str) -> str:
    c = theme.Card(W_IN, H_IN)
    c.text(0.06, 0.945, "@KIDZORDFX", size=12, weight="bold",
           display=True, color=theme.ACCENT)
    c.text(0.06, 0.905, theme.spaced("SMART MONEY · MSNR"), size=8, color=theme.LABEL3)
    c.text(0.94, 0.945, "TIKTOK · УРОК", size=9, color=theme.LABEL3, ha="right")
    c.hairline(0.06, 0.94, 0.875)

    c.text(0.06, 0.80, f"УРОК {idx:02d}", size=16, color=theme.ACCENT)
    y = 0.72
    for line in _wrap(lesson["title"], 16)[:4]:
        c.text(0.06, y, line, size=34, weight="bold", display=True)
        y -= 0.075
    c.text(0.06, y - 0.02, f"{total} слайдов · сохрани и листай", size=12,
           color=theme.LABEL3)
    c.text(0.06, 0.14, "Полный журнал и сигналы — t.me/KidzordFX",
           size=11, color=theme.ACCENT)
    c.text(0.06, 0.09, "не является инвестиционной рекомендацией", size=8,
           color=theme.LABEL3)
    c.save(out_path)
    return out_path


def render_lesson(lesson: dict, index: int, out_dir: str) -> list[str]:
    """Нарисовать обложку и слайды. Возвращает список JPEG-путей."""
    os.makedirs(out_dir, exist_ok=True)
    slides = lesson.get("slides", [])
    total = len(slides) + 1
    paths = [_cover(lesson, index, total, os.path.join(out_dir, "00_cover.jpg"))]
    for i, slide in enumerate(slides, 1):
        paths.append(_slide(lesson, i + 1, total, slide,
                            os.path.join(out_dir, f"{i:02d}_slide.jpg")))
    return paths
