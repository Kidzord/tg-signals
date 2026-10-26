"""Добавление inline кнопок и образовательного контента в subbot.py.

Этот файл содержит функции для интеграции в subbot.py.
"""
from __future__ import annotations

import ui_components


def handle_callback(callback_data: str, uid: str, users: dict):
    """Обработка callback от inline кнопок."""
    if callback_data == "main_menu":
        return show_main_menu(uid, users)
    elif callback_data == "premium":
        return show_premium_info(uid)
    elif callback_data == "mycard":
        return handle_mycard(uid, users)
    elif callback_data == "settings":
        return show_settings(uid, users)
    elif callback_data == "education":
        return show_education(uid)
    elif callback_data.startswith("deposit_"):
        return handle_deposit(callback_data, uid, users)
    elif callback_data.startswith("risk_"):
        return handle_risk(callback_data, uid, users)
    elif callback_data.startswith("lesson_"):
        return show_lesson(callback_data, uid)
    elif callback_data == "edu_glossary":
        return show_glossary(uid)
    elif callback_data == "edu_lessons":
        return show_lessons(uid)
    elif callback_data == "edu_quiz":
        return show_quiz_start(uid)
    elif callback_data == "quiz_start":
        return start_quiz(uid)
    elif callback_data.startswith("quiz_answer_"):
        return handle_quiz_answer(callback_data, uid)
    return None


def show_main_menu(uid: str, users: dict):
    """Показать главное меню."""
    import tg
    text = f"""
<b>👋 Привет!</b>

Твой статус: {'✅ Premium' if is_paid(users, uid) else '❌ Без подписки'}

Выбери действие:
    """
    markup = ui_components.main_menu_keyboard()
    tg.send_message(text.strip(), token=TOKEN, chat_id=uid, reply_markup=markup)
    return users.get(uid, {})


def show_premium_info(uid: str):
    """Показать информацию о premium."""
    import tg
    import config as cfg
    text = f"""
<b>💎 Premium подписка</b>

Цена: {cfg.SUB_PRICE_STARS} Stars / месяц

<b>Преимущества:</b>
• Сигналы мгновенно (без задержки)
• Карточка счёта под твои параметры
• Приоритетная поддержка
• Доступ к образовательному контенту

Оформить: /buy
    """
    markup = ui_components.main_menu_keyboard()
    tg.send_message(text.strip(), token=TOKEN, chat_id=uid, reply_markup=markup)


def show_settings(uid: str, users: dict):
    """Показать настройки."""
    import tg
    user = users.get(uid, {})
    deposit = user.get("deposit", cfg.PLAN_DEPOSIT)
    risk = user.get("risk_pct", cfg.PLAN_RISK_PCT)

    text = f"""
<b>⚙️ Настройки</b>

Текущие параметры:
• Депозит: ${deposit:,.0f}
• Риск: {risk}%
    """
    markup = ui_components.settings_keyboard(deposit, risk)
    tg.send_message(text.strip(), token=TOKEN, chat_id=uid, reply_markup=markup)


def handle_deposit(callback_data: str, uid: str, users: dict):
    """Обработать выбор депозита."""
    amount = callback_data.replace("deposit_", "")
    if amount == "custom":
        import tg
        tg.send_message("Введи депозит через команду: /deposit 10000", token=TOKEN, chat_id=uid)
        return users.get(uid, {})

    user = users.setdefault(uid, {})
    user["deposit"] = float(amount)
    import tg
    tg.send_message(f"✅ Депозит установлен: ${amount}", token=TOKEN, chat_id=uid)
    return user


def handle_risk(callback_data: str, uid: str, users: dict):
    """Обработать выбор риска."""
    risk_str = callback_data.replace("risk_", "")
    if risk_str == "custom":
        import tg
        tg.send_message("Введи риск через команду: /risk 0.5", token=TOKEN, chat_id=uid)
        return users.get(uid, {})

    user = users.setdefault(uid, {})
    user["risk_pct"] = float(risk_str)
    import tg
    tg.send_message(f"✅ Риск установлен: {risk_str}%", token=TOKEN, chat_id=uid)
    return user


def show_education(uid: str):
    """Показать образовательное меню."""
    import tg
    text = """
<b>📚 Образовательный центр</b>

Выбери раздел:
    """
    markup = ui_components.education_keyboard()
    tg.send_message(text.strip(), token=TOKEN, chat_id=uid, reply_markup=markup)


def show_glossary(uid: str):
    """Показать глоссарий."""
    import tg
    text = "<b>📖 Глоссарий терминов</b>\n\n"
    for term, definition in ui_components.GLOSSARY.items():
        text += f"<b>{term}</b>: {definition}\n\n"
    markup = ui_components.education_keyboard()
    tg.send_message(text.strip(), token=TOKEN, chat_id=uid, reply_markup=markup)


def show_lessons(uid: str):
    """Показать список уроков."""
    import tg
    text = "<b>🎓 Уроки</b>\n\nВыбери урок:"
    markup = ui_components.lessons_keyboard()
    tg.send_message(text.strip(), token=TOKEN, chat_id=uid, reply_markup=markup)


def show_lesson(callback_data: str, uid: str):
    """Показать урок."""
    lesson_id = int(callback_data.replace("lesson_", ""))
    lesson = ui_components.get_lesson(lesson_id)
    if lesson:
        import tg
        markup = ui_components.lessons_keyboard()
        tg.send_message(lesson["content"], token=TOKEN, chat_id=uid, reply_markup=markup)


def show_quiz_start(uid: str):
    """Показать начало викторины."""
    import tg
    text = f"""
<b>❓ Викторина</b>

Проверь свои знания! {ui_components.get_quiz_count()} вопросов.

Награда за все правильные ответы: +1 день к подписке!
    """
    markup = ui_components.quiz_keyboard()
    tg.send_message(text.strip(), token=TOKEN, chat_id=uid, reply_markup=markup)


# Состояние викторины (user_id -> {"current_question": 0, "score": 0})
quiz_state = {}


def start_quiz(uid: str):
    """Начать викторину."""
    global quiz_state
    quiz_state[uid] = {"current_question": 0, "score": 0}
    return show_quiz_question(uid, 0)


def show_quiz_question(uid: str, question_id: int):
    """Показать вопрос викторины."""
    question = ui_components.get_quiz_question(question_id)
    if not question:
        return finish_quiz(uid)

    import tg
    text = f"<b>Вопрос {question_id + 1}/{ui_components.get_quiz_count()}</b>\n\n{question['question']}"
    options = question["options"]
    markup = ui_components.quiz_answer_keyboard(question_id, options)
    tg.send_message(text, token=TOKEN, chat_id=uid, reply_markup=markup)


def handle_quiz_answer(callback_data: str, uid: str):
    """Обработать ответ на вопрос."""
    global quiz_state
    parts = callback_data.split("_")
    question_id = int(parts[2])
    answer_idx = int(parts[3])

    question = ui_components.get_quiz_question(question_id)
    if not question:
        return finish_quiz(uid)

    is_correct = answer_idx == question["correct"]
    if is_correct:
        quiz_state[uid]["score"] += 1

    import tg
    result = "✅ Правильно!" if is_correct else "❌ Неправильно"
    explanation = question["explanation"]
    tg.send_message(f"{result}\n\n{explanation}", token=TOKEN, chat_id=uid)

    quiz_state[uid]["current_question"] = question_id + 1
    return show_quiz_question(uid, question_id + 1)


def finish_quiz(uid: str):
    """Завершить викторину."""
    global quiz_state
    score = quiz_state.get(uid, {}).get("score", 0)
    total = ui_components.get_quiz_count()

    import tg
    if score == total:
        text = f"🎉 Поздравляем! Все ответы правильные!\n\n+1 день к подписке!"
        # Добавить день к подписке
        users = load_json(USERS_PATH)
        current_until = paid_until(users, uid)
        if current_until > 0:
            grant(users, uid, current_until + 86400)  # +1 день
            save_json(USERS_PATH, users)
    else:
        text = f"Результат: {score}/{total} правильных ответов.\n\nПопробуй ещё раз!"

    markup = ui_components.education_keyboard()
    tg.send_message(text, token=TOKEN, chat_id=uid, reply_markup=markup)

    if uid in quiz_state:
        del quiz_state[uid]


# Вспомогательные функции
def is_paid(users: dict, uid: str) -> bool:
    import time
    return float(users.get(uid, {}).get("paid_until", 0) or 0) > time.time()


def paid_until(users: dict, uid: str) -> float:
    import time
    return float(users.get(uid, {}).get("paid_until", 0) or 0)


def load_json(path: str) -> dict:
    import json
    import os
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path: str, data: dict):
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# Пути импортируются из subbot.py
USERS_PATH = None
TOKEN = None


def init(users_path: str, token: str):
    """Инициализация путей."""
    global USERS_PATH, TOKEN
    USERS_PATH = users_path
    TOKEN = token
