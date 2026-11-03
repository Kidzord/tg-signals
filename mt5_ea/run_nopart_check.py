# -*- coding: utf-8 -*-
"""Прогон KidzordEA в тестере MT5: live-база и вариант без частичных фиксаций.

Терминал должен быть закрыт: тестер запускает свой экземпляр с /config и
ShutdownTerminal=1. После прогонов скрипт поднимает обычный терминал обратно.
"""
import io
import os
import shutil
import subprocess
import time

MT5_DIR = r"C:\Users\admin\Downloads\tg-signals\mt5_ea"
BASE = r"C:\Program Files\MetaTrader 5\terminal64.exe"
SRC = os.path.join(os.environ["APPDATA"],
                   r"MetaQuotes\Terminal\Common\Files\kidzord_ea_XAUUSD_result.csv")

DEFAULTS = {
    "InpSlopeLen": "20", "InpMinSlope": "0.01", "InpUseDayCtx": "true",
    "InpTolHtf": "0.6", "InpMemHours": "24", "InpChopLen": "100",
    "InpErMin": "0.2", "InpTolLtf": "0.15", "InpTestLook": "20",
    "InpTrigLook": "5", "InpPinMult": "1.5", "InpUsePin": "true",
    "InpUseEngulf": "true", "InpUseMicro": "true", "InpTrigBosLen": "5",
    "InpUseIfvg": "true", "InpIfvgLook": "60", "InpIfvgTol": "0.1",
    "InpMinTrigPow": "1", "InpMinRangeK": "0.4", "InpSwingLen": "10",
    "InpStopBuf": "0.2", "InpManipLook": "20", "InpRiskMaxAtr": "4.0",
    "InpLiqLen": "60", "InpTgtMin": "0.15", "InpTgtMax": "5.0",
    "InpMinRR": "1.5", "InpUseDayBud": "true", "InpDayBudgetK": "1.0",
    "InpTp1R": "1.0", "InpTp2R": "2.0", "InpTp3R": "3.0",
    "InpP1Pct": "40", "InpP2Pct": "30", "InpBeAfter": "1",
    "InpMaxBars": "120", "InpReEntryBars": "10", "InpUseTrail": "true",
    "InpTrailAfter": "1", "InpTrailK": "2.0", "InpRiskPct": "1.0",
    "InpMaxDayTrades": "3", "InpDayStopR": "2.0", "InpLvPersist": "6",
    "InpLvTol": "0.15", "InpLvLook": "60", "InpSweepLen": "20",
    "InpFracL": "2", "InpFracR": "2", "InpBosLen": "20",
    "InpFvgLook": "30", "InpRangeLen": "30", "InpRangeMinAtr": "2.0",
    "InpRangeMaxAtr": "20.0", "InpFacLook": "30", "InpNeedFac": "2",
    "InpUseSess": "true", "InpSessLdn": "true", "InpSessNy": "true",
    "InpNewsOn": "true", "InpRollOn": "true", "InpUseSprd": "true",
    "InpSprTicks": "5.0", "InpSprK": "5", "InpSweepDelay": "1",
    "InpSoftMode": "true", "InpSoftNeed": "2", "InpLimitTP": "false",
}

BASE_INI = (
    "[Tester]\n"
    "Expert=TgSignals\\KidzordEA\n"
    "Symbol=XAUUSD\n"
    "Period=M15\n"
    "Model=0\n"
    "Optimization=0\n"
    f"FromDate={os.getenv('TEST_FROM', '2026.07.28')}\n"
    f"ToDate={os.getenv('TEST_TO', '2026.10.24')}\n"
    "ForwardMode=0\n"
    "Deposit=10000\n"
    "DepositCurrency=USD\n"
    "Leverage=100\n"
    "ExecutionMode=0\n"
    "ReplaceReport=1\n"
    "ShutdownTerminal=1\n"
    "Visual=0\n"
    "Port=3000\n"
)

_SUFFIX = os.getenv("TEST_SUFFIX", "90d")
VARIANTS = {
    "kz_live_" + _SUFFIX: {"InpRiskMaxAtr": "2.0", "InpMinRR": "2.0",
                           "InpP1Pct": "40", "InpP2Pct": "30"},
    "kz_nopart_" + _SUFFIX: {"InpRiskMaxAtr": "2.0", "InpMinRR": "2.0",
                             "InpP1Pct": "0", "InpP2Pct": "0"},
}


def terminal_pids():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq terminal64.exe"],
                         capture_output=True).stdout.decode("utf-8", errors="ignore")
    return "terminal64" in out.lower()


def kill_terminal():
    subprocess.run(["taskkill", "/IM", "terminal64.exe", "/F"],
                   capture_output=True)
    for _ in range(20):
        if not terminal_pids():
            return
        time.sleep(1)


def write_ini(name, overrides):
    vals = dict(DEFAULTS)
    vals.update(overrides)
    section = "[TesterInputs]\n" + "".join(k + "=" + v + "\n" for k, v in vals.items())
    path = os.path.join(MT5_DIR, "tester_" + name + ".ini")
    io.open(path, "w", newline="\n").write(BASE_INI + "\n" + section)
    return path


def run_variant(name, overrides):
    ini = write_ini(name, overrides)
    before = os.path.getmtime(SRC) if os.path.exists(SRC) else 0
    kill_terminal()
    print("запуск", name, flush=True)
    subprocess.Popen(["cmd", "/c", "start", "", BASE, "/config:" + ini], shell=False)
    t0 = time.time()
    appeared = False
    while time.time() - t0 < 900:
        running = terminal_pids()
        appeared = appeared or running
        fresh = os.path.exists(SRC) and os.path.getmtime(SRC) > before
        if appeared and fresh and not running:
            break
        time.sleep(5)
    time.sleep(3)
    if not (os.path.exists(SRC) and os.path.getmtime(SRC) > before):
        print(name, "НЕ получил свежий CSV", flush=True)
        return False
    dst = os.path.join(MT5_DIR, name + ".csv")
    shutil.copyfile(SRC, dst)
    lines = io.open(dst, encoding="latin-1").read().split("\n")
    print(name, "|", lines[1], "|", lines[2], flush=True)
    return True


def start_terminal_normal():
    subprocess.Popen(["cmd", "/c", "start", "", BASE], shell=False)
    time.sleep(25)


if __name__ == "__main__":
    for name, ov in VARIANTS.items():
        run_variant(name, ov)
    start_terminal_normal()
    print("готово, терминал поднят", flush=True)
