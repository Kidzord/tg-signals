"""Бренд-карточки канала и бота: приветствие, оффер подписки, гейт, обложка,
аватар и шаблоны постов для канала.

Стиль — тот же, что у карточек сигналов: тёмная тема в палитре индикатора
(см. theme.py).
Эмодзи в PNG не рисуем: в шрифте нет цветных эмодзи, вместо них — значки-фигуры.

Запуск для (пере)генерации всех ассетов в папку brand/:
    python brand.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg
import theme

W, H = 12.8, 7.2


def _handle() -> str:
    h = cfg.CHANNEL_HANDLE
    if not h:
        return ""
    return h if h.startswith("@") else "@" + h


def _star_price() -> str:
    return f"{cfg.SUB_PRICE_STARS} Stars / месяц"


# ------------------------------------------------------ общие блоки


def _header(c: theme.Card, subtitle: str, right: str = "") -> None:
    c.text(0.04, 0.945, cfg.CARD_TITLE, size=20, weight="bold", display=True)
    c.text(0.04, 0.893, theme.spaced(subtitle), size=8.5, color=theme.LABEL3)
    if right:
        c.text(0.96, 0.945, right, size=17, weight="bold", display=True, ha="right")
    c.hairline(0.04, 0.96, 0.862)


def _footer(c: theme.Card, left: str = "") -> None:
    c.hairline(0.04, 0.96, 0.128)
    if left:
        c.text(0.04, 0.095, left, size=10.5)
    h = _handle()
    if h:
        c.text(0.96, 0.095, h, size=10, weight="semibold", color=theme.ACCENT, ha="right")
    c.text(0.04, 0.045, cfg.DISCLAIMER, size=8, color=theme.LABEL3)


def _bullet(c: theme.Card, x: float, yc: float, title: str, note: str,
            glyph: str, color: str = theme.ACCENT, side: float = 0.058) -> None:
    """Пункт списка: плитка-иконка, заголовок и пояснение под ним."""
    c.tile(x, yc - side * c.aspect / 2, side, glyph, color=color, z=4)
    tx = x + side + 0.022
    c.text(tx, yc + 0.017, title, size=13.5, weight="semibold")
    if note:
        c.text(tx, yc - 0.021, note, size=10.5, color=theme.LABEL2)


def _dot(c: theme.Card, x: float, yc: float, color: str = theme.ACCENT, side: float = 0.013) -> None:
    c.box(x, yc - side * c.aspect / 2, side, side * c.aspect, side / 2, fc=color, z=4)


def _signal_mock(c: theme.Card, x: float, y: float, w: float, h: float) -> None:
    """Мини-макет уведомления с сигналом — иллюстрация на приветственной карточке."""
    c.glass(x, y, w, h, rad=c.r_in(0.34), z=3)
    c.text(x + 0.026, y + h - 0.048, "XAUUSD", size=15, weight="bold", display=True)
    c.chip(x + w - 0.026 - 0.088, y + h - 0.066, 0.088, 0.037, "LONG",
           color=theme.GREEN, size=9.5)
    c.hairline(x + 0.026, x + w - 0.026, y + h - 0.086)
    rows = [("Вход", "2 348.40", theme.LABEL), ("Стоп", "2 341.10", theme.RED),
            ("Цель 1", "2 355.70", theme.GREEN), ("Цель 2", "2 363.00", theme.GREEN),
            ("Цель 3", "2 370.40", theme.GREEN)]
    for i, (label, val, col) in enumerate(rows):
        ry = y + h - 0.130 - i * 0.058
        c.text(x + 0.026, ry, label, size=10.5, color=theme.LABEL3)
        c.text(x + w - 0.026, ry, val, size=11.5, weight="semibold", color=col, ha="right")


# ------------------------------------------------------ карточки для бота


def render_hero(path: str) -> str:
    """Приветствие: что это и что даёт подписка."""
    c = theme.Card(W, H)
    _header(c, "PREMIUM SIGNALS")
    c.text(0.04, 0.775, "Золото · форекс · индексы", size=33, weight="bold", display=True)
    c.text(0.04, 0.695, "в реальном времени", size=33, weight="bold", display=True,
           color=theme.ACCENT)
    _bullet(c, 0.042, 0.560, "Сигнал приходит сразу",
            "вход, стоп и три цели — без задержки", "bolt")
    _bullet(c, 0.042, 0.452, "Объём — под твой депозит",
            "бот сам считает размер сделки", "bars", theme.PURPLE)
    _bullet(c, 0.042, 0.344, "Лимиты под контролем",
            "дневной и общий стоп не превысишь", "shield", theme.GREEN)
    _signal_mock(c, 0.57, 0.30, 0.39, 0.50)
    c.chip(0.042, 0.195, 0.245, 0.058, _star_price(), color=theme.ACCENT, size=12)
    _footer(c, "Подписка на 30 дней · автопродление · отмена в любой момент")
    return c.save(path)


def render_offer(path: str) -> str:
    """Промо-карточка выгод подписки."""
    c = theme.Card(W, H)
    _header(c, "WHAT YOU GET", "Premium")
    c.text(0.04, 0.800, "Что даёт подписка", size=25, weight="bold", display=True)

    items = [
        ("Сигналы без задержки",
         f"сразу в боте, в группе — через {cfg.FREE_DELAY_SEC // 60} мин", "bolt"),
        ("Размер сделки под тебя", "объём считается по твоему депозиту и риску", "bars"),
        ("Дневной и общий лимит", "видно, сколько риска осталось на день", "shield"),
        ("Карточка счёта", "депозит, риск в деньгах, лимиты — одной картинкой", "star"),
    ]
    base, step = 0.660, 0.126
    for n, (title, note, glyph) in enumerate(items):
        y = base - n * step
        c.glass(0.04, y - 0.054, 0.60, 0.112, rad=c.r_in(0.26), z=2)
        c.tile(0.062, y - 0.022, 0.048, glyph, color=theme.ACCENT, z=5)
        c.text(0.132, y + 0.020, title, size=13.5, weight="semibold")
        c.text(0.132, y - 0.022, note, size=10.5, color=theme.LABEL2)

    c.glass(0.665, 0.29, 0.295, 0.50, rad=c.r_in(0.40), z=2)
    c.text(0.8125, 0.700, "Подписка", size=11, color=theme.LABEL3, ha="center")
    c.text(0.8125, 0.605, f"{cfg.SUB_PRICE_STARS}", size=46, weight="bold",
           display=True, ha="center")
    c.text(0.8125, 0.525, "Stars в месяц", size=14, color=theme.ACCENT, ha="center")
    c.hairline(0.715, 0.910, 0.478)
    c.text(0.8125, 0.425, "30 дней", size=13, ha="center")
    c.text(0.8125, 0.375, "автопродление", size=10, color=theme.LABEL2, ha="center")
    c.text(0.8125, 0.332, "отмена в любой момент", size=10, color=theme.LABEL2, ha="center")
    _footer(c, "Оформление — кнопкой под сообщением")
    return c.save(path)


def render_gate(path: str) -> str:
    """Экран «нужна подписка»."""
    c = theme.Card(W, H)
    _header(c, "ACCESS LOCKED")
    c.glass(0.29, 0.30, 0.42, 0.45, rad=c.r_in(0.50), z=2)
    c.tile(0.5 - 0.040, 0.578, 0.080, "lock", color=theme.ORANGE, z=5)
    c.text(0.5, 0.545, "Доступ по подписке", size=21, weight="bold",
           display=True, ha="center")
    c.text(0.5, 0.472, "Быстрые сигналы в боте и карточка твоего счёта",
           size=12, color=theme.LABEL2, ha="center")
    c.text(0.5, 0.425, "Бесплатные сигналы с задержкой — в группе",
           size=11, color=theme.LABEL3, ha="center")
    c.chip(0.365, 0.335, 0.27, 0.058, _star_price(), color=theme.ACCENT, size=12)
    _footer(c)
    return c.save(path)


# ------------------------------------------------------ ассеты канала


def render_cover(path: str) -> str:
    """Широкая обложка канала."""
    c = theme.Card(16.0, 9.0)
    c.box(0.03, 0.05, 0.94, 0.90, c.r_in(0.50), fc=theme.PANEL,
          ec=theme.GRID, lw=1.2, z=2)
    c.text(0.07, 0.700, cfg.CARD_TITLE, size=42, weight="bold", display=True)
    c.text(0.07, 0.600, "ЗОЛОТО · ФОРЕКС · ИНДЕКСЫ", size=30, weight="bold",
           display=True, color=theme.LABEL2)
    c.box(0.07, 0.545, 0.10, 0.011, c.r_in(0.06), fc=theme.ACCENT, z=6)
    c.text(0.07, 0.450, "Вход · стоп · три цели — в каждом сигнале",
           size=16, color=theme.LABEL2)
    c.text(0.07, 0.370, "Публикуем и плюсы, и стопы", size=16, color=theme.LABEL2)
    h = _handle()
    if h:
        c.text(0.93, 0.130, h, size=20, weight="semibold", color=theme.ACCENT, ha="right")
    return c.save(path)


def render_avatar(path: str) -> str:
    """Квадратный аватар канала."""
    c = theme.Card(10.0, 10.0)
    c.glass(0.14, 0.14, 0.72, 0.72, rad=c.r_in(2.40), z=3)
    c.text(0.5, 0.545, "KFX", size=118, weight="bold", display=True, ha="center")
    c.text(0.5, 0.315, theme.spaced("KIDZORDFX"), size=15, color=theme.LABEL3, ha="center")
    return c.save(path)


def render_post(path: str, *, tag: str, headline: str, lines: list[str],
                metric: str | None = None, accent: str | None = None,
                badge: str | None = None) -> str:
    """Шаблон поста для канала: тег, заголовок, необязательная крупная цифра,
    пункты и необязательная плашка."""
    c = theme.Card(W, H)
    _header(c, tag)
    c.text(0.04, 0.775, headline, size=27, weight="bold", display=True)
    y = 0.635
    if metric:
        c.text(0.04, 0.585, metric, size=44, weight="bold", display=True,
               color=accent or theme.ACCENT)
        y = 0.400
    for i, ln in enumerate(lines):
        ry = y - i * 0.076
        _dot(c, 0.046, ry)
        c.text(0.072, ry, ln, size=14)
    if badge:
        c.chip(0.04, 0.185, 0.245, 0.058, badge, color=theme.ACCENT, size=12)
    _footer(c, "KidzordFX — золото, форекс, индексы")
    return c.save(path)


# ------------------------------------------------------ генератор ассетов

ASSETS = {
    "hero": "hero.png",
    "offer": "offer.png",
    "gate": "gate.png",
    "cover": "cover.png",
    "avatar": "avatar.png",
    "post_promo": "post_promo.png",
    "post_result": "post_result.png",
    "post_lesson": "post_lesson.png",
}


def _build(name: str, path: str) -> None:
    if name == "hero":
        render_hero(path)
    elif name == "offer":
        render_offer(path)
    elif name == "gate":
        render_gate(path)
    elif name == "cover":
        render_cover(path)
    elif name == "avatar":
        render_avatar(path)
    elif name == "post_promo":
        render_post(
            path, tag="PREMIUM",
            headline="Быстрые сигналы без задержки",
            lines=["Сигнал приходит сразу: вход, стоп и три цели",
                   "Бот считает объём под твой депозит",
                   "Дневной и общий лимит — под контролем"],
            badge=_star_price())
    elif name == "post_result":
        # Пример-шаблон итога недели: цифры подставляются под конкретную неделю.
        render_post(
            path, tag="ИТОГ НЕДЕЛИ",
            headline="Неделя закрыта", metric="+6.5R", accent=theme.GREEN,
            lines=["Сделок: 7 · дневной лимит не тронут",
                   "Просадка за неделю: −4%",
                   "Публикуем и плюсы, и стопы"])
    elif name == "post_lesson":
        render_post(
            path, tag="РАЗБОР",
            headline="Почему мы не ловим тренды",
            lines=["Инструмент торгует возврат к уровню, а не пробой",
                   "Во флэте возвраты глубокие — шум не выбивает по стопу",
                   "Фильтр режима: работаем только в боковике"])


def ensure_brand_assets(brand_dir: str, force: bool = False) -> dict[str, str]:
    """Создать недостающие карточки в brand_dir. Возвращает имя -> путь."""
    os.makedirs(brand_dir, exist_ok=True)
    out: dict[str, str] = {}
    for name, fname in ASSETS.items():
        path = os.path.join(brand_dir, fname)
        if force or not os.path.exists(path):
            _build(name, path)
        out[name] = path
    return out


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    made = ensure_brand_assets(os.path.join(here, "brand"), force=True)
    for key, path in made.items():
        print(f"{key:12} -> {path}")
