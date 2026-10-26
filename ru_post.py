"""Ручной композер постов для VK и Дзена (RU).

Берём тот же банк текстов, что и Threads (threads_post.POSTS), но ведём
отдельные списки «опубликовано» для каждой площадки — один и тот же текст
можно разместить и в Threads, и в VK, и в Дзене.

Ни у VK, ни у Дзена нет ссылки с уже подставленным текстом, поэтому скрипт
копирует пост в буфер обмена и открывает нужную страницу — остаётся вставить
текст (Ctrl+V) и нажать «Опубликовать».

Запуск:
    python ru_post.py                 # список (общий банк с Threads)
    python ru_post.py vk              # следующий неопубликованный пост для VK
    python ru_post.py vk 5            # взять пост №5 для VK
    python ru_post.py dzen next       # следующий пост для Дзена
    python ru_post.py dzen done 5     # пометить №5 опубликованным в Дзене

Карточки общие с Threads (threads_img/NN.png, рисует threads_img.py).
"""
from __future__ import annotations

import json
import os
import sys
import webbrowser
from pathlib import Path

import threads_post

HERE = Path(__file__).resolve().parent
IMG_DIR = HERE / "threads_img"

NETS: dict[str, dict] = {
    "vk": {"title": "VK", "url": "https://vk.com/feed", "used": HERE / "vk_used.json"},
    "dzen": {"title": "Дзен", "url": "https://dzen.ru/", "used": HERE / "dzen_used.json"},
}

POSTS = threads_post.POSTS


def _load_used(net: str) -> set:
    path = NETS[net]["used"]
    if path.exists():
        try:
            return set(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def _save_used(net: str, used: set) -> None:
    NETS[net]["used"].write_text(json.dumps(sorted(used)), encoding="utf-8")


def next_unused(net: str) -> int | None:
    """Номер первого неопубликованного поста для площадки. None — банк исчерпан."""
    used = _load_used(net)
    for i in range(1, len(POSTS) + 1):
        if i not in used:
            return i
    return None


def mark_used(net: str, n: int) -> None:
    """Отметить пост опубликованным в этой площадке."""
    used = _load_used(net)
    used.add(n)
    _save_used(net, used)


def show() -> None:
    marks = {net: _load_used(net) for net in NETS}
    head = "  ".join(f"{NETS[net]['title']:>4}" for net in NETS)
    print(f" №   {head}   пост")
    for i, p in enumerate(POSTS, 1):
        row = "  ".join((" ✓  " if i in marks[net] else " ·  ") for net in NETS)
        print(f"{i:>3}  {row}  {p['title']}")


def pick(net: str, n: int) -> None:
    if net not in NETS:
        print(f"Площадка {net!r}. Доступно: {', '.join(NETS)}")
        return
    if not 1 <= n <= len(POSTS):
        print(f"Нет поста №{n}. Всего {len(POSTS)}.")
        return
    info = NETS[net]
    post = POSTS[n - 1]
    text = post["text"]

    # Помечаем сразу: если подведёт буфер или браузер, пост всё равно не потеряется.
    mark_used(net, n)

    ok, how = threads_post._clip(text)
    if ok:
        print(f"Текст скопирован в буфер обмена ({how}).")
    else:
        print(f"Не удалось скопировать автоматически: {how}")
        print("Текст ниже — скопируй вручную.")

    try:
        opened = webbrowser.open(info["url"])
    except Exception as e:
        opened = False
        print("Браузер не открылся:", e)
    if not opened:
        print(f"Открой {info['title']} вручную ({info['url']}) и вставь текст из буфера.")

    img = IMG_DIR / f"{n:02d}.png"
    if img.exists():
        print(f"Картинка: {img}")
        try:
            os.startfile(str(img))      # Windows: открываем, чтобы прикрепить к посту
            print(f"Картинка открыта — прикрепи её к посту в {info['title']}.")
        except OSError:
            pass
    else:
        print(f"Картинки нет. Отрисуй: python threads_img.py {n}")

    print(f"\n{info['title']} · №{n} · {post['title']}")
    print("-" * 60)
    print(text)
    print("-" * 60)
    print(f"Вставь текст в {info['title']} (Ctrl+V) и нажми «Опубликовать».")


def main() -> None:
    args = list(sys.argv[1:])

    if not args:
        show()
        print("\nПлощадка и номер (например «vk 3», Enter — выйти): ", end="")
        try:
            raw = input().split()
        except EOFError:
            return
        if len(raw) == 2 and raw[0].lower() in NETS and raw[1].isdigit():
            pick(raw[0].lower(), int(raw[1]))
        return

    net = args[0].lower()
    if net not in NETS:
        print(f"Площадка {net!r}. Доступно: {', '.join(NETS)}")
        return

    rest = args[1:]
    if rest and rest[0] == "done":
        if len(rest) < 2 or not rest[1].isdigit():
            print(f"Формат: python ru_post.py {net} done <номер>")
            return
        mark_used(net, int(rest[1]))
        print(f"Пост №{rest[1]} помечен опубликованным в {NETS[net]['title']}.")
        return

    if not rest or rest[0] == "next":
        n = next_unused(net)
        if n is None:
            print(f"Все посты из банка использованы в {NETS[net]['title']}.")
            return
        pick(net, n)
        return

    if rest[0].isdigit():
        pick(net, int(rest[0]))
        return

    print(f"Формат: python ru_post.py {net} [номер|next|done <номер>]")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
