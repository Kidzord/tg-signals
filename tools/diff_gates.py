# -*- coding: utf-8 -*-
"""Побарная сверка гейтов bot.py (st.evaluate) против KidzordEA."""
import csv

BOT = r"C:\Users\admin\Downloads\tg-signals\mt5_ea\gates_bot_XAUUSD.csv"
EA = r"C:\Users\admin\Downloads\tg-signals\mt5_ea\gates_ea_combo.csv"


def load(path):
    out = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        rd = csv.DictReader(f, delimiter=";")
        for row in rd:
            t = row.get("time", "").strip()
            if t:
                out[t] = row
    return out


bot = load(BOT)
ea = load(EA)
common = sorted(set(bot) & set(ea))
print(f"баров: bot={len(bot)} ea={len(ea)} общих={len(common)}")
if not common:
    raise SystemExit("нет пересечения по времени")

gates = [f"r{i}" for i in range(1, 13)]
mismatch = {g: [] for g in ["isL"] + gates}
for t in common:
    b, e = bot[t], ea[t]
    if b["isL"] != e["isL"]:
        mismatch["isL"].append(t)
    for g in gates:
        if b.get(g, "") != e.get(g, ""):
            mismatch[g].append(t)

n = len(common)
print(f"{'гейт':<5} {'расхождений':>12} {'%':>7}")
for g in ["isL"] + gates:
    k = len(mismatch[g])
    print(f"{g:<5} {k:>12} {100*k/n:>6.1f}%")

# примеры первых расхождений по самому проблемному гейту
worst = max(gates, key=lambda g: len(mismatch[g]))
print(f"\nсамый расходящийся: {worst} ({len(mismatch[worst])} шт.)")
for t in mismatch[worst][:8]:
    b, e = bot[t], ea[t]
    print(f"  {t}  bot: {['bot '+g+'='+b.get(g,'?') for g in gates if b.get(g)!=e.get(g)]}"
          f"  ea: {['ea '+g+'='+e.get(g,'?') for g in gates if b.get(g)!=e.get(g)]}  isL={b['isL']}/{e['isL']}")
