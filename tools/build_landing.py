"""Сборка статического лендинга из trades.jsonl.

Никакого сервера: один index.html с инлайн-CSS и SVG-equity. Его можно
загрузить на Cloudflare Pages / Netlify Drop как есть. Обновляется командой
`python tools/build_landing.py` (autoposts делает это раз в сутки).

Направление UI — «редакционный журнал»: серифные заголовки, mono-цифры,
hairline-разделители, золото только как акцент. Метод/принципы — unslop
(Damir Sokolovsky, CC BY 4.0), https://github.com/heydamir/unslop.
"""
from __future__ import annotations

import datetime as dt
import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config as cfg  # noqa: E402
import track_record  # noqa: E402

OUT_DIR = ROOT / "landing"
UPDATED_FMT = "%d.%m.%Y %H:%M"

LANDING_CSS = """
:root {
  --bg:#0b0d12; --panel:#11141b; --raised:#171b23;
  --line:rgba(233,234,236,.10); --line-strong:rgba(233,234,236,.16);
  --text:#e9eaec; --text2:#b9bec6; --muted:#8f96a1;
  --gold:#c9a66b; --green:#57a08b; --red:#b25b49;
  --serif:ui-serif, Georgia, "Times New Roman", serif;
  --sans:-apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, Arial, sans-serif;
  --mono:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --ease:cubic-bezier(.22,1,.36,1);
  color-scheme:dark;
}
* { box-sizing:border-box; }
html { scroll-behavior:smooth; max-width:100%; overflow-x:hidden; }
body { margin:0; max-width:100%; overflow-x:hidden; background:var(--bg); color:var(--text);
       font:15px/1.65 var(--sans); }
img, svg { max-width:100%; }
.hero > *, .ledger > *, .steps > *, .materials > *, .pricing > * { min-width:0; }
::selection { background:rgba(201,166,107,.28); }
a { color:var(--text); }
.wrap { max-width:1120px; margin:0 auto; padding:0 24px 64px; }
header { display:flex; align-items:center; justify-content:space-between; gap:24px;
         padding:24px 0; border-bottom:1px solid var(--line); }
.brand { display:inline-flex; align-items:center; gap:10px; color:var(--text);
         text-decoration:none; font-size:16px; font-weight:600; letter-spacing:.01em; }
.mark { width:24px; height:24px; display:grid; place-items:center;
        border:1px solid var(--gold); border-radius:6px; color:var(--gold);
        font:600 12px/1 var(--mono); }
nav a { color:var(--muted); text-decoration:none; font-size:14px; margin-left:24px; }
nav a:hover { color:var(--text); }
.hero { display:grid; grid-template-columns:1.05fr .95fr; gap:64px;
        align-items:center; padding:96px 0 56px; }
.hero h1 { font-family:var(--serif); font-size:clamp(38px,5.2vw,64px);
           font-weight:400; line-height:1.04; letter-spacing:-.02em; margin:0 0 24px; }
.lead { color:var(--text2); font-size:19px; line-height:1.55; max-width:34rem; margin:0 0 32px; }
.actions { display:flex; gap:12px; flex-wrap:wrap; }
.btn { display:inline-flex; align-items:center; justify-content:center; min-height:46px;
       padding:0 20px; border:1px solid var(--line-strong); border-radius:8px;
       font:500 15px/1 var(--sans); text-decoration:none;
       transition:background-color .16s var(--ease), border-color .16s var(--ease),
                  color .16s var(--ease), transform .12s var(--ease); }
.btn:active { transform:scale(.98); }
.btn.primary { background:var(--gold); border-color:var(--gold); color:#0b0d12; }
.btn.primary:hover { background:#d4b37c; border-color:#d4b37c; }
.btn.ghost { background:var(--raised); color:var(--text); }
.btn.ghost:hover { border-color:var(--muted); }
.hero-visual { background:var(--panel); border:1px solid var(--line);
               border-radius:14px; padding:20px 20px 16px; }
.visual-top { display:flex; align-items:baseline; justify-content:space-between;
              gap:16px; margin-bottom:16px; }
.visual-label { color:var(--muted); font-size:13px; }
.visual-value { font:500 24px/1 var(--mono); font-variant-numeric:tabular-nums; }
.visual-note { color:var(--muted); font-size:12px; margin-top:14px; }
.equity { width:100%; height:auto; display:block; }
.ledger { display:grid; grid-template-columns:repeat(4,1fr);
          border-top:1px solid var(--line); border-bottom:1px solid var(--line);
          margin:0 0 24px; }
.ledger > div { padding:20px 0; }
.ledger > div + div { border-left:1px solid var(--line); padding-left:24px; }
.k { color:var(--muted); font-size:13px; }
.v { font:500 26px/1.1 var(--mono); font-variant-numeric:tabular-nums; margin-top:6px; }
.notice { border-left:2px solid var(--gold); padding:2px 0 2px 14px;
          color:var(--text2); font-size:14px; margin:0 0 8px; }
section { padding-top:96px; }
h2 { font-family:var(--serif); font-size:30px; font-weight:400; line-height:1.15;
     letter-spacing:-.01em; margin:0 0 28px; }
.section-lead { color:var(--muted); max-width:42rem; margin:-16px 0 28px; font-size:15px; }
p { color:var(--text2); }
.table-wrap { overflow-x:auto; }
table { width:100%; border-collapse:collapse; border-top:1px solid var(--line); font-size:14px; }
th { color:var(--muted); font-weight:400; font-size:13px; text-align:left;
     padding:12px 0; border-bottom:1px solid var(--line); }
td { padding:12px 0; border-bottom:1px solid var(--line); font-variant-numeric:tabular-nums; }
th:last-child, td:last-child { text-align:right; }
tbody tr:hover { background:rgba(233,234,236,.02); }
.pos { color:var(--green); font-weight:500; }
.neg { color:var(--red); font-weight:500; }
.zero { color:var(--muted); font-weight:500; }
.muted { color:var(--muted); font-weight:400; }
.steps { display:grid; grid-template-columns:repeat(3,1fr); border-top:1px solid var(--line); }
.step { padding:24px 24px 0 0; }
.step + .step { border-left:1px solid var(--line); padding-left:24px; }
.step .num { font:500 12px/1 var(--mono); color:var(--gold); letter-spacing:.04em; }
.step b { display:block; font-weight:500; margin:10px 0 6px; }
.step p { margin:0; color:var(--muted); font-size:14px; }
.materials { display:grid; grid-template-columns:1fr 1fr; gap:0 48px; }
.material { border-top:1px solid var(--line); padding:20px 0; }
.material b { display:block; font-weight:500; margin-bottom:6px; }
.material p { margin:0; color:var(--muted); font-size:14px; }
.pricing { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; }
.price-card { background:var(--panel); border:1px solid var(--line);
              border-radius:12px; padding:24px; }
.price-card.featured { border-color:var(--gold); }
.price-title { color:var(--muted); font-size:13px; }
.price-value { font:500 26px/1.1 var(--mono); font-variant-numeric:tabular-nums;
               margin:8px 0 12px; }
.price-card p { color:var(--muted); font-size:14px; margin:0; }
.price-card .btn { width:100%; margin-top:16px; }
details { border-top:1px solid var(--line); padding:16px 0; }
summary { cursor:pointer; font-weight:500; }
details p { color:var(--muted); margin:12px 0 0; }
footer { border-top:1px solid var(--line); margin-top:96px; padding-top:24px;
         color:var(--muted); font-size:13px; }
footer a { color:var(--muted); }
@media (max-width:900px) {
  .hero { grid-template-columns:1fr; gap:32px; padding-top:64px; }
  .hero-visual { max-width:560px; }
  .ledger { grid-template-columns:1fr 1fr; }
  .ledger > div:nth-child(3) { border-left:0; padding-left:0; }
  .steps { grid-template-columns:1fr; }
  .step + .step { border-left:0; padding-left:0; }
  .materials { grid-template-columns:1fr; }
  .pricing { grid-template-columns:1fr; }
  nav { display:none; }
}
@media (max-width:560px) {
  .ledger { grid-template-columns:1fr; }
  .ledger > div + div { border-left:0; padding-left:0; border-top:1px solid var(--line); }
  section { padding-top:64px; }
  footer { margin-top:64px; }
  th:nth-child(3), td:nth-child(3) { display:none; }
  th, td { padding:10px 10px 10px 0; }
  table { font-size:13px; }
}
@media (prefers-reduced-motion:reduce) {
  html { scroll-behavior:auto; }
  .btn { transition:none; }
}
"""


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
    grid = "".join(
        f'<line x1="{pad}" y1="{pad + (height - 2 * pad) * f:.1f}" '
        f'x2="{width - pad}" y2="{pad + (height - 2 * pad) * f:.1f}" '
        f'stroke="#1c222c" stroke-width="1"/>'
        for f in (0.25, 0.5, 0.75))
    one = ""
    if n == 1:
        one = (f'<line x1="{pad}" y1="{last_y}" x2="{width-pad}" y2="{last_y}" '
               f'stroke="{color}" stroke-width="2" stroke-dasharray="6 6"/>')
    return f"""
<svg viewBox="0 0 {width} {height}" class="equity" role="img" aria-label="Накопленный результат в R">
  {grid}
  <line x1="{pad}" y1="{zero_y:.1f}" x2="{width-pad}" y2="{zero_y:.1f}" stroke="#2a3140" stroke-dasharray="4 6"/>
  {one}
  <polyline points="{' '.join(pts)}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>
  <circle cx="{width-pad}" cy="{last_y}" r="3.5" fill="{color}"/>
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
          <div class="price-value">{cfg.SUB_PRICE_STARS} Stars / мес</div>
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
    sample = ("" if m["n"] >= 20 else
              f'<p class="notice">Выборка пока маленькая: {m["n"]} закрытых сделок. '
              f'Публикуем без обещаний доходности — статистика будет расти.</p>')
    premium = _with_start(cfg.PREMIUM_URL or f"https://t.me/{cfg.SUB_BOT_USERNAME}", "landing")
    method = _with_start(cfg.PREMIUM_URL or f"https://t.me/{cfg.SUB_BOT_USERNAME}", "method")
    support = (f'<a href="{html.escape(cfg.SUPPORT_URL)}">Поддержка</a>' if cfg.SUPPORT_URL else "")
    updated = dt.datetime.now().strftime(UPDATED_FMT)
    handle = (cfg.CHANNEL_HANDLE or "@KidzordFX").lstrip("@")

    page = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KidzordFX — публичный журнал сделок</title>
<meta name="description" content="Журнал сделок по золоту и форексу: вход, стоп, цели и результат в R. Бесплатная группа и premium-бот с расчётом риска.">
<meta property="og:title" content="KidzordFX — публичный журнал сделок">
<meta property="og:description" content="Каждый вход, каждый стоп, результат в R. Без «100% винрейта».">
<meta name="theme-color" content="#0b0d12">
<style>{LANDING_CSS}</style>
</head>
<body>
<div class="wrap">
  <header>
    <a class="brand" href="#"><span class="mark" aria-hidden="true">K</span>KidzordFX</a>
    <nav>
      <a href="#journal">Журнал</a>
      <a href="#how">Как это работает</a>
      <a href="#method">Методички</a>
      <a href="#pricing">Цена</a>
      <a href="#faq">FAQ</a>
    </nav>
  </header>

  <main>
    <section class="hero">
      <div class="hero-copy">
        <h1>Публичный журнал сделок по золоту и форексу</h1>
        <p class="lead">Каждый вход, каждый стоп и результат в R. Без «100% винрейта»
          и скриншотов задним числом. Бесплатно — с задержкой, в боте — сразу
          и с расчётом под твой депозит.</p>
        <div class="actions">
          <a class="btn primary" href="{html.escape(premium)}">Пробный доступ {cfg.TRIAL_DAYS} дня</a>
          <a class="btn ghost" href="{html.escape(cfg.CHANNEL_INVITE or '#')}">Бесплатная группа</a>
        </div>
      </div>
      <aside class="hero-visual" aria-label="Кривая результата">
        <div class="visual-top">
          <span class="visual-label">Накопленный результат</span>
          <span class="visual-value">{m['sum_r']:+.1f}R</span>
        </div>
        {_svg_equity(trades)}
        <div class="visual-note">Последние {m['n']} закрытых сделок · обновлено {updated}</div>
      </aside>
    </section>

    <div class="ledger">
      <div><div class="k">Сделок</div><div class="v">{m['n']}</div></div>
      <div><div class="k">Итог</div><div class="v">{m['sum_r']:+.1f}R</div></div>
      <div><div class="k">Profit factor</div><div class="v">{pf}</div></div>
      <div><div class="k">Макс. просадка</div><div class="v">{m['max_dd_r']:.1f}R</div></div>
    </div>
    {sample}

    <section id="journal">
      <h2>Последние сделки</h2>
      <p class="section-lead">Последние 12 закрытых сделок, включая убыточные.
        Результат в R: +1R — это риск, умноженный на один.</p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Дата</th><th>Инструмент</th><th>Сторона</th><th>Результат</th></tr></thead>
          <tbody>{_trade_rows(trades)}</tbody>
        </table>
      </div>
    </section>

    <section id="how">
      <h2>Как это работает</h2>
      <div class="steps">
        <div class="step"><div class="num">01</div><b>Смотри журнал</b>
          <p>В бесплатной группе сигнал приходит с задержкой 30 минут. Видно вход,
          стоп, цели и чем закончилось.</p></div>
        <div class="step"><div class="num">02</div><b>Открой бота</b>
          <p>В premium сигнал приходит сразу. Бот считает объём под твой депозит,
          риск на сделку, дневной и общий лимит.</p></div>
        <div class="step"><div class="num">03</div><b>Проверь на триале</b>
          <p>{cfg.TRIAL_DAYS} дня бесплатно, без карты. Дальше — {cfg.SUB_PRICE_STARS} Stars в месяц.</p></div>
      </div>
    </section>

    <section id="method">
      <h2>Методички</h2>
      <p class="section-lead">Короткие разборы без «секретных стратегий»: одна мысль —
        один слайд. Бесплатно, без подписки и регистрации.</p>
      <div class="materials">
        <div class="material"><b>Смарт-мани и ликвидность</b>
          <p>Кто двигает цену, зачем снимают стопы за экстремумами и почему вход
          в середине диапазона — худший.</p></div>
        <div class="material"><b>MSNR: уровни по телам</b>
          <p>Уровни по телам свечей на D1/H4, а не по теням. Свип, возврат
          и подтверждение вместо входа по касанию.</p></div>
        <div class="material"><b>Структура, BOS и CHoCH</b>
          <p>Как отличить продолжение тренда от слома характера и когда
          вообще не стоит искать вход.</p></div>
        <div class="material"><b>Вход, стоп и риск</b>
          <p>Стоп за экстремум, цель 2–3R, лимит стопов в день и расчёт объёма
          под депозит — математика вместо угадывания.</p></div>
      </div>
      <div class="actions" style="margin-top:24px">
        <a class="btn ghost" href="{html.escape(method)}">Открыть методички в боте</a>
      </div>
      <p class="muted">В боте: /lesson — следующая методичка, /lessons — все.</p>
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
  </main>

  <footer>
    <p>Данные обновлены: {updated} · Telegram: <a href="https://t.me/{html.escape(handle)}">@{html.escape(handle)}</a>
    {(' · ' + support) if support else ''}</p>
    <p>{html.escape(cfg.DISCLAIMER)}</p>
  </footer>
</div>
</body>
</html>
"""
    out = Path(out_dir) if out_dir else OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    path = out / "index.html"
    path.write_text(page, encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    print(build())
