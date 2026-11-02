# -*- coding: utf-8 -*-
# Полный список инпутов в каждом ini — терминал накапливает [TesterInputs]
# между прогонами, поэтому частичные переопределения складываются.
import io, os, subprocess, time

MT5_DIR = r'C:\Users\admin\Downloads\tg-signals\mt5_ea'
BASE = r'C:\Program Files\MetaTrader 5\terminal64.exe'
SRC_CSV = os.path.join(os.environ['APPDATA'], r'MetaQuotes\Terminal\Common\Files\kidzord_ea_XAUUSD_result.csv')

DEFAULTS = {
    'InpSlopeLen': '20', 'InpMinSlope': '0.01', 'InpUseDayCtx': 'true',
    'InpTolHtf': '0.6', 'InpMemHours': '24', 'InpChopLen': '100',
    'InpErMin': '0.2', 'InpTolLtf': '0.15', 'InpTestLook': '20',
    'InpTrigLook': '5', 'InpPinMult': '1.5', 'InpUsePin': 'true',
    'InpUseEngulf': 'true', 'InpUseMicro': 'true', 'InpTrigBosLen': '5',
    'InpUseIfvg': 'true', 'InpIfvgLook': '60', 'InpIfvgTol': '0.1',
    'InpMinTrigPow': '1', 'InpMinRangeK': '0.4', 'InpSwingLen': '10',
    'InpStopBuf': '0.2', 'InpManipLook': '20', 'InpRiskMaxAtr': '4.0',
    'InpLiqLen': '60', 'InpTgtMin': '0.15', 'InpTgtMax': '5.0',
    'InpMinRR': '1.5', 'InpUseDayBud': 'true', 'InpDayBudgetK': '1.0',
    'InpTp1R': '1.0', 'InpTp2R': '2.0', 'InpTp3R': '3.0',
    'InpP1Pct': '40', 'InpP2Pct': '30', 'InpBeAfter': '1',
    'InpMaxBars': '120', 'InpReEntryBars': '10', 'InpUseTrail': 'true',
    'InpTrailAfter': '1', 'InpTrailK': '2.0', 'InpRiskPct': '1.0',
    'InpMaxDayTrades': '3', 'InpDayStopR': '2.0', 'InpLvPersist': '6',
    'InpLvTol': '0.15', 'InpLvLook': '60', 'InpSweepLen': '20',
    'InpFracL': '2', 'InpFracR': '2', 'InpBosLen': '20',
    'InpFvgLook': '30', 'InpRangeLen': '30', 'InpRangeMinAtr': '2.0',
    'InpRangeMaxAtr': '20.0', 'InpFacLook': '30', 'InpNeedFac': '2',
    'InpUseSess': 'true', 'InpSessLdn': 'true', 'InpSessNy': 'true',
    'InpNewsOn': 'true', 'InpRollOn': 'true', 'InpUseSprd': 'true',
    'InpSprTicks': '5.0', 'InpSprK': '5', 'InpSweepDelay': '1',
    'InpSoftMode': 'true', 'InpSoftNeed': '2', 'InpLimitTP': 'false',
}

base = (
    '[Tester]\n'
    'Expert=TgSignals\\KidzordEA\n'
    'Symbol=XAUUSD\n'
    'Period=M15\n'
    'Model=0\n'
    'Optimization=0\n'
    'FromDate=2026.07.28\n'
    'ToDate=2026.10.24\n'
    'ForwardMode=0\n'
    'Deposit=10000\n'
    'DepositCurrency=USD\n'
    'Leverage=100\n'
    'ExecutionMode=0\n'
    'ReplaceReport=1\n'
    'ShutdownTerminal=1\n'
    'Visual=0\n'
    'Port=3000\n'
)

variants = {
    'combo':  {'InpRiskMaxAtr': '2.0', 'InpMinRR': '2.0'},
}

def terminal_running():
    r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq terminal64.exe'],
                       capture_output=True)
    return 'terminal64' in r.stdout.decode('utf-8', errors='ignore').lower()

def run_one(name, overrides):
    vals = dict(DEFAULTS)
    vals.update(overrides)
    ini = os.path.join(MT5_DIR, 'tester_kz_' + name + '.ini')
    section = '[TesterInputs]\n' + ''.join(k + '=' + v + '\n' for k, v in vals.items())
    io.open(ini, 'w', newline='\n').write(base + '\n' + section)
    subprocess.Popen(['cmd', '/c', 'start', '', BASE, '/config:' + ini], shell=False)
    time.sleep(20)
    t0 = time.time()
    while time.time() - t0 < 300:
        if terminal_running():
            break
        time.sleep(5)
    t0 = time.time()
    while time.time() - t0 < 600:
        if not terminal_running():
            break
        time.sleep(10)
    time.sleep(3)
    data = io.open(SRC_CSV, encoding='latin-1').read()
    io.open(os.path.join(MT5_DIR, 'kz_var_' + name + '.csv'), 'w', newline='\n').write(data)
    lines = data.split('\n')
    print(name.ljust(8), lines[1][:115])
    print(' '.ljust(8), lines[3][:115])

for name, overrides in variants.items():
    run_one(name, overrides)
print('batch done')
