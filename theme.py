"""Палитра и примитивы отрисовки карточек.

Тёмная тема в палитре индикатора: плоские панели, золотой акцент, системный
шрифт DejaVu Sans. Без градиентов, размытия и «стекла».

Используется в render.py (сигнал, счёт) и brand.py (карточки канала и бота).
"""
from __future__ import annotations

import math

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Arc, Ellipse, FancyBboxPatch, Polygon

plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["axes.unicode_minus"] = False

# ---------------------------------------------------------------- шрифт

_WEIGHTS = {"regular": "normal", "medium": "normal", "semibold": "bold", "bold": "bold"}

_font_cache: dict[str, FontProperties] = {}


def font(weight: str = "regular", display: bool = False) -> FontProperties:
    """Шрифт нужного начертания. DejaVu Sans: regular и bold."""
    key = _WEIGHTS.get(weight, "normal")
    if key not in _font_cache:
        _font_cache[key] = FontProperties(family="DejaVu Sans", weight=key)
    return _font_cache[key]


def font_ready() -> bool:
    """Системный шрифт всегда доступен."""
    return True


def spaced(s: str) -> str:
    """Разрядка для мелких капс-подписей."""
    return " ".join(s)


# ---------------------------------------------------------------- палитра (тёмная, палитра индикатора)

BASE = "#0E1116"          # подложка
PANEL = "#14171C"         # панель
GRID = "#232830"          # сетка и разделители
ACCENT = "#C9A66B"        # золотой акцент

LABEL = "#E4E6E9"         # основной текст
LABEL2 = "#B9C0C8"        # второстепенный
LABEL3 = "#8D949E"        # третичный

GREEN = "#57A08B"         # рост
RED = "#B25B49"           # падение
ORANGE = "#C98A4B"
YELLOW = "#C7A54A"
PURPLE = "#8E7CC3"
PINK = "#B25B49"
TEAL = "#57A08B"
INDIGO = "#6C8CA8"

# Плоская панель: заливка, кромка и разделители внутри неё.
GLASS_FILL = PANEL
GLASS_EDGE = GRID
GLASS_LINE = GRID

# Оставлено для совместимости: подложка теперь сплошная, свечений нет.
BG_BRAND: list = []
BG_CHART: list = []


def rgba(hex_color: str, alpha: float) -> tuple[float, float, float, float]:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return (r, g, b, alpha)


# ---------------------------------------------------------------- карточка


class Card:
    """Полотно карточки: подложка, плоские панели, текст, элементы.

    Координаты — доли полотна (0..1), начало отсчёта внизу слева.
    """

    def __init__(self, w_in: float = 12.8, h_in: float = 7.2, blobs=None):
        self.w_in, self.h_in = w_in, h_in
        self.aspect = w_in / h_in     # компенсация скруглений при разном масштабе осей
        self.fig = plt.figure(figsize=(w_in, h_in), dpi=100, facecolor=BASE)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, 1)
        self.ax.set_ylim(0, 1)
        self.ax.set_autoscale_on(False)
        self.ax.axis("off")
        self.ax.set_facecolor(BASE)

    # ---- геометрия

    def r_in(self, inches: float) -> float:
        """Радиус скругления, заданный в дюймах, в долях оси X."""
        return inches / self.w_in

    def box(self, x, y, w, h, rad, *, fc, ec="none", lw=0.0, z=2, alpha=None):
        # alpha передаём только если задан явно: иначе matplotlib перебивает
        # альфу из RGBA-цвета.
        patch = FancyBboxPatch(
            (x, y), w, h,
            boxstyle=f"round,pad=0,rounding_size={rad}",
            mutation_aspect=self.aspect,
            facecolor=fc, edgecolor=ec, lw=lw, zorder=z,
        )
        if alpha is not None:
            patch.set_alpha(alpha)
        self.ax.add_patch(patch)
        return patch

    def glass(self, x, y, w, h, *, rad=None, z=2, fill=GLASS_FILL, edge=GLASS_EDGE,
              shadow=True, lw=1.0):
        """Плоская панель: заливка PANEL и кромка цвета сетки."""
        rad = self.r_in(0.30) if rad is None else rad
        return self.box(x, y, w, h, rad, fc=fill, ec=edge, lw=lw, z=z)

    def hairline(self, x0, x1, y, *, color=GLASS_LINE, lw=1.0, z=4):
        self.ax.plot([x0, x1], [y, y], color=color, lw=lw, zorder=z,
                     solid_capstyle="round")

    # ---- элементы

    def pill(self, x, y, w, h, text, *, fill=ACCENT, fg=BASE, size=14.0,
             weight="semibold", z=6, display=False):
        self.box(x, y, w, h, h / (2 * self.aspect), fc=fill, z=z)
        self.text(x + w / 2, y + h / 2, text, size=size, weight=weight, color=fg,
                  ha="center", z=z + 1, display=display)

    def chip(self, x, y, w, h, text, *, color=GREEN, size=12.0, z=6):
        """Плашка-статус: приглушённая подложка и цветной текст."""
        self.box(x, y, w, h, h / (2 * self.aspect), fc=rgba(color, 0.18), z=z)
        self.text(x + w / 2, y + h / 2, text, size=size, weight="semibold",
                  color=color, ha="center", z=z + 1)

    def track(self, x, y, w, h, frac, *, color=ACCENT, z=5):
        """Полоса заполнения с круглыми концами."""
        rad = h / (2 * self.aspect)
        self.box(x, y, w, h, rad, fc=GRID, z=z)
        if frac > 0:
            fw = min(w, max(w * min(1.0, max(0.0, frac)), h * 1.35))
            self.box(x, y, fw, h, rad, fc=color, z=z + 1)

    def tile(self, x, y, side, glyph, *, color=ACCENT, z=6):
        """Плитка-иконка: тонированный квадрат со скруглением и глифом."""
        h = side * self.aspect
        self.box(x, y, side, h, side * 0.30, fc=rgba(color, 0.20), z=z)
        self._glyph(glyph, x + side / 2, y + h / 2, side * 0.30, color, z + 1)

    def _glyph(self, kind, cx, cy, s, color, z):
        ky = self.aspect          # вертикальный масштаб: в долях оси Y фигура «выше»
        if kind == "bolt":
            pts = [(0.20, 1.0), (-0.60, 0.05), (-0.05, 0.05), (-0.20, -1.0),
                   (0.60, -0.05), (0.05, -0.05)]
            self.ax.add_patch(Polygon([(cx + px * s, cy + py * s * ky) for px, py in pts],
                                      closed=True, facecolor=color, edgecolor="none", zorder=z))
        elif kind == "bars":
            for i, hh in enumerate((0.55, 1.05, 1.55)):
                self.box(cx + (i - 1) * s * 0.72 - s * 0.20, cy - s * 0.80 * ky,
                         s * 0.40, hh * s * 0.80 * ky, s * 0.18, fc=color, z=z)
        elif kind == "shield":
            pts = [(-0.85, 0.85), (0.85, 0.85), (0.85, -0.15), (0.0, -1.0), (-0.85, -0.15)]
            self.ax.add_patch(Polygon([(cx + px * s, cy + py * s * ky) for px, py in pts],
                                      closed=True, facecolor=color, edgecolor="none", zorder=z))
        elif kind == "lock":
            self.box(cx - s * 0.72, cy - s * 0.85 * ky, s * 1.44, s * 1.10 * ky,
                     s * 0.28, fc=color, z=z)
            self.ax.add_patch(Arc((cx, cy + s * 0.25 * ky), s * 1.05, s * 0.95 * ky,
                                  theta1=0, theta2=180, color=color,
                                  lw=max(s * 130, 1.4), zorder=z))
        elif kind == "star":
            pts = []
            for i in range(10):
                ang = -math.pi / 2 + i * math.pi / 5
                rr = 1.0 if i % 2 == 0 else 0.44
                pts.append((cx + math.cos(ang) * rr * s, cy + math.sin(ang) * rr * s * ky))
            self.ax.add_patch(Polygon(pts, closed=True, facecolor=color,
                                      edgecolor="none", zorder=z))
        elif kind == "check":
            self.ax.plot([cx - s * 0.75, cx - s * 0.15, cx + s * 0.80],
                         [cy - s * 0.05 * ky, cy + s * 0.55 * ky, cy - s * 0.60 * ky],
                         color=color, lw=max(s * 260, 1.6), zorder=z,
                         solid_capstyle="round")
        elif kind == "dot":
            self.ax.add_patch(Ellipse((cx, cy), s * 1.7, s * 1.7 * ky,
                                      facecolor=color, edgecolor="none", zorder=z))

    # ---- текст

    def text(self, x, y, s, *, size=15.0, weight="regular", color=LABEL,
             ha="left", va="center", z=5, display=False, alpha=None):
        t = self.ax.text(x, y, s, fontproperties=font(weight, display),
                         fontsize=size, color=color, ha=ha, va=va, zorder=z)
        if alpha is not None:
            t.set_alpha(alpha)
        return t

    def save(self, path: str) -> str:
        self.fig.savefig(path, facecolor=BASE, dpi=100)
        plt.close(self.fig)
        return path
