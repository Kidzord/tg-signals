"""Банк учебных TikTok-каруселей: SMC, ICT и MSNR простыми словами.

Оригинальные объяснения публичных концепций. Один урок = обложка + 4–5
слайдов, каждый слайд — одна мысль. Тексты короткие, чтобы читались в
карусели, а не превращались в статью.
"""
from __future__ import annotations

import html
import json
import os

import config as cfg
import edu_cards
import tg

HERE = os.path.dirname(os.path.abspath(__file__))
USED_PATH = os.path.join(HERE, "tiktok_edu_used.json")
OUT_DIR = os.path.join(HERE, "renders", "edu")


LESSONS: list[dict] = [
    {
        "title": "Smart Money: кто двигает цену",
        "slides": [
            {"title": "Цена двигается не сама",
             "text": "За движением стоят крупные участники: банки, фонды, маркетмейкеры. "
                     "Им нужно набрать позицию там, где хватит объёма."},
            {"title": "Ликвидность — это чужие стопы",
             "text": "За максимумами и минимумами стоят стопы розничных трейдеров. "
                     "Крупному игроку выгодно снять их, чтобы набрать позицию.",
             "diagram": "liquidity"},
            {"title": "Смарт мани — не тайна",
             "text": "Это логика: где толпе больно, там топливо для движения. "
                     "Не «магия», а поведение цены вокруг ликвидности."},
            {"title": "Что с этим делать",
             "text": "Не входить в середине диапазона. Ждать, пока снимут ликвидность, "
                     "и искать подтверждение разворота."},
        ],
    },
    {
        "title": "MSNR: уровни по телам свечей",
        "slides": [
            {"title": "Что такое MSNR",
             "text": "Malaysian Support & Resistance. Уровни строят по телам свечей, "
                     "а не по теням, и чаще всего на D1 или H4."},
            {"title": "Тело, а не тень",
             "text": "Тень — это часто свип и шум. Тело показывает, где цена реально "
                     "закрепилась и где остался интерес.",
             "diagram": "msnr"},
            {"title": "Старший таймфрейм",
             "text": "Уровень с D1/H4 — это контекст. Вход ищем на младшем ТФ, "
                     "но уровень берём со старшего."},
            {"title": "Как использовать",
             "text": "Не входить по касанию линии. Ждать реакцию: свип, возврат, "
                     "слом структуры — и только потом искать вход."},
        ],
    },
    {
        "title": "Структура рынка: HH, HL, LH, LL",
        "slides": [
            {"title": "Сначала структура",
             "text": "Прежде чем рисовать уровни, ответь: рынок растёт, падает "
                     "или стоит в диапазоне?"},
            {"title": "Восходящая структура",
             "text": "Каждый новый максимум выше предыдущего (HH), каждый откат "
                     "останавливается выше предыдущего минимума (HL).",
             "diagram": "structure"},
            {"title": "Нисходящая структура",
             "text": "Ниже максимумы (LH) и ниже минимумы (LL). Покупать против "
                     "такой структуры — значит спорить с потоком."},
            {"title": "Диапазон",
             "text": "Цена ходит между двумя уровнями. Внутри диапазона лучший вход — "
                     "от края, а не в середине."},
        ],
    },
    {
        "title": "BOS и CHoCH: слом или продолжение",
        "slides": [
            {"title": "BOS — продолжение",
             "text": "Break of Structure: цена пробивает предыдущий экстремум "
                     "в сторону текущего тренда. Тренд продолжается."},
            {"title": "CHoCH — смена характера",
             "text": "Change of Character: цена ломает последний противоположный "
                     "экстремум. Это первый сигнал, что тренд может развернуться.",
             "diagram": "bos_choch"},
            {"title": "Как отмечать",
             "text": "Сначала находим свинги на старшем ТФ. Потом смотрим, какой "
                     "экстремум сломан: в сторону тренда или против."},
            {"title": "Подтверждение",
             "text": "CHoCH на M5/M15 — не приказ входить. Это разрешение искать "
                     "вход в OB или FVG после свипа."},
        ],
    },
    {
        "title": "Свип ликвидности: охота на стопы",
        "slides": [
            {"title": "Что такое свип",
             "text": "Цена пробивает уровень за экстремум, снимает стопы и "
                     "возвращается обратно. Это не пробой, а сбор ликвидности.",
             "diagram": "liquidity"},
            {"title": "Как выглядит",
             "text": "Длинная тень за уровнем и закрытие обратно. Чем резче "
                     "возврат, тем понятнее, что ликвидность сняли."},
            {"title": "Не каждый свип — вход",
             "text": "Свип без слома структуры и без реакции — просто шум. "
                     "Нужна последовательность, а не одна свеча."},
            {"title": "Что дальше",
             "text": "После свипа ждём CHoCH на младшем ТФ и возврат в зону "
                     "OB/FVG. Только там появляется вход."},
        ],
    },
    {
        "title": "Order Block: зона крупного интереса",
        "slides": [
            {"title": "Что такое OB",
             "text": "Последняя противоположная свеча перед сильным импульсом. "
                     "Считается, что в ней крупный игрок набирал позицию.",
             "diagram": "ob"},
            {"title": "Как искать",
             "text": "Находим импульс, который сломал структуру. Берём последнюю "
                     "свечу против импульса — её тело и есть зона."},
            {"title": "Вход",
             "text": "Ждём возврат в OB и реакцию. Вход — от зоны, стоп — за "
                     "экстремум, который её создал."},
            {"title": "Не каждый OB работает",
             "text": "Зона без свипа, без структуры и без старшего контекста — "
                     "просто прямоугольник на графике."},
        ],
    },
    {
        "title": "FVG: имбаланс и возврат цены",
        "slides": [
            {"title": "Что такое FVG",
             "text": "Fair Value Gap — гэп между первой и третьей свечой импульса. "
                     "Цена часто возвращается, чтобы его закрыть.",
             "diagram": "fvg"},
            {"title": "Как найти",
             "text": "Три свечи: у первой максимум ниже минимума третьей. "
                     "Пространство между ними — зона дисбаланса."},
            {"title": "Зачем он нужен",
             "text": "FVG — не вход сам по себе. Это место, где цена может "
                     "дать реакцию после свипа и слома структуры."},
            {"title": "Комбинация",
             "text": "Лучший вход — FVG, который совпал с OB и уровнем MSNR. "
                     "Один фактор — случайность, три — сетап."},
        ],
    },
    {
        "title": "Premium и Discount: где покупать",
        "slides": [
            {"title": "Диапазон и 50%",
             "text": "Любой диапазон можно разделить пополам. Выше 50% — premium, "
                     "ниже 50% — discount.",
             "diagram": "premium"},
            {"title": "Покупаем дешевле",
             "text": "В лонг логичнее искать discount: цена уже в нижней части "
                     "диапазона, риск до стопа меньше."},
            {"title": "Продаём дороже",
             "text": "В шорт — premium. Покупать на хаях и продавать на лоях — "
                     "худшая сделка по соотношению риск/прибыль."},
            {"title": "Не догма",
             "text": "В сильном тренде цена может долго жить в premium. "
                     "Поэтому 50% — фильтр, а не единственный сигнал."},
        ],
    },
    {
        "title": "Полная модель входа: MSNR + SMC",
        "slides": [
            {"title": "1. Старший ТФ",
             "text": "На D1/H4 отмечаем уровни MSNR по телам и понимаем, "
                     "где сейчас premium, а где discount."},
            {"title": "2. Свип",
             "text": "Цена подходит к уровню и импульсно пробивает его за "
                     "экстремум, снимая стопы. Закрытие — обратно за уровнем.",
             "diagram": "entry"},
            {"title": "3. Слом структуры",
             "text": "На M5/M15 ждём CHoCH в сторону истинного движения. "
                     "Это подтверждает, что свип был манипуляцией."},
            {"title": "4. Вход",
             "text": "Входим от OB или FVG, оставленного после слома. "
                     "Стоп — за свип, цель — противоположная ликвидность."},
            {"title": "5. Риск",
             "text": "Риск 0.5–1% на сделку. Если стоп далеко — уменьшаем "
                     "объём, а не отодвигаем стоп."},
        ],
    },
    {
        "title": "Стоп и цель: математика сделки",
        "slides": [
            {"title": "Стоп — не «побольше»",
             "text": "Стоп ставится за экстремум, который создал сетап. "
                     "Если он слишком далеко — уменьши объём, но не двигай стоп.",
             "diagram": "risk"},
            {"title": "Цель — ликвидность",
             "text": "Цель логично ставить на противоположной ликвидности: "
                     "там, где стоят стопы другой стороны."},
            {"title": "RR",
             "text": "Цель 2R при риске 0.5% даёт +1% к счёту. При 3R — +1.5%. "
                     "Одна хорошая сделка перекрывает 2–3 стопа."},
            {"title": "Лимит",
             "text": "3 стопа за день — день закрыт. После третьего стопа "
                     "решения хуже, чем рынок."},
        ],
    },
    {
        "title": "Почему вход «просто от уровня» не работает",
        "slides": [
            {"title": "Уровень — это зона",
             "text": "Одна линия ничего не значит. Цена может прошить её, "
                     "снять стопы и только потом развернуться."},
            {"title": "Нет контекста",
             "text": "Покупать поддержку в нисходящем тренде — значит ловить "
                     "падающий нож. Сначала структура и старший ТФ."},
            {"title": "Нет подтверждения",
             "text": "Свип, CHoCH, OB/FVG — это подтверждение. Без него "
                     "вход по касанию превращается в угадывание."},
            {"title": "Что делать",
             "text": "Ждать комбинацию: уровень MSNR + свип + слом структуры. "
                     "Нет комбинации — нет сделки."},
        ],
    },
    {
        "title": "Дневник и бэктест: как проверить модель",
        "slides": [
            {"title": "Без дневника нет системы",
             "text": "После каждой сделки три вопроса: вход по правилам? "
                     "стоп стоял где надо? что сделал бы иначе?",
             "diagram": "journal"},
            {"title": "Собери 50 сделок",
             "text": "На 5 сделках статистики нет. Нужно хотя бы 30–50, "
                     "чтобы увидеть винрейт, PF и просадку."},
            {"title": "Не меняй всё сразу",
             "text": "После трёх стопов хочется переписать систему. "
                     "Сначала проверь: правила соблюдались?"},
            {"title": "Считай в R",
             "text": "Не в деньгах. R показывает качество системы, "
                     "а размер депозита — только масштаб."},
        ],
    },
]


def _load_used() -> set[int]:
    if os.path.exists(USED_PATH):
        try:
            return set(json.loads(open(USED_PATH, encoding="utf-8").read()))
        except Exception:
            return set()
    return set()


def _save_used(used: set[int]) -> None:
    open(USED_PATH, "w", encoding="utf-8").write(json.dumps(sorted(used)))


def next_unused() -> int | None:
    used = _load_used()
    for i in range(1, len(LESSONS) + 1):
        if i not in used:
            return i
    return None


def mark_used(n: int) -> None:
    used = _load_used()
    used.add(n)
    _save_used(used)


def remaining() -> int:
    return len(LESSONS) - len(_load_used())


def caption(n: int) -> str:
    lesson = LESSONS[n - 1]
    handle = (cfg.CHANNEL_HANDLE or "@KidzordFX").lstrip("@")
    lines = [
        f"{lesson['title']}.",
        "Коротко и без воды — в карусели.",
        "",
        f"Полный журнал сделок — в Telegram: t.me/{handle}",
    ]
    days = cfg.PROMO_CODES.get("TIKTOK")
    if days:
        lines.append(f"Промокод TIKTOK: +{days} дн. к доступу в боте.")
    lines.append("#трейдинг #смартмани #msnr #золото")
    return "\n".join(lines)


def total() -> int:
    return len(LESSONS)


def next_after(n: int) -> int:
    return n % len(LESSONS) + 1


def _lead(n: int, limit: int = 220) -> str:
    """Первая мысль урока — для подписи к альбому."""
    first = (LESSONS[n - 1].get("slides") or [{}])[0]
    text = f"{first.get('title', '')}. {first.get('text', '')}".strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def user_caption(n: int) -> str:
    title = html.escape(LESSONS[n - 1]["title"])
    return (f"🎓 <b>Методичка {n} · {title}</b>\n\n"
            f"{html.escape(_lead(n))}\n\n"
            "Одна мысль — один слайд. Все методички — /lessons")


def index_text() -> str:
    lines = [
        "<b>🎓 Методички</b>",
        "",
        "Короткие разборы SMC, MSNR и риск-менеджмента. Бесплатно, без регистрации.",
        "",
    ]
    for i, lesson in enumerate(LESSONS, 1):
        lines.append(f"{i}. {html.escape(lesson['title'])}")
    lines += ["", "/lesson — следующая, /lesson 5 — конкретная. Список — /lessons."]
    return "\n".join(lines)


def lessons_markup() -> dict:
    """Список методичек кнопками: по две в ряд."""
    rows: list[list[dict]] = []
    for start in range(1, len(LESSONS) + 1, 2):
        row = [{"text": f"{n}. {LESSONS[n - 1]['title']}",
                "callback_data": f"edu:lesson:{n}"}
               for n in range(start, min(start + 2, len(LESSONS) + 1))]
        rows.append(row)
    rows.append([{"text": "➡️ Следующая", "callback_data": "edu:next:0"},
                 {"text": "🏠 Меню", "callback_data": "main_menu"}])
    return {"inline_keyboard": rows}


def send_index(chat_id: str, token: str | None = None) -> bool:
    ok, _ = tg.send_message(index_text(), token=token, chat_id=chat_id,
                            markup=lessons_markup())
    return ok


def send_to_user(chat_id: str, token: str | None = None,
                 number: int | None = None) -> bool:
    """Отправить методичку подписчику: обложка + слайды и кнопка «дальше».

    В отличие от send_lesson() это не расходует банк для TikTok: урок можно
    открывать повторно и разным людям.
    """
    n = number or 1
    if not 1 <= n <= len(LESSONS):
        tg.send_plain(f"Нет методички #{n}. Всего {len(LESSONS)}.",
                      token=token, chat_id=chat_id)
        return False
    lesson = LESSONS[n - 1]
    out_dir = os.path.join(OUT_DIR, f"lesson_{n:02d}")
    try:
        images = edu_cards.render_lesson(lesson, n, out_dir)
    except Exception as e:
        tg.send_plain(f"Не удалось нарисовать методичку #{n}: {e}",
                      token=token, chat_id=chat_id)
        return False
    res = tg.send_media_group(images[:10], caption=user_caption(n),
                              token=token, chat_id=chat_id)
    if not res.ok:
        tg.send_plain(f"Альбом методички не ушёл: {res.error[:200]}",
                      token=token, chat_id=chat_id)
        return False
    nxt = next_after(n)
    tg.send_plain(
        "Листай карточки — сохрани, если пригодится.",
        token=token, chat_id=chat_id,
        markup={"inline_keyboard": [[
            {"text": "➡️ Следующая методичка", "callback_data": f"edu:lesson:{nxt}"},
            {"text": "📚 Все", "callback_data": "edu:list"},
        ]]})
    return True



def send_lesson(chat_id: str, token: str | None = None, number: int | None = None) -> bool:
    n = number or next_unused()
    if n is None:
        tg.send_plain("🎓 Банк уроков закончился. Скажи — добавлю новые.",
                      token=token, chat_id=chat_id)
        return False
    if not 1 <= n <= len(LESSONS):
        tg.send_plain(f"Нет урока #{n}. Всего {len(LESSONS)}.",
                      token=token, chat_id=chat_id)
        return False
    lesson = LESSONS[n - 1]
    out_dir = os.path.join(OUT_DIR, f"lesson_{n:02d}")
    try:
        images = edu_cards.render_lesson(lesson, n, out_dir)
    except Exception as e:
        tg.send_plain(f"Не удалось нарисовать урок #{n}: {e}",
                      token=token, chat_id=chat_id)
        return False
    res = tg.send_media_group(
        images[:10],
        caption=(f"🎓 <b>Урок {n} · {lesson['title']}</b>\n"
                 "Карточки готовы. Загрузи по порядку в TikTok и выбери музыку."),
        token=token, chat_id=chat_id)
    if not res.ok:
        tg.send_plain(f"Альбом урока не ушёл: {res.error[:200]}",
                      token=token, chat_id=chat_id)
        return False
    tg.send_plain(
        "📋 Текст для TikTok:\n\n" + caption(n) +
        "\n\n🎵 Музыка: спокойный lofi / atmospheric beat.\n"
        "📲 TikTok → + → Загрузить → Фото → выбери слайды по порядку → вставь текст.",
        token=token, chat_id=chat_id)
    mark_used(n)
    return True


def send_next(chat_id: str, token: str | None = None) -> bool:
    return send_lesson(chat_id, token, None)


if __name__ == "__main__":
    used = _load_used()
    for i, lesson in enumerate(LESSONS, 1):
        mark = "✓" if i in used else " "
        print(f"[{mark}] {i:>2}. {lesson['title']} — {len(lesson['slides']) + 1} слайдов")
    print("осталось:", remaining())
