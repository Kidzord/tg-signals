# -*- coding: utf-8 -*-
"""Прогон RangeBotEA в тестере MT5 на независимом терминале Exness.

Не трогает основной терминал и живой флот: останавливает только
C:\\MT5-Exness\\terminal64.exe, запускает тестер, забирает CSV из общей
папки Files и поднимает Exness-терминал обратно.

    python mt5_ea/run_range_exness.py --symbol EURUSDm --tag 90d
    python mt5_ea/run_range_exness.py --symbol EURUSDm --from 2025.01.01 --to 2025.12.31 --tag 2025

EA не содержит тренд-фильтра, поэтому проверяется база er=0/tp=3.
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BASE = r"C:\MT5-Exness\terminal64.exe"
DATA = Path(r"C:\MT5-Exness")
EXPERTS = DATA / "MQL5" / "Experts" / "TgSignals"
METAEDITOR = DATA / "metaeditor64.exe"
COMMON = Path(os.environ["APPDATA"]) / "MetaQuotes" / "Terminal" / "Common" / "Files"

INPUTS = {
    "InpH4Bars": 1500, "InpFracL": 3, "InpFracR": 3, "InpTolMult": 0.20,
    "InpTouches": 2, "InpMinAge": 6, "InpBreakMult": 2.0, "InpFlipsKeep": 60,
    "InpFlipTol": 0.10, "InpErLen": 50, "InpErMax": 1.0, "InpUseSMT": "false",
    "InpSmtSymbol": "XAGUSDm", "InpSmtFor": "XAUUSDm", "InpSmtLook": 30,
    "InpMaxDailyStops": 0, "InpStopATR": 1.0, "InpTpR": 3.0,
    "InpTargetMinR": 1.0, "InpTargetMaxR": 5.0, "InpMaxBars": 120,
    "InpRiskPct": 0.5,
}


def compile_ea() -> Path:
    EXPERTS.mkdir(parents=True, exist_ok=True)
    src = HERE / "RangeBotEA.mq5"
    dst = EXPERTS / "RangeBotEA.mq5"
    shutil.copyfile(src, dst)
    log = EXPERTS / "compile.log"
    if log.exists():
        log.unlink()
    print("компиляция", dst, flush=True)
    subprocess.run([str(METAEDITOR), f"/compile:{dst}", f"/log:{log}"],
                   capture_output=True, timeout=180)
    ex5 = dst.with_suffix(".ex5")
    if not ex5.exists():
        text = log.read_text(encoding="utf-16", errors="replace") if log.exists() else ""
        raise SystemExit(f"EA не скомпилировался:\n{text[-2000:]}")
    return ex5


def stop_exness() -> None:
    for p in psutil.process_iter(["pid", "exe"]):
        try:
            if p.info["exe"] and p.info["exe"].lower() == BASE.lower():
                p.kill()
                print("остановлен Exness-терминал", p.pid, flush=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    for _ in range(20):
        alive = any(
            p.info["exe"] and p.info["exe"].lower() == BASE.lower()
            for p in psutil.process_iter(["pid", "exe"])
            if p.info.get("exe")
        )
        if not alive:
            return
        time.sleep(1)


def write_ini(symbol: str, date_from: str, date_to: str, tag: str) -> Path:
    path = DATA / f"tester_range_{tag}_{symbol}.ini"
    lines = [
        "[Tester]",
        r"Expert=TgSignals\RangeBotEA",
        f"Symbol={symbol}",
        "Period=M15",
        "Model=0",
        "Optimization=0",
        f"FromDate={date_from}",
        f"ToDate={date_to}",
        "ForwardMode=0",
        "Deposit=10000",
        "DepositCurrency=USD",
        "Leverage=100",
        "ExecutionMode=0",
        "ReplaceReport=1",
        "ShutdownTerminal=1",
        "Visual=0",
        "Port=3001",
    ]
    login = os.getenv("EXNESS_LOGIN", "").strip()
    password = os.getenv("EXNESS_PASSWORD", "")
    server = os.getenv("EXNESS_SERVER", "").strip()
    if login and password and server:
        lines = ["[Common]", f"Login={login}", f"Password={password}",
                 f"Server={server}", ""] + lines
    lines += ["", "[TesterInputs]"]
    lines += [f"{k}={v}" for k, v in INPUTS.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run(symbol: str, date_from: str, date_to: str, tag: str) -> Path | None:
    mql = f"range_ea_{symbol}_result.csv"
    src = COMMON / mql
    before = src.stat().st_mtime if src.exists() else 0.0
    ini = write_ini(symbol, date_from, date_to, tag)
    stop_exness()
    print(f"тестер {symbol} {date_from}–{date_to}", flush=True)
    subprocess.Popen([BASE, "/config:" + str(ini)], close_fds=True)
    deadline = time.time() + 1800
    seen_alive = False
    gone_since = None
    while time.time() < deadline:
        alive = any(
            p.info["exe"] and p.info["exe"].lower() == BASE.lower()
            for p in psutil.process_iter(["pid", "exe"]) if p.info.get("exe")
        )
        seen_alive = seen_alive or alive
        fresh = src.exists() and src.stat().st_mtime > before
        if fresh and not alive:
            break
        if not alive:
            gone_since = gone_since or time.time()
            if time.time() - gone_since > 30:
                print("терминал завершился без свежего CSV", flush=True)
                break
        else:
            gone_since = None
        time.sleep(5)
    time.sleep(2)
    if not (src.exists() and src.stat().st_mtime > before):
        print("CSV не обновился", flush=True)
        return None
    dst = HERE / f"range_{symbol}_{tag}.csv"
    shutil.copyfile(src, dst)
    rows = io.open(dst, encoding="latin-1").read().splitlines()
    print(" | ".join(rows[:3]), flush=True)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSDm")
    ap.add_argument("--from", dest="date_from", default="2026.07.28")
    ap.add_argument("--to", dest="date_to", default="2026.10.24")
    ap.add_argument("--tag", default="90d")
    ap.add_argument("--er", type=float, default=1.0, help="ER-порог; 1.0 = фильтр выключен")
    args = ap.parse_args()
    INPUTS["InpErMax"] = args.er
    try:
        compile_ea()
        out = run(args.symbol, args.date_from, args.date_to, args.tag)
    finally:
        for p in DATA.glob("tester_range_*.ini"):
            p.unlink(missing_ok=True)
        subprocess.Popen([BASE, "/portable"], close_fds=True)
        print("Exness-терминал поднят", flush=True)
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
