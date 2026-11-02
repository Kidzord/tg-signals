"""Сборка статического лендинга из trades.jsonl.

Никакого сервера: один index.html с инлайн-CSS и SVG-equity. Его можно
загрузить на Cloudflare Pages / Netlify Drop как есть. Обновляется командой
`python tools/build_landing.py` (autoposts делает это раз в сутки).
"""
from __future__ import annotations

import datetime as dt
import html
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config as cfg  # noqa: E402
import track_record  # noqa: E402

OUT_DIR = ROOT / "landing"
UPDATED_FMT = "%d.%m.%Y %H:%M"


def _money(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ") + " $"


def _with_start(url: str, source: str) -> str:
    if not url:
        return ""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}start=src_{source}"


def _svg_equity(trades: list[dict], width: int = 900, height: int = 260) -> str:
    if not trades:
        return '<p class="muted">Пока нет закрытых сделок — график появится после первой.</p>'
    eq: list[float] = []
    acc = 0.0
    for t in trades:
        acc += float(t["r"])
        eq.append(acc)
    lo = min(0.0, min(eq))
    hi = max(0.0, max(eq))
    span = (hi - lo) or 1.0
    pad = 34
    n = len(eq)
    step = (width - 2 * pad) / max(1, n - 1)
    pts = []
    for i, v in enumerate(eq):
        x = pad + i * step
        y = height - pad - (v - lo) / span * (height - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    zero_y = height - pad - (0.0 - lo) / span * (height - 2 * pad)
    last_y = pts[-1].split(",")[1]
    color = "#57a08b" if eq[-1] >= 0 else "#b25b49"
    one = ""
    if n == 1:
        # С одной сделкой полилиния не видна — рисуем горизонталь.
        one = (f'<line x1="{pad}" y1="{last_y}" x2="{width-pad}" y2="{last_y}" '
               f'stroke="{color}" stroke-width="2.4" stroke-dasharray="6 6"/>')
    return f"""
<svg viewBox="0 0 {width} {height}" class="equity" role="img" aria-label="Кривая результата в R">
  <rect x="0" y="0" width="{width}" height="{height}" rx="14" fill="#14171c"/>
  <line x1="{pad}" y1="{zero_y:.1f}" x2="{width-pad}" y2="{zero_y:.1f}" stroke="#2a3140" stroke-dasharray="4 4"/>
  {one}
  <polyline points="{' '.join(pts)}" fill="none" stroke="{color}" stroke-width="2.4" stroke-linejoin="round"/>
  <circle cx="{width-pad}" cy="{last_y}" r="4" fill="{color}"/>
  <text x="{pad}" y="22" fill="#8d949e" font-size="12">Накопленный результат, R</text>
  <text x="{width-pad}" y="22" text-anchor="end" fill="{color}" font-size="13" font-weight="bold">{eq[-1]:+.1f}R</text>
</svg>"""


def _trade_rows(trades: list[dict]) -> str:
    if not trades:
        return '<tr><td colspan="4" class="muted">Закрытых сделок пока нет.</td></tr>'
    labels = {"sl": "стоп", "tp": "цель", "tp3": "TP3", "be": "безубыток", "time": "по времени"}
    rows = []
    for t in sorted(trades, key=lambda x: x["ts"], reverse=True)[:12]:
        when = dt.datetime.fromtimestamp(float(t["ts"])).strftime("%d.%m")
        side = "лонг" if str(t.get("side", "")).upper() == "LONG" else "шорт"
        r = float(t["r"])
        cls = "pos" if r > 0 else ("zero" if abs(r) < 0.001 else "neg")
        rows.append(
            f"<tr><td>{when}</td><td>{html.escape(str(t.get('symbol', '')))}</td>"
            f"<td>{side}</td><td class=\"{cls}\">{r:+.1f}R"
            f" <span class=\"muted\">{labels.get(str(t.get('kind', '')), '')}</span></td></tr>")
    return "".join(rows)


def _pricing() -> str:
    cards = [
        f"""
        <div class="price-card">
          <div class="price-title">Бесплатно</div>
          <div class="price-value">0 ₽</div>
          <p>Сигналы в Telegram с задержкой 30 минут. Полный журнал и статистика.</p>
          <a class="btn ghost" href="{html.escape(cfg.CHANNEL_INVITE or '#')}">Открыть группу</a>
        </div>""",
        f"""
        <div class="price-card featured">
          <div class="price-title">Premium</div>
          <div class="price-value">{cfg.SUB_PRICE_STARS} ⭐ / мес</div>
          <p>Сигнал сразу, расчёт объёма под твой депозит, дневной и общий лимит.
             Пробный доступ на {cfg.TRIAL_DAYS} дня.</p>
          <a class="btn primary" href="{html.escape(_with_start(cfg.PREMIUM_URL or f'https://t.me/{cfg.SUB_BOT_USERNAME}', 'landing'))}">Попробовать</a>
        </div>""",
    ]
    if cfg.CRYPTO_PRICE_USDT > 0:
        cards.append(f"""
        <div class="price-card">
          <div class="price-title">Крипта</div>
          <div class="price-value">{cfg.CRYPTO_PRICE_USDT:g} {html.escape(cfg.CRYPTO_ASSET)} / мес</div>
          <p>Разовая оплата через Crypto Pay. Продление — вручную.</p>
          <a class="btn ghost" href="{html.escape(_with_start(cfg.PREMIUM_URL or f'https://t.me/{cfg.SUB_BOT_USERNAME}', 'landing'))}">Оплатить</a>
        </div>""")
    return "".join(cards)


def build(out_dir: Path | str | None = None) -> str:
    trades = track_record.load_trades(None)
    m = track_record.metrics(trades)
    pf = "∞" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
    winrate = f"{m['winrate']:.0f}%" if m["n"] else "—"
    sample = ("" if m["n"] >= 20 else
              f'<p class="notice">Выборка пока маленькая: {m["n"]} закрытых сделок. '
              f'Публикуем без обещаний доходности — статистика будет расти.</p>')
    premium = _with_start(cfg.PREMIUM_URL or f"https://t.me/{cfg.SUB_BOT_USERNAME}", "landing")
    support = (f'<a href="{html.escape(cfg.SUPPORT_URL)}">Поддержка</a>' if cfg.SUPPORT_URL else "")
    updated = dt.datetime.now().strftime(UPDATED_FMT)
    handle = (cfg.CHANNEL_HANDLE or "@KidzordFX").lstrip("@")

    page = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KidzordFX — публичный журнал сделок</title>
<meta name="description" content="Журнал сделок по золоту, форексу и индексам: вход, стоп, цели и результат в R. Бесплатная группа и premium-бот с расчётом риска.">
<meta property="og:title" content="KidzordFX — публичный журнал сделок">
<meta property="og:description" content="Каждый вход, каждый стоп, результат в R. Без «100% винрейта».">
<meta name="theme-color" content="#0e1116">
<style>
:root {{ --bg:#0e1116; --panel:#14171c; --line:#242b39; --text:#e4e6e9; --muted:#8d949e;
        --green:#57a08b; --red:#b25b49; --gold:#c9a66b; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text);
       font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif; }}
.wrap {{ max-width:980px; margin:0 auto; padding:28px 20px 60px; }}
header {{ display:flex; justify-content:space-between; align-items:center; gap:16px; }}
.brand {{ font-weight:800; letter-spacing:.18em; font-size:14px; color:var(--gold); }}
nav a {{ color:var(--muted); text-decoration:none; margin-left:18px; font-size:14px; }}
nav a:hover {{ color:var(--text); }}
.hero {{ padding:64px 0 36px; }}
h1 {{ font-size:clamp(30px,5vw,54px); line-height:1.08; margin:0 0 18px; letter-spacing:-.02em; }}
.lead {{ color:var(--muted); font-size:clamp(16px,2vw,20px); max-width:720px; margin:0 0 28px; }}
.actions {{ display:flex; gap:12px; flex-wrap:wrap; }}
.btn {{ display:inline-block; padding:13px 20px; border-radius:12px; text-decoration:none;
        font-weight:700; font-size:15px; border:1px solid var(--line); transition:.15s; }}
.btn.primary {{ background:var(--gold); color:#14171c; border-color:var(--gold); }}
.btn.primary:hover {{ filter:brightness(1.08); }}
.btn.ghost {{ color:var(--text); background:transparent; }}
.btn.ghost:hover {{ border-color:var(--muted); }}
.grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:36px 0; }}
.stat {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:16px; }}
.stat .k {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.08em; }}
.stat .v {{ font-size:26px; font-weight:800; margin-top:4px; }}
section {{ margin:50px 0; }}
h2 {{ font-size:26px; margin:0 0 14px; }}
p {{ color:#c5cbd3; }}
.notice {{ background:#1b1a15; border:1px solid #4a3d22; color:#e8d9b5;
           padding:12px 16px; border-radius:12px; }}
.equity {{ width:100%; height:auto; display:block; }}
table {{ width:100%; border-collapse:collapse; background:var(--panel);
         border:1px solid var(--line); border-radius:14px; overflow:hidden; }}
th,td {{ text-align:left; padding:11px 14px; border-bottom:1px solid var(--line); font-size:14px; }}
th {{ color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.06em; }}
tr:last-child td {{ border-bottom:none; }}
.pos {{ color:var(--green); font-weight:700; }}
.neg {{ color:var(--red); font-weight:700; }}
.zero {{ color:var(--muted); font-weight:700; }}
.muted {{ color:var(--muted); font-weight:400; }}
.steps {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }}
.step {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:18px; }}
.step b {{ display:block; margin-bottom:6px; }}
.pricing {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }}
.price-card {{ background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:20px; }}
.price-card.featured {{ border-color:var(--gold); }}
.price-title {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.08em; }}
.price-value {{ font-size:24px; font-weight:800; margin:6px 0 10px; }}
.price-card p {{ font-size:14px; color:var(--muted); }}
.price-card .btn {{ width:100%; text-align:center; margin-top:10px; }}
details {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px 16px; margin:8px 0; }}
summary {{ cursor:pointer; font-weight:600; }}
footer {{ border-top:1px solid var(--line); padding-top:22px; color:var(--muted); font-size:13px; }}
footer a {{ color:var(--muted); }}
@media (max-width:760px) {{
  .grid,.steps,.pricing {{ grid-template-columns:1fr 1fr; }}
  nav {{ display:none; }}
}}
@media (max-width:520px) {{
  .grid,.steps,.pricing {{ grid-template-columns:1fr; }}
  .hero {{ padding-top:40px; }}
}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">KIDZORDFX</div>
    <nav>
      <a href="#journal">Журнал</a>
      <a href="#how">Как это работает</a>
      <a href="#pricing">Цена</a>
      <a href="#faq">FAQ</a>
    </nav>
  </header>

  <div class="hero">
    <h1>Публичный журнал<br>сделок по золоту, форексу и индексам</h1>
    <p class="lead">Каждый вход, каждый стоп, каждая цель и результат в R.
      Без «100% винрейта» и скриншотов задним числом. Бесплатно — с задержкой,
      в боте — сразу и с расчётом под твой депозит.</p>
    <div class="actions">
      <a class="btn primary" href="{html.escape(premium)}">Пробный доступ {cfg.TRIAL_DAYS} дня</a>
      <a class="btn ghost" href="{html.escape(cfg.CHANNEL_INVITE or '#')}">Бесплатная группа</a>
    </div>
  </div>

  <div class="grid">
    <div class="stat"><div class="k">Сделок</div><div class="v">{m['n']}</div></div>
    <div class="stat"><div class="k">Итог</div><div class="v">{m['sum_r']:+.1f}R</div></div>
    <div class="stat"><div class="k">Profit factor</div><div class="v">{pf}</div></div>
    <div class="stat"><div class="k">Макс. просадка</div><div class="v">{m['max_dd_r']:.1f}R</div></div>
  </div>
  {sample}

  <section id="journal">
    <h2>Кривая результата</h2>
    {_svg_equity(trades)}
    <h2 style="margin-top:34px">Последние сделки</h2>
    <table>
      <thead><tr><th>Дата</th><th>Инструмент</th><th>Сторона</th><th>Результат</th></tr></thead>
      <tbody>{_trade_rows(trades)}</tbody>
    </table>
  </section>

  <section id="how">
    <h2>Как это работает</h2>
    <div class="steps">
      <div class="step"><b>1. Смотри журнал</b>
        В бесплатной группе сигнал приходит с задержкой 30 минут. Видно вход,
        стоп, цели и чем закончилось.</div>
      <div class="step"><b>2. Открой бота</b>
        В premium сигнал приходит сразу. Бот считает объём под твой депозит,
        риск на сделку, дневной и общий лимит.</div>
      <div class="step"><b>3. Проверь на триале</b>
        {cfg.TRIAL_DAYS} дня бесплатно, без карты. Дальше — {cfg.SUB_PRICE_STARS} ⭐ в месяц.</div>
    </div>
  </section>

  <section id="pricing">
    <h2>Цена</h2>
    <div class="pricing">{_pricing()}</div>
  </section>

  <section id="faq">
    <h2>Частые вопросы</h2>
    <details><summary>Что такое R?</summary>
      <p>R — единица риска. Риск 0.5% депозита и результат +2R = +1% к счёту.
      Считать в R удобнее, чем в деньгах: результат не зависит от размера депозита.</p></details>
    <details><summary>Почему вы публикуете стопы?</summary>
      <p>Потому что канал только с прибыльными сделками — это маркетинг, а не статистика.
      Стоп — часть системы, риск ограничен заранее.</p></details>
    <details><summary>Это инвестиционная рекомендация?</summary>
      <p>Нет. Это информация и журнал. Решение по сделке принимаешь ты и несёшь
      ответственность за свой риск.</p></details>
    <details><summary>Как отменить подписку?</summary>
      <p>Stars — в «Мои звёзды» в Telegram. Крипта — оплата разовая, продление вручную.</p></details>
  </section>

  <footer>
    <p>Данные обновлены: {updated} · Telegram: <a href="https://t.me/{html.escape(handle)}">@{html.escape(handle)}</a>
    {(' · ' + support) if support else ''}</p>
    <p>{html.escape(cfg.DISCLAIMER)}</p>
  </footer>
</div>
</body>
</html>
"""
    dest = Path(out_dir) if out_dir else OUT_DIR
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "index.html"
    path.write_text(page, encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    print(build())
