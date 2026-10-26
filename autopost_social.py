"""Автопостинг в Threads и X по расписанию.

Публикует готовый контент из threads_img/ и x_post.py по расписанию.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import config as cfg
import logger_config
import threads
import x_post

log = logger_config.get_logger("autopost_social")

IMG_DIR = Path(__file__).parent / "threads_img"
USED_PATH = Path(__file__).parent / "threads_used.json"
SCHEDULE_PATH = Path(__file__).parent / "social_schedule.json"


def load_schedule() -> dict:
    """Загрузить расписание."""
    try:
        with open(SCHEDULE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        # Расписание по умолчанию
        return {
            "threads": {
                "enabled": False,
                "times": ["09:00", "15:00", "21:00"],  # UTC
                "timezone": "UTC",
            },
            "x": {
                "enabled": False,
                "times": ["10:00", "16:00", "22:00"],  # UTC
                "timezone": "UTC",
            },
        }


def save_schedule(schedule: dict):
    """Сохранить расписание."""
    with open(SCHEDULE_PATH, "w", encoding="utf-8") as f:
        json.dump(schedule, f, ensure_ascii=False, indent=2)


def get_next_threads_post() -> int | None:
    """Получить номер следующего поста для Threads."""
    used = set()
    if USED_PATH.exists():
        try:
            used = set(json.loads(USED_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass

    # Ищем первый неиспользованный пост
    for i in range(1, 29):  # у тебя 28 картинок
        if i not in used:
            return i
    return None


def post_to_threads(post_num: int) -> bool:
    """Опубликовать пост в Threads."""
    img_path = IMG_DIR / f"{post_num:02d}.png"
    if not img_path.exists():
        log.error(f"Картинка не найдена: {img_path}")
        return False

    # Текст поста из x_post.py
    if post_num <= len(x_post.POSTS):
        post = x_post.POSTS[post_num - 1]
        text = post["text"]
    else:
        text = f"Trade #{post_num}\n\nSignals in Telegram"

    # Публикуем
    ok, info = threads.post(text, image_url=None)  # Threads пока без URL картинок
    if ok:
        # Помечаем как использованный
        used = set()
        if USED_PATH.exists():
            try:
                used = set(json.loads(USED_PATH.read_text(encoding="utf-8")))
            except Exception:
                pass
        used.add(post_num)
        USED_PATH.write_text(json.dumps(sorted(used)), encoding="utf-8")
        log.info(f"Threads: пост #{post_num} опубликован ({info})")
        return True
    else:
        log.error(f"Threads: ошибка публикации поста #{post_num}: {info}")
        return False


def post_to_x(post_num: int) -> bool:
    """Опубликовать пост в X (через буфер обмена)."""
    # Это ручной режим - скопируем в буфер и открываем X
    used = set()
    if USED_PATH.exists():
        try:
            used = set(json.loads(USED_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass

    if post_num not in used:
        x_post.pick(post_num, used)
        log.info(f"X: пост #{post_num} подготовлен для публикации")
        return True
    return False


def check_and_post_threads():
    """Проверить и опубликовать в Threads если пришло время."""
    schedule = load_schedule()
    threads_config = schedule.get("threads", {})

    if not threads_config.get("enabled", False):
        return

    tz = threads_config.get("timezone", "UTC")
    times = threads_config.get("times", [])

    # Получаем текущее время в нужном часовом поясе
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(tz))
    except:
        now = datetime.utcnow()

    current_time = now.strftime("%H:%M")

    if current_time in times:
        post_num = get_next_threads_post()
        if post_num:
            post_to_threads(post_num)
        else:
            log.warning("Threads: все посты использованы")


def check_and_post_x():
    """Проверить и опубликовать в X если пришло время."""
    schedule = load_schedule()
    x_config = schedule.get("x", {})

    if not x_config.get("enabled", False):
        return

    tz = x_config.get("timezone", "UTC")
    times = x_config.get("times", [])

    # Получаем текущее время в нужном часовом поясе
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(tz))
    except:
        now = datetime.utcnow()

    current_time = now.strftime("%H:%M")

    if current_time in times:
        post_num = get_next_threads_post()
        if post_num:
            post_to_x(post_num)
        else:
            log.warning("X: все посты использованы")


def show_status():
    """Показать статус автопостинга."""
    schedule = load_schedule()

    print("=== Статус автопостинга ===")
    print(f"\nThreads:")
    print(f"  Включен: {schedule.get('threads', {}).get('enabled', False)}")
    print(f"  Время: {schedule.get('threads', {}).get('times', [])}")
    print(f"  Часовой пояс: {schedule.get('threads', {}).get('timezone', 'UTC')}")
    print(f"  Осталось постов: {28 - len(json.loads(USED_PATH.read_text()) if USED_PATH.exists() else [])}")

    print(f"\nX:")
    print(f"  Включен: {schedule.get('x', {}).get('enabled', False)}")
    print(f"  Время: {schedule.get('x', {}).get('times', [])}")
    print(f"  Часовой пояс: {schedule.get('x', {}).get('timezone', 'UTC')}")


def enable_threads(times: list[str], timezone: str = "UTC"):
    """Включить автопостинг в Threads."""
    schedule = load_schedule()
    schedule["threads"] = {
        "enabled": True,
        "times": times,
        "timezone": timezone,
    }
    save_schedule(schedule)
    print(f"Threads автопостинг включён: {times} {timezone}")


def enable_x(times: list[str], timezone: str = "UTC"):
    """Включить автопостинг в X."""
    schedule = load_schedule()
    schedule["x"] = {
        "enabled": True,
        "times": times,
        "timezone": timezone,
    }
    save_schedule(schedule)
    print(f"X автопостинг включён: {times} {timezone}")


def disable_threads():
    """Отключить автопостинг в Threads."""
    schedule = load_schedule()
    schedule["threads"]["enabled"] = False
    save_schedule(schedule)
    print("Threads автопостинг отключён")


def disable_x():
    """Отключить автопостинг в X."""
    schedule = load_schedule()
    schedule["x"]["enabled"] = False
    save_schedule(schedule)
    print("X автопостинг отключён")


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        show_status()
    elif cmd == "enable-threads":
        times = sys.argv[2].split(",") if len(sys.argv) > 2 else ["09:00", "15:00", "21:00"]
        tz = sys.argv[3] if len(sys.argv) > 3 else "UTC"
        enable_threads(times, tz)
    elif cmd == "enable-x":
        times = sys.argv[2].split(",") if len(sys.argv) > 2 else ["10:00", "16:00", "22:00"]
        tz = sys.argv[3] if len(sys.argv) > 3 else "UTC"
        enable_x(times, tz)
    elif cmd == "disable-threads":
        disable_threads()
    elif cmd == "disable-x":
        disable_x()
    elif cmd == "check":
        check_and_post_threads()
        check_and_post_x()
    else:
        print("Команды:")
        print("  python autopost_social.py status")
        print("  python autopost_social.py enable-threads 09:00,15:00,21:00 UTC")
        print("  python autopost_social.py enable-x 10:00,16:00,22:00 UTC")
        print("  python autopost_social.py disable-threads")
        print("  python autopost_social.py disable-x")
        print("  python autopost_social.py check")
