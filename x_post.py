"""Ручной композер англоязычных постов для X (Twitter), аккаунт @KidzordFX.

Отдельный англоязычный аккаунт: тот же смысл воронки, что у Threads, но текст
на английском и лимит 280 символов. Meta/Developer API не настроены, поэтому
постим руками, но без набора текста: скрипт копирует пост в буфер обмена и
открывает X с уже подставленным текстом. Остаётся нажать «Post».

Запуск:
    python x_post.py              # показать список и выбрать интерактивно
    python x_post.py 3            # взять пост №3
    python x_post.py next         # следующий ещё не использованный
    python x_post.py done 3       # пометить пост №3 опубликованным
    python x_post.py bio          # описание профиля (bio) — скопировать в буфер

К каждому посту при публикации добавляется строка хештегов (см. TAG_LINES) —
она не идёт на карточку, только в сам твит.

Что уже брали — в x_used.json рядом со скриптом. Карточки (англ.) лежат в
x_img/NN.png и рисуются threads_img.py: python threads_img.py en
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
USED_PATH = HERE / "x_used.json"
IMG_DIR = HERE / "x_img"
INTENT = "https://twitter.com/intent/tweet?text="
LIMIT = 280

POSTS: list[dict] = [
    {"title": "Week's number", "text":
     "13 stops this week. Still in profit.\n"
     "My target is 3x my stop — one win pays for three losses.\n"
     "I count R, not win rate. Boring, that's why it works.\n"
     "Full flow -> Telegram, link in bio."},

    {"title": "About the stop", "text":
     "A stop isn't a mistake. A missing stop is.\n"
     "I risk 0.5% per trade, so 5 stops in a row is -2.5% — a normal day, not a disaster.\n"
     "Math beats nerves."},

    {"title": "I don't chase trends", "text":
     "I don't chase trends.\n"
     "On a breakout the noise takes my stop while the target never comes.\n"
     "I want the range: price sweeps a level and returns.\n"
     "Boring — but the rules are set in advance."},

    {"title": "Risk 0.5%", "text":
     "My risk per trade: 0.5%.\n"
     "Looks like cowardice. It's math: 10 stops in a row is -5%, not the end of the account.\n"
     "Big money isn't made by risking big — but by not blowing up."},

    {"title": "Liquidity sweep", "text":
     "If price sweeps the old low and snaps back above — that's not a breakout.\n"
     "It's a stop hunt: everyone with a loss under the low got taken out.\n"
     "Then price runs to the opposite edge. That's my long."},

    {"title": "I don't sit at the chart", "text":
     "How long do I sit at the chart? Zero.\n"
     "The system scans every 20 seconds and sends only what matters.\n"
     "My job is to keep the rules and keep my hands off. Best skill of the year."},

    {"title": "I post stops too", "text":
     "I post my stops, not only my wins.\n"
     "Everyone with a \"100% win rate\" is lying.\n"
     "Want to know if a channel is real? Look at its bad week. Mine was weak. I left it up."},

    {"title": "The account", "text":
     "My balance? Real numbers.\n"
     "Started at $5,000. Now ~$6,050 — +21% since the start. Last week was weak: +0.9%.\n"
     "No \"doubled in a month\". Just discipline and limits."},

    {"title": "Engagement: instrument", "text":
     "Which instrument should I add next?\n"
     "1) Silver\n2) US indices\n3) None — gold and forex are enough\n"
     "Reply with a number. I'll decide by stats, not by taste."},

    {"title": "Soft offer", "text":
     "Free signals — but with a 30-minute delay. By then price is usually gone.\n"
     "Want them instantly and sized to your own deposit? There's a bot.\n"
     "Full flow -> Telegram, link in bio."},

    {"title": "Daily stop limit", "text":
     "A rule many call weakness: no more than 3 stops a day.\n"
     "Three stops and the day is closed — even if \"it's about to reverse\".\n"
     "You miss a trade. You don't lose a month."},

    {"title": "Why 3R target", "text":
     "Why is my target 3R and my stop 1R?\n"
     "Because then I can be wrong 2 out of 3 times and still be in profit.\n"
     "I don't predict. I build math where being wrong is allowed."},

    {"title": "Prop-firm limits", "text":
     "I trade by prop-firm rules: 0.5% per trade, a daily limit, an overall drawdown limit.\n"
     "Even without passing an exam I keep these rails.\n"
     "An account lives longer than greed."},

    {"title": "Trade journal", "text":
     "After every trade I log it: entry, stop, target, what worked.\n"
     "Without that, a month later you can't tell a rule from luck.\n"
     "The best decisions came from boring spreadsheet rows."},

    {"title": "Trader's morning", "text":
     "My morning starts not with a forecast but with one question: where's the nearest level — and is there a setup at all?\n"
     "If not, the terminal closes and the day goes on without me.\n"
     "That's part of the job too."},

    {"title": "90% is waiting", "text":
     "90% of trading is waiting.\n"
     "Price in the middle of a range is the worst place to enter. Better to skip the day than to trade out of boredom.\n"
     "Boredom is free. A bored trade isn't."},

    {"title": "A missed trade", "text":
     "Missing a trade isn't a loss.\n"
     "The loss is entering with no level, no reason and no stop.\n"
     "I don't regret moves that happened without me — only trades that broke my rules."},

    {"title": "Position size", "text":
     "Stop too wide? Cut your size — don't move the stop.\n"
     "Move a stop \"so it doesn't get hit\" and one trade becomes a hole in the account.\n"
     "Position size is the only safe dial."},

    {"title": "Why public stops", "text":
     "My channel isn't a highlight reel. There will be stops here.\n"
     "A profit-only channel is marketing. A channel with stops is a strategy you can check.\n"
     "Trust is built on bad days."},

    {"title": "What is R", "text":
     "R is one unit of my risk. Risk 0.5% and +3R = +1.5%.\n"
     "Think in R, not in money: then the result depends far less on account size — only on decision quality."},

    {"title": "Gold, forex, indices", "text":
     "I don't trade only gold.\n"
     "Gold, forex, indices — because an entry idea is measured by one thing: a level, a stop, a target.\n"
     "Diversification isn't symbols. It's calm."},

    {"title": "Trading sessions", "text":
     "The market lives in two windows: London and New York.\n"
     "Outside them price is choppy and empty — that's where stops get farmed.\n"
     "My signals mostly come in the active hours."},

    {"title": "A good system is boring", "text":
     "A good system is boring.\n"
     "If a strategy feels exciting, you're probably gambling, not trading.\n"
     "I want results, not adrenaline. Adrenaline pays nothing."},

    {"title": "How much do you make", "text":
     "\"How much do you make?\"\n"
     "I count in R, not money. Money differs for everyone; system quality doesn't.\n"
     "Last 30 days: +38R at 0.5% risk. That's an answer about the system."},

    {"title": "Direct offer", "text":
     "What bot access gives you:\n"
     "• real-time signals, no delay\n"
     "• trade size for your deposit\n"
     "• daily and overall limit control\n"
     "Access and price -> Telegram, link in bio."},

    {"title": "Return to EMA200 zone", "text":
     "I don't need a trend. I need a return.\n"
     "I watch the 4H EMA200 zone: if price comes back and structure confirms — there's the entry.\n"
     "Moves without pullbacks? I skip them. Calmly."},

    {"title": "Why MT5", "text":
     "I run MetaTrader 5: my broker, my data.\n"
     "Fancy wrappers add nothing to the result and break more often.\n"
     "A boring tool that works beats a pretty, capricious one."},

    {"title": "After a losing streak", "text":
     "After three stops in a row you want to revenge-trade — or to quit.\n"
     "Both are emotions, not trading.\n"
     "Simple rule: the daily limit is hit, I close the terminal. Next entry tomorrow, by plan."},
]


# Строка хештегов внизу твита (ротация по номеру поста). На карточку не идёт.
TAG_LINES: list[str] = [
    "#trading #forex #XAUUSD #gold",
    "#forex #trading #indices #riskmanagement",
    "#XAUUSD #gold #trading #smartmoney",
    "#trading #propfirm #riskmanagement #forex",
]

# Описание профиля X (bio, лимит 160 символов). Команда: python x_post.py bio
BIO = (
    "Gold, forex, indices. Smart-money entries: range + sweep. "
    "Risk 0.5% per trade, max 3 stops a day. Rules over predictions. "
    "Signals in Telegram — link below."
)


def tweet_text(i: int) -> str:
    """Текст твита: тело поста + строка хештегов (то, что реально публикуем)."""
    body = POSTS[i - 1]["text"]
    return body + "\n\n" + TAG_LINES[(i - 1) % len(TAG_LINES)]


def _load_used() -> set:
    if USED_PATH.exists():
        try:
            return set(json.loads(USED_PATH.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def _save_used(used: set) -> None:
    USED_PATH.write_text(json.dumps(sorted(used)), encoding="utf-8")


def next_unused() -> int | None:
    """Номер первого неопубликованного поста. None — банк исчерпан."""
    used = _load_used()
    for i in range(1, len(POSTS) + 1):
        if i not in used:
            return i
    return None


def mark_used(n: int) -> None:
    """Отметить пост опубликованным (общий список с ручным композером)."""
    used = _load_used()
    used.add(n)
    _save_used(used)


def show() -> None:
    used = _load_used()
    for i, p in enumerate(POSTS, 1):
        mark = "✓" if i in used else " "
        print(f"[{mark}] {i:>2}. {p['title']}")


def pick(n: int, used: set) -> None:
    if not 1 <= n <= len(POSTS):
        print(f"Нет поста №{n}. Всего {len(POSTS)}.")
        return
    post = POSTS[n - 1]
    text = tweet_text(n)

    if len(text) > LIMIT:
        print(f"Внимание: пост №{n} длиннее {LIMIT} символов, X его обрежет.")

    # Помечаем сразу: если подведёт буфер или браузер, пост всё равно не потеряется.
    used.add(n)
    _save_used(used)

    from threads_post import _clip      # общий помощник буфера обмена (Windows)

    ok, how = _clip(text)
    if ok:
        print(f"Текст скопирован в буфер обмена ({how}).")
    else:
        print(f"Не удалось скопировать автоматически: {how}")
        print("Текст ниже — скопируй вручную.")

    url = INTENT + urllib.parse.quote(text, safe="")
    try:
        opened = webbrowser.open(url)
    except Exception as e:
        opened = False
        print("Браузер не открылся:", e)
    if not opened:
        print("Открой X вручную (https://x.com) и вставь текст из буфера.")

    img = IMG_DIR / f"{n:02d}.png"
    if img.exists():
        print(f"Картинка: {img}")
        try:
            os.startfile(str(img))      # Windows: открываем, чтобы перетащить в пост
            print("Картинка открыта — добавь её в пост в X.")
        except OSError:
            pass
    else:
        print(f"Картинки нет. Отрисуй: python threads_img.py en {n}")

    print(f"\n№{n} · {post['title']}")
    print("-" * 60)
    print(text)
    print("-" * 60)
    print("Проверь текст в открывшемся окне X и нажми «Post».")


def main() -> None:
    args = list(sys.argv[1:])
    used = _load_used()

    if args and args[0] == "bio":
        from threads_post import _clip
        ok, how = _clip(BIO)
        print(BIO)
        note = f"скопировано в буфер обмена: {how})" if ok else "не удалось скопировать)"
        print(f"\n({len(BIO)}/160 символов, {note}")
        return

    if args and args[0] == "done":
        if len(args) < 2 or not args[1].isdigit():
            print("Формат: python x_post.py done <номер>")
            return
        used.add(int(args[1]))
        _save_used(used)
        print(f"Пост №{args[1]} помечен как опубликованный.")
        return

    if args and args[0] == "next":
        free = [i for i in range(1, len(POSTS) + 1) if i not in used]
        if not free:
            print("Все посты из банка использованы.")
            return
        pick(free[0], used)
        return

    if args and args[0].isdigit():
        pick(int(args[0]), used)
        return

    show()
    print("\nНомер поста (Enter — выйти): ", end="")
    try:
        raw = input()
    except EOFError:
        return
    if raw.strip().isdigit():
        pick(int(raw.strip()), used)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
