"""OCR картинок для Discord-приёма: скриншот TradingView → текст.

Бэкенды по предпочтению (первый доступный):

1. ``rapidocr_onnxruntime`` — ставится через pip, без внешнего бинарника;
2. ``pytesseract`` + установленный ``tesseract``;
3. ничего — модуль молча возвращает пустую строку.

Отсутствие OCR не ошибка: текст сообщения разбирается и без него, а картинка
попадает в очередь на ручную проверку (discord_review.jsonl). Поэтому все
исключения здесь глушатся — приём сигналов не должен падать из-за OCR.
"""
from __future__ import annotations

import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Изолированная папка для OCR-стека: ставится через
#   python -m pip install --target .ocr_vendor rapidocr-onnxruntime
# и не трогает общий userbase (там numpy, которым пользуется MT5-часть).
_VENDOR = os.path.join(HERE, ".ocr_vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    # именно append: штатный numpy из окружения должен оставаться главным,
    # иначе OCR-сборка с другой ABI может подменить его всему процессу
    sys.path.append(_VENDOR)

MIN_SCORE = float(os.getenv("OCR_MIN_SCORE", "0.5"))
LANG = os.getenv("OCR_LANG", "eng").strip() or "eng"

_engine = None
_backend: str | None = None


def backend() -> str:
    """Какой OCR доступен: rapidocr / tesseract / '' (нет)."""
    global _backend
    if _backend is None:
        try:
            import rapidocr_onnxruntime  # noqa: F401
            _backend = "rapidocr"
        except Exception:               # noqa: BLE001 — нет пакета или чужая ABI
            try:
                import pytesseract  # noqa: F401
                _backend = "tesseract" if shutil.which("tesseract") else ""
            except Exception:           # noqa: BLE001
                _backend = ""
    return _backend


def image_text(path: str) -> str:
    """Текст с картинки одной строкой. Пусто — OCR недоступен или не распознал."""
    try:
        name = backend()
        if name == "rapidocr":
            return _rapidocr(path)
        if name == "tesseract":
            return _tesseract(path)
    except Exception:                      # noqa: BLE001 — fail-open, см. docstring
        return ""
    return ""


def _rapidocr(path: str) -> str:
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    out = _engine(path)
    parts: list[str] = []
    rows = out[0] if isinstance(out, (tuple, list)) and out else None
    if isinstance(rows, list):
        for row in rows:
            # строка вида [box, text, score]; в некоторых версиях — (text, score)
            text, score = "", 0.0
            if isinstance(row, (list, tuple)) and len(row) >= 3:
                text, score = str(row[1]), float(row[2])
            elif isinstance(row, (list, tuple)) and len(row) == 2:
                text, score = str(row[0]), float(row[1])
            if text and score >= MIN_SCORE:
                parts.append(text)
    return " ".join(parts)


def _tesseract(path: str) -> str:
    import pytesseract
    from PIL import Image

    with Image.open(path) as img:
        return pytesseract.image_to_string(img, lang=LANG).strip()


if __name__ == "__main__":
    import sys

    print(f"бэкенд: {backend() or 'нет (pip install rapidocr-onnxruntime)'}")
    for arg in sys.argv[1:]:
        print(f"--- {arg}")
        print(image_text(arg))
