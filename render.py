"""Рендер карточки сделки и карточки счёта в PNG.

Стиль — тёмная тема в палитре индикатора: плоский фон, золотой акцент,
шрифт DejaVu Sans (см. theme.py).
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

import matplotlib

matplotlib.use("Agg")

import matplotlib.ticker as mticker
from matplotlib.patches import Rectangle

import config as cfg
import feeds
import theme
from feeds import Bar

# Публичные имена палитры: на них завязаны пост-статистика (post_month.py) и
# отчёт по кривой доходности (report.py). Значения — из дизайн-системы.
BG = theme.BASE
PANEL = theme.PANEL
GRID = theme.GRID
GOLD = theme.ACCENT
TEXT = theme.LABEL
SUB = theme.LABEL3
UP = theme.GREEN
DOWN = theme.RED


@dataclass
class Signal:
    side: str            # "LONG" | "SHORT"
    symbol: str
    tf: str
    entry: float
    sl: float
    tp1: float
    tp2: float | None = None
    tp3: float | None = None
    when: dt.datetime = dt.datetime(2000, 1, 1)

    @property
    def is_long(self) -> bool:
        return self.side.upper() == "LONG"

    @property
    def risk(self) -> float:
        return abs(self.entry - self.sl)

    @property
    def risk_pct(self) -> float:
        return self.risk / self.entry * 100 if self.entry else 0.0

    def r_multiple(self, price: float) -> float:
        return abs(price - self.entry) / self.risk if self.risk else 0.0


def money(v: float, digits: int | None = None) -> str:
    """Цена с числом знаков под инструмент: FX — 5, JPY — 3, золото и индексы — 2."""
    if digits is None:
        a = abs(v)
        digits = 5 if a < 10 else (3 if a < 1000 else 2)
    return f"{v:,.{digits}f}".replace(",", " ")


def _tick_font(ax) -> None:
    """Мелкие подписи осей — тем же шрифтом, что и карточка."""
    for lb in list(ax.get_xticklabels()) + list(ax.get_yticklabels()):
        lb.set_fontproperties(theme.font("regular"))
        lb.set_fontsize(8)


def render_card(sig: Signal, bars: list[Bar], source: str, is_demo: bool, out_path: str) -> str:
    n = len(bars)
    c = theme.Card(12.8, 7.2)

    # ---------------- шапка ----------------
    c.text(0.035, 0.945, cfg.CARD_TITLE, size=20, weight="bold", display=True)
    c.text(0.035, 0.893, theme.spaced("SIGNAL CARD"), size=8.5, color=theme.LABEL3)

    c.text(0.965, 0.945, f"{sig.symbol} · {feeds.tf_label(sig.tf)}", size=22,
           weight="bold", display=True, ha="right")

    side_color = theme.GREEN if sig.is_long else theme.RED
    side_word = "LONG" if sig.is_long else "SHORT"
    c.text(0.845, 0.884, "вход по закрытию бара", size=10, color=theme.LABEL2, ha="right")
    c.chip(0.858, 0.8615, 0.107, 0.045, side_word, color=side_color, size=11.5)

    c.hairline(0.035, 0.965, 0.845)

    # ---------------- график ----------------
    ax = c.fig.add_axes([0.045, 0.155, 0.915, 0.665])
    ax.set_facecolor("none")
    ax.patch.set_alpha(0.0)

    # Границы считаем по всем уровням сразу: для шорта TP3 ниже цены, для лонга — выше.
    all_levels = [x for x in (sig.entry, sig.sl, sig.tp1, sig.tp2, sig.tp3) if x is not None]
    lo = min([b.l for b in bars] + all_levels)
    hi = max([b.h for b in bars] + all_levels)
    pad = max((hi - lo) * 0.08, 0.0001)
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-1, n + 7)

    # зоны риска и прибыли
    last_target = sig.tp3 if sig.tp3 is not None else sig.tp1
    if sig.is_long:
        ax.fill_between([-1, n + 7], sig.entry, sig.sl, color=theme.RED, alpha=0.10, lw=0)
        ax.fill_between([-1, n + 7], sig.entry, last_target, color=theme.GREEN, alpha=0.08, lw=0)
    else:
        ax.fill_between([-1, n + 7], sig.sl, sig.entry, color=theme.RED, alpha=0.10, lw=0)
        ax.fill_between([-1, n + 7], last_target, sig.entry, color=theme.GREEN, alpha=0.08, lw=0)

    # сетка
    ax.yaxis.set_major_locator(mticker.MaxNLocator(7))
    ax.grid(axis="y", color=theme.GLASS_LINE, lw=0.7)
    ax.set_axisbelow(True)

    # свечи
    tick = max((hi - lo) / 400, 1e-5)
    for i, b in enumerate(bars):
        color = theme.GREEN if b.c >= b.o else theme.RED
        ax.vlines(i, b.l, b.h, color=color, lw=0.9, zorder=3)
        body_lo, body_hi = sorted((b.o, b.c))
        ax.add_patch(Rectangle(
            (i - 0.31, body_lo), 0.62, max(body_hi - body_lo, tick),
            facecolor=color, edgecolor=color, lw=0, zorder=4))

    # уровни
    levels = [(name, px, col, a) for name, px, col, a in (
        ("TP3", sig.tp3, theme.GREEN, 0.55),
        ("TP2", sig.tp2, theme.GREEN, 0.75),
        ("TP1", sig.tp1, theme.GREEN, 1.0),
        ("SL", sig.sl, theme.RED, 1.0),
        ("Вход", sig.entry, theme.LABEL2, 1.0),
    ) if px is not None]
    for name, price, color, alpha in levels:
        lw = 1.5 if name == "Вход" else 1.1
        ax.axhline(price, color=color, lw=lw, ls=(0, (5, 4)), alpha=alpha, zorder=2)

    # подписи уровней справа
    placed: list[float] = []
    for name, price, color, alpha in sorted(levels, key=lambda x: -x[1]):
        y = price
        # разводим близкие подписи, чтобы не наезжали
        span = (hi - lo) or 1.0
        for prev in placed:
            if abs(y - prev) < span * 0.028:
                y = prev - span * 0.030
        placed.append(y)
        ax.annotate(
            f"{name}  {money(price)}",
            xy=(n + 6.6, y), xytext=(n + 6.6, y),
            color=theme.BASE, fontsize=8.5,
            fontproperties=theme.font("semibold"),
            ha="right", va="center", zorder=6,
            bbox=dict(boxstyle="round,pad=0.34", fc=color, ec="none", alpha=max(alpha, 0.78)),
        )

    # оси
    ax.tick_params(axis="y", colors=theme.LABEL3, length=0)
    ax.tick_params(axis="x", colors=theme.LABEL3, length=0)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(theme.GLASS_LINE)
    _tick_font(ax)

    step = max(1, n // 5)
    ticks = list(range(0, n, step))[:6]
    labels = []
    for i in ticks:
        t = bars[i].t
        labels.append(t.strftime("%d.%m %H:%M") if feeds.tf_minutes(sig.tf) < 1440 else t.strftime("%d.%m"))
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels)
    _tick_font(ax)

    if is_demo:
        ax.text(n / 2, (hi + lo) / 2, "ДЕМО", color="#FFFFFF", alpha=0.07,
                fontproperties=theme.font("bold", display=True),
                fontsize=88, ha="center", va="center", rotation=12, zorder=1)

    # ---------------- подвал ----------------
    c.hairline(0.035, 0.965, 0.128)

    last_tp = sig.tp3 if sig.tp3 is not None else (sig.tp2 if sig.tp2 is not None else sig.tp1)
    left = (f"Вход {money(sig.entry)}   ·   Стоп {money(sig.sl)}   ·   "
            f"Риск {sig.risk_pct:.2f}%   ·   R:R {sig.r_multiple(last_tp):.1f}")
    c.text(0.035, 0.095, left, size=10.5)

    stamp = sig.when.strftime("%d.%m.%Y %H:%M")
    src_txt = f"данные: {source}"
    # Защита от устаревших данных: терминал мог отвалиться, а алерт — прийти.
    if source == "MT5" and not is_demo and bars:
        last_t = bars[-1].t
        if last_t.tzinfo is None:
            last_t = last_t.replace(tzinfo=dt.timezone.utc)
        age_min = (sig.when.astimezone(dt.timezone.utc) - last_t.astimezone(dt.timezone.utc)).total_seconds() / 60
        if age_min > 3 * feeds.tf_minutes(sig.tf):
            src_txt += f"  ·  ! последний бар {age_min / 60:.0f} ч назад"

    c.text(0.965, 0.095, f"{stamp} {cfg.TZ_LABEL}  ·  {src_txt}",
           size=9, color=theme.LABEL3, ha="right")

    c.text(0.035, 0.045, cfg.DISCLAIMER, size=8, color=theme.LABEL3)

    return c.save(out_path)


# ------------------------------------------------------ карточка-сводка счёта


@dataclass
class AccountPlan:
    """Параметры счёта подписчика для карточки-сводки."""
    deposit: float
    risk_pct: float = 0.5        # риск на сделку, % от депозита
    daily_pct: float = 5.0       # дневной стоп, % от депозита
    total_pct: float = 10.0      # общий стоп, % от депозита
    stops_today: int = 0         # сколько стопов уже сработало сегодня
    title: str = "План счёта"
    handle: str = ""
    when: dt.datetime = dt.datetime(2000, 1, 1)

    @property
    def risk_money(self) -> float:
        return self.deposit * self.risk_pct / 100.0

    @property
    def daily_money(self) -> float:
        return self.deposit * self.daily_pct / 100.0

    @property
    def total_money(self) -> float:
        return self.deposit * self.total_pct / 100.0

    @property
    def stops_to_day(self) -> int:
        """Сколько стопов подряд закрывают день при заданном риске."""
        return int(self.daily_money // self.risk_money) if self.risk_money > 0 else 0


def _usd(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ") + " $"


def render_account_card(plan: AccountPlan, out_path: str) -> str:
    """Сводка счёта: депозит, риск на сделку, дневной и общий стоп."""
    c = theme.Card(12.8, 7.2)

    # ---------------- шапка ----------------
    c.text(0.035, 0.945, cfg.CARD_TITLE, size=20, weight="bold", display=True)
    c.text(0.035, 0.893, theme.spaced("ACCOUNT PLAN"), size=8.5, color=theme.LABEL3)
    c.text(0.965, 0.945, plan.title, size=19, weight="bold", display=True, ha="right")
    c.hairline(0.035, 0.965, 0.855)

    stops_day = plan.stops_to_day
    used_frac = (plan.stops_today / stops_day) if stops_day > 0 else 0.0
    risk_note = f"{plan.risk_pct:.2f}% от депозита"
    if stops_day:
        risk_note += f"  ·  {stops_day} стопов = дневной лимит"

    rows = [
        ("Депозит", _usd(plan.deposit), "стартовый баланс счёта", None, theme.ACCENT),
        ("Риск на сделку", _usd(plan.risk_money), risk_note, None, theme.LABEL),
        ("Дневной стоп", f"{plan.daily_pct:.1f}%  ·  {_usd(plan.daily_money)}",
         "сегодня стопов: " + (f"{plan.stops_today} из {stops_day}" if stops_day else str(plan.stops_today)),
         used_frac, theme.ORANGE),
        ("Общий стоп", f"{plan.total_pct:.1f}%  ·  {_usd(plan.total_money)}",
         "просадка, после которой останавливаемся и пересматриваем систему", None, theme.RED),
    ]

    base, step = 0.79, 0.172
    for n, (label, value, note, frac, color) in enumerate(rows):
        y = base - n * step
        c.glass(0.035, y - 0.062, 0.93, 0.134, rad=c.r_in(0.30), z=2)
        c.text(0.062, y + 0.036, label, size=10.5, color=theme.LABEL3)
        c.text(0.943, y + 0.036, value, size=21, weight="bold", display=True,
               color=color, ha="right")
        c.text(0.062, y - 0.016, note, size=9, color=theme.LABEL2)
        if frac is not None:
            c.track(0.062, y - 0.052, 0.881, 0.014, frac, color=color)

    # ---------------- подвал ----------------
    c.hairline(0.035, 0.965, 0.128)
    left = f"1 стоп = {_usd(plan.risk_money)}"
    if stops_day:
        left += f"  ·  {stops_day} стопов = день закрыт"
    c.text(0.035, 0.095, left, size=10.5)
    tail = f"{plan.when:%d.%m.%Y %H:%M} {cfg.TZ_LABEL}"
    if plan.handle:
        tail += f"  ·  {plan.handle}"
    c.text(0.965, 0.095, tail, size=9, color=theme.LABEL3, ha="right")
    c.text(0.035, 0.045, cfg.DISCLAIMER, size=8, color=theme.LABEL3)

    return c.save(out_path)


# ------------------------------------------------------ текстовые карточки

_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def plain(s: str) -> str:
    """Простой текст без HTML-разметки — на карточках разметка не нужна."""
    return (_TAG_RE.sub("", s).replace("&amp;", "&").replace("&lt;", "<")
            .replace("&gt;", ">").replace("&quot;", '"'))


def _wrap(text: str, width: int) -> list[str]:
    """Перенос по словам: matplotlib сам текст не переносит."""
    lines: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            if line and len(line) + 1 + len(word) > width:
                lines.append(line)
                line = word
            else:
                line = (line + " " + word).strip()
        lines.append(line)
    return lines


def _chars(size: float, span: float = 0.93) -> int:
    """Сколько символов помещается в строку карточки при заданном кегле."""
    return max(10, int(12.8 * 72 * span / (size * 0.56)))


KIND_LABEL = {
    "tp1": "Цель 1 взята",
    "tp2": "Цель 2 взята",
    "tp3": "Конечная цель взята",
    "tp": "Цель взята",
    "sl": "Стоп сработал",
    "be": "Безубыток",
    "time": "Закрытие по времени",
}


def render_result_card(*, symbol: str, side: str, kind: str, r: float, out_path: str,
                       entry: float | None = None, stop: float | None = None,
                       exit_px: float | None = None, note: str = "", tf: str = "15",
                       when: dt.datetime | None = None) -> str:
    """Карточка закрытой сделки: исход и результат в R."""
    is_long = side.upper() == "LONG"
    side_word = "LONG" if is_long else "SHORT"
    flat = abs(r) <= 0.001
    color = theme.GREEN if r > 0.001 else (theme.LABEL2 if flat else theme.RED)

    c = theme.Card(12.8, 7.2)

    c.text(0.035, 0.945, cfg.CARD_TITLE, size=20, weight="bold", display=True)
    c.text(0.035, 0.893, theme.spaced("TRADE RESULT"), size=8.5, color=theme.LABEL3)

    c.text(0.965, 0.945, f"{symbol} · {feeds.tf_label(tf)}", size=22,
           weight="bold", display=True, ha="right")
    c.text(0.845, 0.884, "сделка закрыта", size=10, color=theme.LABEL2, ha="right")
    c.chip(0.858, 0.8615, 0.107, 0.045, side_word,
           color=theme.GREEN if is_long else theme.RED, size=11.5)

    c.hairline(0.035, 0.965, 0.845)

    rtxt = "0.00R" if flat else f"{r:+.2f}R"
    c.text(0.5, 0.655, rtxt, size=72, weight="bold", display=True, color=color, ha="center")
    c.text(0.5, 0.545, KIND_LABEL.get(kind, "Результат"), size=14, color=theme.LABEL2,
           ha="center")

    # три плитки: вход / стоп / выход
    cells = [("Вход", entry, theme.LABEL), ("Стоп", stop, theme.RED),
             ("Выход", exit_px, theme.LABEL)]
    x0, w, gap, y0, h = 0.035, 0.30, 0.015, 0.295, 0.155
    for n, (label, value, col) in enumerate(cells):
        x = x0 + n * (w + gap)
        c.glass(x, y0, w, h, rad=c.r_in(0.30))
        c.text(x + 0.022, y0 + h - 0.042, label, size=10, color=theme.LABEL3)
        c.text(x + w - 0.022, y0 + 0.045, money(value) if value is not None else "—",
               size=17, weight="bold", display=True, color=col, ha="right")

    note = plain(note).strip()
    if note:
        c.text(0.035, 0.215, "\n".join(_wrap(note, _chars(10.5))[:2]), size=10.5,
               color=theme.LABEL2, va="top")

    c.hairline(0.035, 0.965, 0.128)
    if entry and stop:
        c.text(0.035, 0.095, f"риск {abs(entry - stop) / entry * 100:.2f}% от входа",
               size=10.5)
    tail = f"{(when or dt.datetime.now()).strftime('%d.%m.%Y %H:%M')} {cfg.TZ_LABEL}"
    c.text(0.965, 0.095, tail, size=9, color=theme.LABEL3, ha="right")
    c.text(0.035, 0.045, cfg.DISCLAIMER, size=8, color=theme.LABEL3)

    return c.save(out_path)


@dataclass
class SummaryPlan:
    """Данные сводки за период для карточки-отчёта."""
    title: str = "Итог дня"
    period: str = ""
    trades: int = 0
    wins: int = 0
    sum_r: float = 0.0
    bal_start: float | None = None
    bal_end: float | None = None
    note: str = ""
    when: dt.datetime = dt.datetime(2000, 1, 1)


def render_summary_card(plan: SummaryPlan, out_path: str) -> str:
    """Карточка итога дня или недели: сумма R, счёт, комментарий."""
    c = theme.Card(12.8, 7.2)
    color = theme.GREEN if plan.sum_r > 0.001 else (
        theme.LABEL2 if abs(plan.sum_r) <= 0.001 else theme.RED)

    c.text(0.035, 0.945, cfg.CARD_TITLE, size=20, weight="bold", display=True)
    c.text(0.035, 0.893, theme.spaced("REPORT"), size=8.5, color=theme.LABEL3)
    c.text(0.965, 0.945, plan.title, size=19, weight="bold", display=True, ha="right")
    if plan.period:
        c.text(0.965, 0.893, plan.period, size=10.5, color=theme.LABEL3, ha="right")
    c.hairline(0.035, 0.965, 0.855)

    c.text(0.5, 0.70, f"{plan.sum_r:+.1f}R", size=64, weight="bold", display=True,
           color=color, ha="center")
    losses = max(0, plan.trades - plan.wins)
    c.text(0.5, 0.60, f"Сделок {plan.trades}   ·   {plan.wins} целей / {losses} стопов",
           size=13, color=theme.LABEL2, ha="center")

    if plan.bal_start is not None and plan.bal_end is not None:
        c.glass(0.035, 0.315, 0.93, 0.19, rad=c.r_in(0.30))
        c.text(0.062, 0.458, "Счёт", size=10.5, color=theme.LABEL3)
        c.text(0.943, 0.451, _usd(plan.bal_end), size=24, weight="bold", display=True,
               color=theme.LABEL, ha="right")
        pct = (plan.bal_end / plan.bal_start - 1) * 100 if plan.bal_start else 0.0
        c.text(0.062, 0.355, f"{_usd(plan.bal_start)}  →  {pct:+.1f}%", size=10,
               color=theme.LABEL2)

    note = plain(plan.note).strip()
    if note:
        c.text(0.035, 0.265, "\n".join(_wrap(note, _chars(10.5))[:3]), size=10.5,
               color=theme.LABEL2, va="top")

    c.hairline(0.035, 0.965, 0.128)
    c.text(0.035, 0.095, f"{plan.trades} сделок за период", size=10.5)
    c.text(0.965, 0.095, f"{plan.when:%d.%m.%Y %H:%M} {cfg.TZ_LABEL}", size=9,
           color=theme.LABEL3, ha="right")
    c.text(0.035, 0.045, cfg.DISCLAIMER, size=8, color=theme.LABEL3)

    return c.save(out_path)


def render_plan_card(*, title: str, subtitle: str, body: list[str], out_path: str,
                     when: dt.datetime | None = None) -> str:
    """Карточка-заметка на день: заголовок и абзацы плана."""
    c = theme.Card(12.8, 7.2)

    c.text(0.035, 0.945, cfg.CARD_TITLE, size=20, weight="bold", display=True)
    c.text(0.035, 0.893, theme.spaced("PLAN"), size=8.5, color=theme.LABEL3)
    c.text(0.965, 0.945, title, size=19, weight="bold", display=True, ha="right")
    if subtitle:
        c.text(0.965, 0.893, subtitle, size=10.5, color=theme.LABEL3, ha="right")
    c.hairline(0.035, 0.965, 0.855)

    size, lh, width, y = 12.5, 0.042, _chars(12.5), 0.80
    for para in body:
        for line in _wrap(plain(para).strip(), width):
            if y < 0.20:
                break
            c.text(0.035, y, line, size=size, color=theme.LABEL2, va="top")
            y -= lh
        y -= 0.024

    c.hairline(0.035, 0.965, 0.128)
    c.text(0.035, 0.095, "сигналы будут здесь, по одному, как войду", size=10.5)
    c.text(0.965, 0.095, f"{(when or dt.datetime.now()):%d.%m.%Y} {cfg.TZ_LABEL}", size=9,
           color=theme.LABEL3, ha="right")
    c.text(0.035, 0.045, cfg.DISCLAIMER, size=8, color=theme.LABEL3)

    return c.save(out_path)
