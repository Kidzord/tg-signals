"""Мониторинг: боты живы, доставка не встала, внешний dead-man switch.

Запуск:  python monitor.py          — цикл каждые MONITOR_EVERY_MIN минут
         python monitor.py once     — одна проверка (без отправки алертов)

Что проверяется:
  * heartbeat-файлы bot.py (hb_<СИМВОЛ>_<СТРАТЕГИЯ>.json) не старше
    MONITOR_STALE_MIN минут;
  * число процессов bot.py не меньше HEARTBEAT_EXPECT_BOTS;
  * очередь доставки: есть ли pending старше 30 минут или failed-строки;
  * если задан HEALTHCHECK_URL — пинг внешнего сервиса (healthchecks.io).
    Это единственный способ узнать, что упал весь ПК: локальный алерт
    вместе с ПК и умрёт.

Алерты шлются владельцу (OWNER_CHAT_ID) только на смену состояния,
чтобы не спамить каждые 15 минут.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg
import store
import tg

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "monitor_state.json")
# pending дольше этого — считаем, что доставка встала
BACKLOG_STALE_SEC = 30 * 60


def _load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def _alert(text: str) -> bool:
    if not cfg.OWNER_CHAT_ID:
        return True   # некого уведомлять — считаем доставленным
    try:
        ok, _ = tg.send_message(text, token=cfg.SUB_BOT_TOKEN or cfg.BOT_TOKEN,
                                chat_id=cfg.OWNER_CHAT_ID)
        return bool(ok)
    except Exception as e:
        print("алерт не отправлен:", repr(e))
        return False


def _transition(key: str, bad: bool, bad_text: str, ok_text: str, state: dict) -> None:
    """Отправить алерт только при смене состояния и не терять его при сбое Telegram."""
    prev = bool(state.get(key))
    if bad != prev:
        if not _alert(bad_text if bad else ok_text):
            return   # state не меняем — повторим в следующем цикле
    state[key] = bad


def check_heartbeats() -> tuple[bool, str]:
    """Свежесть hb-файлов и число процессов bot.py."""
    now = time.time()
    files = glob.glob(os.path.join(HERE, "hb_*.json"))
    stale: list[str] = []
    failed: list[str] = []
    for path in files:
        try:
            age_min = (now - os.path.getmtime(path)) / 60.0
        except OSError:
            continue
        name = os.path.basename(path)
        if age_min > cfg.MONITOR_STALE_MIN:
            stale.append(f"{name} ({age_min:.0f} мин)")
            continue
        try:
            with open(path, encoding="utf-8") as f:
                hb = json.load(f)
            if hb.get("ok") is False:
                failed.append(name)
        except (OSError, ValueError):
            pass
    # число процессов — второй сигнал: файл мог остаться от прошлого запуска
    procs = -1
    try:
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -match '(?<![a-z])bot\\.py' }).Count"],
            capture_output=True, timeout=30)
        m = re.search(r"\d+", ps.stdout.decode("utf-8", errors="ignore"))
        procs = int(m.group()) if m else -1
    except Exception:
        pass
    problems: list[str] = []
    if stale:
        problems.append("нет пульса: " + ", ".join(stale))
    if failed:
        problems.append("ошибка шага: " + ", ".join(failed))
    if procs >= 0 and procs < cfg.EXPECTED_BOTS:
        problems.append(f"bot.py процессов {procs} из {cfg.EXPECTED_BOTS}")
    if not files and procs == 0:
        problems.append("ни одного bot.py и ни одного heartbeat")
    elif not files and procs < 0:
        problems.append("нет heartbeat-файлов, процессы проверить не удалось")
    return (not problems), ("; ".join(problems) or "все боты живы")


def check_delivery() -> tuple[bool, str]:
    stats = store.delivery_stats()
    pending = stats.get("pending", {})
    problems: list[str] = []
    oldest = pending.get("oldest")
    min_due = pending.get("min_due")
    now = time.time()
    # ближайшая попытка запланирована в будущем — очередь ждёт (429/backoff), не стоит
    waiting = min_due is not None and float(min_due) > now
    if oldest and not waiting:
        age = now - float(oldest)
        if age > BACKLOG_STALE_SEC:
            problems.append(f"очередь доставки стоит {age / 60:.0f} мин")
    failed = store.delivery_failed_recent(now - 86400)
    if failed:
        problems.append(f"недоставленных сообщений за сутки: {failed}")
    group_pending = store.group_pending_count()
    if group_pending > 20:
        problems.append(f"в групповой очереди {group_pending} постов")
    return (not problems), ("; ".join(problems) or "доставка в порядке")


def check_backup(max_age_h: float = 26.0) -> tuple[bool, str]:
    import backup
    last = backup.latest_snapshot()
    if not last:
        return False, "нет ни одного бэкапа"
    try:
        age_h = (time.time() - os.path.getmtime(last)) / 3600.0
    except OSError:
        return False, "бэкап не читается"
    if age_h > max_age_h:
        return False, f"последний бэкап {age_h:.0f} ч назад"
    return True, "бэкап свежий"


def check_logs(max_mb: float = 50.0) -> tuple[bool, str]:
    big: list[str] = []
    for path in glob.glob(os.path.join(HERE, "logs", "*.log")):
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if size > max_mb * 1024 * 1024:
            big.append(f"{os.path.basename(path)} {size / 1048576:.0f} МБ")
    return (not big), ("логи: " + ", ".join(big) if big else "логи в норме")


def ping_healthcheck() -> bool:
    """Пинг внешнего dead-man switch (если настроен)."""
    url = cfg.HEALTHCHECK_URL
    if not url:
        return True
    try:
        r = requests.get(url, timeout=10)
        return r.status_code < 400
    except requests.RequestException as e:
        print("healthcheck ping:", e)
        return False


def set_healthcheck(url: str) -> bool:
    """Записать HEALTHCHECK_URL в .env и проверить пингом."""
    url = (url or "").strip()
    if not url.startswith("http"):
        print("URL должен начинаться с http:// или https://")
        return False
    env_path = os.path.join(HERE, ".env")
    if not os.path.exists(env_path):
        print("нет .env рядом с monitor.py")
        return False
    lines = open(env_path, encoding="utf-8").read().splitlines()
    out, found = [], False
    for line in lines:
        if line.startswith("HEALTHCHECK_URL="):
            out.append(f"HEALTHCHECK_URL={url}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"HEALTHCHECK_URL={url}")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip() + "\n")
    try:
        r = requests.get(url, timeout=10)
        print("проверка URL:", r.status_code, r.text[:80])
    except requests.RequestException as e:
        print("URL записан, но проверка не прошла:", e)
    print("HEALTHCHECK_URL записан в .env — перезапусти monitor.bat")
    return True


def run_once(send_alerts: bool = True) -> dict:
    store.init_db()
    state = _load_state()
    ok_b, text_b = check_heartbeats()
    ok_d, text_d = check_delivery()
    ok_bk, text_bk = check_backup()
    ok_lg, text_lg = check_logs()
    if send_alerts:
        _transition("bots", not ok_b,
                    f"🔴 <b>Боты сигналов</b>\n{text_b}",
                    "🟢 <b>Боты сигналов</b> снова на связи.", state)
        _transition("delivery", not ok_d,
                    f"🔴 <b>Доставка</b>\n{text_d}",
                    "🟢 <b>Доставка</b> восстановлена.", state)
        _transition("backup", not ok_bk,
                    f"🔴 <b>Бэкапы</b>\n{text_bk}",
                    "🟢 <b>Бэкапы</b> снова в порядке.", state)
        _transition("logs", not ok_lg,
                    f"🔴 <b>Логи</b>\n{text_lg}",
                    "🟢 <b>Логи</b> снова в норме.", state)
        _save_state(state)
    ping_healthcheck()
    return {"bots": ok_b, "bots_text": text_b,
            "delivery": ok_d, "delivery_text": text_d,
            "backup": ok_bk, "backup_text": text_bk,
            "logs": ok_lg, "logs_text": text_lg}


def main() -> None:
    if len(sys.argv) > 2 and sys.argv[1] == "set-healthcheck":
        set_healthcheck(sys.argv[2])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        res = run_once(send_alerts=False)
        print("боты:", "ok" if res["bots"] else "ПРОБЛЕМА", "-", res["bots_text"])
        print("доставка:", "ok" if res["delivery"] else "ПРОБЛЕМА", "-", res["delivery_text"])
        print("бэкапы:", "ok" if res["backup"] else "ПРОБЛЕМА", "-", res["backup_text"])
        print("логи:", "ok" if res["logs"] else "ПРОБЛЕМА", "-", res["logs_text"])
        return
    print(f"monitor: каждые {cfg.MONITOR_EVERY_MIN} мин, "
          f"порог пульса {cfg.MONITOR_STALE_MIN} мин, "
          f"healthcheck {'вкл' if cfg.HEALTHCHECK_URL else 'выкл'}")
    if not cfg.HEALTHCHECK_URL:
        print("внешний dead-man выключен: создай check на healthchecks.io и выполни "
              "python monitor.py set-healthcheck https://hc-ping.com/<uuid>")
    while True:
        try:
            run_once()
        except Exception as e:
            print("ошибка монитора:", repr(e))
        time.sleep(max(60, cfg.MONITOR_EVERY_MIN * 60))


if __name__ == "__main__":
    main()
