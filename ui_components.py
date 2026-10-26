"""Inline кнопки и образовательный контент для premium-бота."""
from __future__ import annotations

from typing import List
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config as cfg


# ------------------------------------------------------------------ Inline кнопки

def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню с кнопками."""
    keyboard = [
        [
            InlineKeyboardButton("💎 Premium", callback_data="premium"),
            InlineKeyboardButton("📊 Мой счёт", callback_data="mycard"),
        ],
        [
            InlineKeyboardButton("⚙️ Настройки", callback_data="settings"),
            InlineKeyboardButton("📚 Обучение", callback_data="education"),
        ],
        [
            InlineKeyboardButton("ℹ️ Помощь", callback_data="help"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def settings_keyboard(current_deposit: float, current_risk: float) -> InlineKeyboardMarkup:
    """Клавиатура настроек депозита и риска."""
    keyboard = [
        [
            InlineKeyboardButton("💰 Депозит", callback_data="settings_deposit"),
            InlineKeyboardButton(f"{current_risk}% риск", callback_data="settings_risk"),
        ],
        [
            InlineKeyboardButton("📊 Лимиты", callback_data="settings_limits"),
        ],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="main_menu"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def deposit_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора депозита."""
    keyboard = [
        [
            InlineKeyboardButton("$1,000", callback_data="deposit_1000"),
            InlineKeyboardButton("$5,000", callback_data="deposit_5000"),
        ],
        [
            InlineKeyboardButton("$10,000", callback_data="deposit_10000"),
            InlineKeyboardButton("$25,000", callback_data="deposit_25000"),
        ],
        [
            InlineKeyboardButton("$50,000", callback_data="deposit_50000"),
            InlineKeyboardButton("🔧 Свой", callback_data="deposit_custom"),
        ],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="settings"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def risk_keyboard(current_risk: float) -> InlineKeyboardMarkup:
    """Клавиатура выбора риска."""
    keyboard = [
        [
            InlineKeyboardButton("0.25%", callback_data="risk_0.25"),
            InlineKeyboardButton("0.5%", callback_data="risk_0.5"),
        ],
        [
            InlineKeyboardButton("1%", callback_data="risk_1"),
            InlineKeyboardButton("2%", callback_data="risk_2"),
        ],
        [
            InlineKeyboardButton("🔧 Свой", callback_data="risk_custom"),
        ],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="settings"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def limits_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура лимитов."""
    keyboard = [
        [
            InlineKeyboardButton("📊 Дневной лимит", callback_data="limits_daily"),
            InlineKeyboardButton("🎯 Общий лимит", callback_data="limits_total"),
        ],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="settings"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def education_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура образовательного контента."""
    keyboard = [
        [
            InlineKeyboardButton("📖 Глоссарий", callback_data="edu_glossary"),
            InlineKeyboardButton("🎓 Уроки", callback_data="edu_lessons"),
        ],
        [
            InlineKeyboardButton("❓ Quiz", callback_data="edu_quiz"),
            InlineKeyboardButton("📊 Анализ сигналов", callback_data="edu_signals"),
        ],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="main_menu"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def lessons_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура уроков."""
    keyboard = [
        [
            InlineKeyboardButton("1. SMC basics", callback_data="lesson_1"),
            InlineKeyboardButton("2. Ликвидность", callback_data="lesson_2"),
        ],
        [
            InlineKeyboardButton("3. BOS", callback_data="lesson_3"),
            InlineKeyboardButton("4. Управление риском", callback_data="lesson_4"),
        ],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="education"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def quiz_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура викторины."""
    keyboard = [
        [
            InlineKeyboardButton("🎯 Начать тест", callback_data="quiz_start"),
        ],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="education"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def quiz_answer_keyboard(question_id: int, options: List[str]) -> InlineKeyboardMarkup:
    """Клавиатура ответов на вопрос викторины."""
    keyboard = []
    for i, option in enumerate(options):
        keyboard.append([InlineKeyboardButton(option, callback_data=f"quiz_answer_{question_id}_{i}")])
    keyboard.append([InlineKeyboardButton("⬅️ Пропустить", callback_data="quiz_skip")])
    return InlineKeyboardMarkup(keyboard)


# ------------------------------------------------------------------ Образовательный контент

GLOSSARY = {
    "SMC": "Smart Money Concept - концепцияSmart Money, изучающая действия крупных игроков (институционалов) на рынке.",
    "BOS": "Break of Structure - пробой структуры, который signalling смену тренда.",
    "Liquidity": "Ликвидность - зоны с большим количеством стоп-лоссов, которые часто срабатывают.",
    "FVG": "Fair Value Gap - разрыв между свечами, который цена часто заполняет.",
    "Order Block": "Зона ордеров - место, где институционалы открыли крупные позиции.",
    "Mitigation": "Снятие ликвидности - когда цена достигает зоны стоп-лоссов.",
    "Inducement": "Приманка - ценовой уровень, который привлекает трейдеров к входу.",
    "MSS": "Market Structure Shift - сдвиг структуры рынка, резкая смена направления.",
}


LESSONS = {
    1: {
        "title": "📖 Урок 1: SMC Basics",
        "content": """
<b>Smart Money Concept (SMC)</b>

SMC - это подход к анализу рынка, основанный на действиях крупных игроков (институционалов).

<b>Основные принципы:</b>

1. <b>Ликвидность</b> - зона стоп-лоссов розничных трейдеров
2. <b>Снятие ликвидности</b> - цена стремится к зонам ликвидности
3. <b>Смена структуры</b> - изменение тренда после снятия ликвидности
4. <b>Ордер блоки</b> - зоны, где открывались крупные позиции

<b>Почему это важно?</b>

Институционалы не могут войти в рынок крупным объёмом без смещения цены. Они создают ловушки для розничных трейдеров, снимают их стопы, и только потом двигают цену в нужном направлении.

<i>Следующий урок: Ликвидность</i>
        """,
    },
    2: {
        "title": "📖 Урок 2: Ликвидность",
        "content": """
<b>Ликвидность на рынке</b>

Ликвидность - это концентрация стоп-лоссов и лимитных ордеров в определённой зоне.

<b>Типы ликвидности:</b>

1. <b>Buy-side liquidity</b> - ордера на покупку выше текущей цены
2. <b>Sell-side liquidity</b> - ордера на продажу ниже текущей цены

<b>Как работает:</b>

• Цена подходит к зоне ликвидности
• Розничные трейдеры устанавливают стопы и лимиты
• Институционалы видят эту концентрацию
• Цена резко пробивает зону (снимает ликвидность)
• Институционалы входят в противоположном направлении

<b>На что смотреть:</b>

• Равенство highs/lows
• Острая реакция на уровне
• Большой объём на пробое

<i>Следующий урок: BOS</i>
        """,
    },
    3: {
        "title": "📖 Урок 3: BOS (Break of Structure)",
        "content": """
<b>Break of Structure (BOS)</b>

BOS - это пробой предыдущего экстремума, signalling смену тренда.

<b>Типы BOS:</b>

1. <b>Bullish BOS</b> - пробой предыдущего хая, сигнал на лонг
2. <b>Bearish BOS</b> - пробой предыдущего лоу, сигнал на шорт

<b>Правила:</b>

• БОС должен быть на закрытии свечи
• Фильтр: EMA старшего ТФ
• Конфликт: не входить против тренда старшего ТФ

<b>Пример:</b>

1. Цена делает серию низких минимумов (LL)
2. Пробивает последний низ (BOS)
3. Входим в лонг на откате

<i>Следующий урок: Управление риском</i>
        """,
    },
    4: {
        "title": "📖 Урок 4: Управление риском",
        "content": """
<b>Управление риском</b>

Правильное управление риском - ключ к успеху в трейдинге.

<b>Основные правила:</b>

1. <b>Риск на сделку</b> - не более 0.5-1% от депозита
2. <b>Дневной лимит</b> - максимум 3-5 стопов в день
3. <b>Общий лимит</b> - максимум 10% от депозита
4. <b>Risk-Reward</b> - минимум 1:2 (риск к прибыли)

<b>Как рассчитать размер позиции:</b>

Риск в $ = Депозит × Риск %
Размер позиции = Риск в $ / (SL - Entry)

<b>Пример:</b>

Депозит: $10,000
Риск: 0.5% = $50
SL - Entry: 300 пунктов
Размер позиции: $50 / 300 = $0.166 за пункт

<i>Это последний урок урока!</i>
        """,
    },
}


QUIZ_QUESTIONS = [
    {
        "question": "Что такое SMC?",
        "options": ["Smart Money Concept", "Stop Money Control", "Simple Market Cycle"],
        "correct": 0,
        "explanation": "SMC = Smart Money Concept - концепция Smart Money",
    },
    {
        "question": "Что такое BOS?",
        "options": ["Break of Structure", "Best of Signals", "Buy on Support"],
        "correct": 0,
        "explanation": "BOS = Break of Structure - пробой структуры",
    },
    {
        "question": "Какой рекомендуемый риск на сделку?",
        "options": ["5%", "0.5-1%", "10%"],
        "correct": 1,
        "explanation": "Рекомендуется 0.5-1% от депозита на сделку",
    },
    {
        "question": "Что такое ликвидность?",
        "options": ["Скорость торговли", "Зона стоп-лоссов", "Тип ордера"],
        "correct": 1,
        "explanation": "Ликвидность - зона концентрации стоп-лоссов и лимитных ордеров",
    },
]


def get_glossary_term(term: str) -> str:
    """Получить определение термина."""
    return GLOSSARY.get(term.upper(), "Термин не найден")


def get_lesson(lesson_id: int) -> dict:
    """Получить урок."""
    return LESSONS.get(lesson_id)


def get_quiz_question(question_id: int) -> dict:
    """Получить вопрос викторины."""
    if 0 <= question_id < len(QUIZ_QUESTIONS):
        return QUIZ_QUESTIONS[question_id]
    return None


def get_quiz_count() -> int:
    """Получить количество вопросов в викторине."""
    return len(QUIZ_QUESTIONS)


def get_signal_explanation(signal_data: dict) -> str:
    """Сгенерировать объяснение сигнала."""
    side = signal_data.get("side", "")
    symbol = signal_data.get("symbol", "")
    entry = signal_data.get("entry", 0)
    sl = signal_data.get("sl", 0)
    tp = signal_data.get("tp1", 0)

    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = reward / risk if risk > 0 else 0

    explanation = f"""
<b>Анализ сигнала: {side} {symbol}</b>

<b>📍 Уровни:</b>
• Вход: {entry}
• Стоп: {sl} (риск {risk:.2f})
• Цель: {tp} (прибыль {reward:.2f})
• Risk-Reward: 1:{rr:.1f}

<b>🔍 Почему этот сигнал?</b>

{'1. Ликвидность: цена сняла уровень ниже входа' if side == 'LONG' else '1. Ликвидность: цена сняла уровень выше входа'}
2. BOS: пробой предыдущего экстремума
3. Структура: смена тренда подтверждена
4. Фильтр: EMA старшего ТФ в нашу сторону

<b>⚠️ Управление риском:</b>
• Размер позиции рассчитай по своему депозиту
• Риск на сделку: не более 1%
• Стоп не двигай без причин

<i>Не является инвестиционной рекомендацией.</i>
    """

    return explanation.strip()
