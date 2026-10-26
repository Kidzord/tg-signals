"""Прямая отправка напоминания о посте (картинка + текст)."""
import json
import subprocess
from pathlib import Path

# Конфигурация
TG_BOT_TOKEN = "8957215002:AAGHuG07sM9CCBZTBEnb7wmMWa21tpQUZlU"
OWNER_CHAT_ID = "36685705"

# Русские тексты для Threads (первые 5)
POSTS = {
    1: "13 стопов за неделю. Всё равно в плюсе.\nМоя цель — 3x от стопа: одна прибыльная сделка покрывает три убыточные.\nЯ считаю R, а не винрейт. Скучно — поэтому это работает.\nПолный поток -> Telegram, ссылка в описании.",
    2: "Стоп — это не ошибка. Отсутствие стопа — ошибка.\nЯ рискую 0.5% на сделку, так что 5 стопов подряд — это -2.5%, нормальный день, а не катастрофа.\nМатематика бьёт нервы.",
    3: "Я не гонюсь за трендами.\nНа пробое шум съедает мой стоп, а цель так и не доходит.\nМне нужен рендж: цена сносит уровень и возвращается.\nСкучно — но правила заданы заранее.",
    4: "Мой риск на сделку: 0.5%.\nВыглядит как трусость. Это математика: 10 стопов подряд — это -5%, не конец депозита.\nБольшие деньги делаются не за счёт большого риска — а за счёт того, чтобы не слить.",
    5: "Если цена сносит старый минимум и резко возвращается выше — это не пробой.\nЭто охота на стопы: все с минусом под минимумом вышли.\nЗатем цена бежит к противоположному краю. Это мой лонг.",
}

# Загрузка used.json
USED_PATH = Path(__file__).parent / "threads_used.json"
used = set()
if USED_PATH.exists():
    try:
        used = set(json.loads(USED_PATH.read_text(encoding="utf-8")))
    except:
        pass

# Следующий пост
for i in range(1, 29):
    if i not in used:
        post_num = i
        break
else:
    post_num = 1

# Текст поста
post_text = POSTS.get(post_num, f"Текст поста #{post_num}")

# Путь к картинке
img_path = Path(__file__).parent / "threads_img" / f"{post_num:02d}.png"

# Текст для подписи (короткий)
caption = f"📱 Памятка: пост для Threads\n\nПост #{post_num}"

# Текст поста
message = f"""📝 Текст поста:
{post_text}

Открой https://www.threads.net/ и опубликуй!
""".strip()

print(f"Пост #{post_num}")
print(f"Картинка: {img_path}")
print(f"Текст:\n{post_text}\n")

# Отправляем через curl
# Сначала картинку
if img_path.exists():
    curl_cmd = [
        "curl", "-X", "POST",
        f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto",
        "-F", f"chat_id={OWNER_CHAT_ID}",
        "-F", f"photo=@{img_path}",
        "-F", f"caption={caption}"
    ]
    try:
        result = subprocess.run(curl_cmd, capture_output=True, text=True)
        print(f"Картинка: {result.stdout}")
    except Exception as e:
        print(f"Ошибка картинки: {e}")

# Затем текст
curl_cmd_text = [
    "curl", "-X", "POST",
    f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
    "-F", f"chat_id={OWNER_CHAT_ID}",
    "-F", f"text={message}"
]
try:
    result = subprocess.run(curl_cmd_text, capture_output=True, text=True)
    print(f"Текст: {result.stdout}")
except Exception as e:
    print(f"Ошибка текста: {e}")
