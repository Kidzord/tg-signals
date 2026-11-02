"""Скриншот терминала MT5 через советник ScreenshotEA.mq5.

Python-пакет MetaTrader5 не умеет ChartScreenShot, поэтому общаемся с
советником через файлы в MQL5/Files:

* Python пишет kz_screenshot_request.txt (symbol, tf, уровни, out);
* советник открывает временный график, рисует линии, делает PNG;
* Python читает kz_screenshot_done.txt и копирует картинку в renders/.

Если советник не запущен (нет свежего heartbeat), request() сразу
возвращает None — бот рисует свою карточку и не ждёт таймаут.
"""
from __future__ import annotations

import os
import shutil
import time

try:
    import msvcrt
except ImportError:  # не Windows — на CI тесты не доходят до lock
    msvcrt = None
try:
    import fcntl
except ImportError:
    fcntl = None

HERE = os.path.dirname(os.path.abspath(__file__))
RENDERS = os.path.join(HERE, "renders")
LOCK_PATH = os.path.join(HERE, "mt5_screenshot.lock")
REQ = "kz_screenshot_request.txt"
DONE = "kz_screenshot_done.txt"
HB = "kz_screenshot_heartbeat.txt"
MAX_AGE_SEC = 60

_TF = {
    "1": "M1", "5": "M5", "15": "M15", "30": "M30", "60": "H1", "240": "H4",
    "M1": "M1", "M5": "M5", "M15": "M15", "M30": "M30",
    "H1": "H1", "H4": "H4", "1D": "D1", "D1": "D1", "1W": "W1", "W1": "W1",
}


def tf_mql(tf: str) -> str:
    return _TF.get(str(tf).strip().upper(), "M15")


def files_dir(data_path: str) -> str:
    return os.path.join(data_path, "MQL5", "Files")


def heartbeat_age(data_path: str) -> float | None:
    if not data_path:
        return None
    try:
        return time.time() - os.path.getmtime(os.path.join(files_dir(data_path), HB))
    except OSError:
        return None


def available(data_path: str, max_age: float = MAX_AGE_SEC) -> bool:
    age = heartbeat_age(data_path)
    return age is not None and age <= max_age


def _parse_done(path: str) -> dict:
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _num(v: float | None) -> str:
    if v is None:
        return ""
    return f"{float(v):.5f}".rstrip("0").rstrip(".")


def _acquire_lock(timeout: float = 20.0) -> int | None:
    """Один запрос за раз: три bot.py могут закрыть сделки одновременно."""
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR)
    if msvcrt is not None:
        deadline = time.time() + timeout
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return fd
            except OSError:
                if time.time() >= deadline:
                    os.close(fd)
                    return None
                time.sleep(0.15)
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _release_lock(fd: int | None) -> None:
    if fd is None:
        return
    try:
        if msvcrt is not None:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        elif fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def request(symbol: str, tf: str, *, data_path: str,
            side: str = "LONG", entry: float | None = None,
            stop: float | None = None, tp1: float | None = None,
            tp2: float | None = None, tp3: float | None = None,
            exit_px: float | None = None, label: str = "", r: float = 0.0,
            entry_ts: float | None = None, exit_ts: float | None = None,
            width: int = 1280, height: int = 720,
            timeout: float = 15.0) -> str | None:
    """Попросить советник сделать PNG. None — советник не запущен/ошибка."""
    if not available(data_path):
        return None
    folder = files_dir(data_path)
    if not os.path.isdir(folder):
        return None

    lock = _acquire_lock(min(25.0, timeout + 5.0))
    if lock is None:
        print("screenshot EA: не дождались очереди на скриншот")
        return None
    try:
        return _request_locked(
            symbol, tf, folder=folder, side=side, entry=entry, stop=stop,
            tp1=tp1, tp2=tp2, tp3=tp3, exit_px=exit_px, label=label, r=r,
            entry_ts=entry_ts, exit_ts=exit_ts,
            width=width, height=height, timeout=timeout)
    finally:
        _release_lock(lock)


def _request_locked(symbol: str, tf: str, *, folder: str,
                    side: str, entry: float | None, stop: float | None,
                    tp1: float | None, tp2: float | None, tp3: float | None,
                    exit_px: float | None, label: str, r: float,
                    entry_ts: float | None, exit_ts: float | None,
                    width: int, height: int, timeout: float) -> str | None:
    out_name = f"kz_shot_{symbol}_{int(time.time() * 1000)}.png"
    req_path = os.path.join(folder, REQ)
    done_path = os.path.join(folder, DONE)
    for path in (req_path, done_path):
        try:
            os.remove(path)
        except OSError:
            pass

    lines = [
        f"symbol={symbol}", f"tf={tf_mql(tf)}", f"out={out_name}",
        f"width={int(width)}", f"height={int(height)}", f"side={side}",
        f"entry={_num(entry)}", f"stop={_num(stop)}",
        f"tp1={_num(tp1)}", f"tp2={_num(tp2)}", f"tp3={_num(tp3)}",
        f"exit={_num(exit_px)}", f"r={float(r):.4f}", f"label={label}",
        f"entry_ts={int(entry_ts) if entry_ts else 0}",
        f"exit_ts={int(exit_ts) if exit_ts else 0}",
    ]
    tmp = req_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, req_path)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(done_path):
            data = _parse_done(done_path)
            if data.get("status") == "ok" and data.get("out"):
                src = os.path.join(folder, data["out"])
                if os.path.exists(src):
                    os.makedirs(RENDERS, exist_ok=True)
                    dst = os.path.join(RENDERS, os.path.basename(src))
                    shutil.copy2(src, dst)
                    return dst
            print("screenshot EA:", data.get("error", "неизвестная ошибка"))
            return None
        time.sleep(0.2)
    print("screenshot EA: таймаут ожидания PNG")
    return None


def check(data_path: str | None = None) -> dict:
    """Проверить, запущен ли советник и свежий ли у него heartbeat."""
    if not data_path:
        try:
            import MetaTrader5 as mt5
        except ImportError:
            return {"ok": False, "reason": "пакет MetaTrader5 не установлен"}
        if not mt5.initialize():
            return {"ok": False, "reason": f"MT5 не отвечает: {mt5.last_error()}"}
        info = mt5.terminal_info()
        data_path = info.data_path if info else ""
        mt5.shutdown()
    age = heartbeat_age(data_path)
    return {
        "ok": age is not None and age <= MAX_AGE_SEC,
        "data_path": data_path,
        "heartbeat_age": age,
        "files_dir": files_dir(data_path) if data_path else "",
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        info = check()
        print(info)
        path = request("XAUUSD", "15", data_path=info.get("data_path", ""),
                       side="LONG", entry=4000.0, stop=3990.0,
                       tp1=4010.0, tp2=4020.0, tp3=4030.0,
                       exit_px=4020.0, label="TP2", r=2.0,
                       timeout=20)
        print("screenshot:", path or "не получен")
    else:
        print(check())
