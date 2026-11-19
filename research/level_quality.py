"""I livelli importanti sono davvero supporti/resistenze? Test indipendente dai segnali.

Per ogni barra e per ogni distanza d in {0.5, 0.8, 1.1, 1.4} ATR, si considera un livello ipotetico a distanza d SOTTO il prezzo
(supporto) o SOPRA (resistenza) e si guarda cosa succede nelle 10 barre successive:
- "rimbalzo": il prezzo si muove di 1.5 ATR a favore (lontano dal livello) PRIMA di rompere il livello di 0.3 ATR;
- "rottura": il livello viene rotto prima.
Si confrontano tre gruppi con la STESSA geometria: livello su una zona IMPORTANTE (>= 2 pivot e forza >= soglia), su una zona
DEBOLE, e punto qualunque (nessuna zona entro la tolleranza). Se le zone importanti non rimbalzano più dei punti qualunque,
non sono livelli utili. Periodo: fuori campione (dopo il 60% dei dati precedenti al periodo riservato)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import pipeline as pl, signals, storage  # noqa: E402
from app.analysis import indicators as ind  # noqa: E402
from app.levels import LevelFinder  # noqa: E402

DIST = (0.5, 0.8, 1.1, 1.4)
H = 10


def classify(finder, t, level):
    """Gruppo del livello ipotetico: la zona PIÙ FORTE che lo contiene (per numero di tocchi)."""
    a = finder.atr[t]
    tol = finder.p["tol_atr"] * a
    best = 0
    for z in finder.at(t):
        if z.low - tol <= level <= z.high + tol:
            best = max(best, z.touches)
    return "nessuna" if best == 0 else "1 tocco" if best == 1 else "2-3 tocchi" if best <= 3 else "4-5 tocchi" if best <= 5 else "6+ tocchi"


def main(window="oos"):
    storage.init(storage.DB_PATH)
    acc = {}
    for inst in pl.UNIVERSE["instruments"]:
        df = storage.load_bars(inst["id"], "1d").iloc[:-1]
        if len(df) < 400:
            continue
        n = len(df); n_pre = n - signals.HOLDOUT_DAYS; split = int(n_pre * signals.TRAIN_FRAC)
        lo, hi = (split, n_pre - H - 1) if window == "oos" else (300, split)
        atr = ind.atr(df); f = LevelFinder(df, atr)
        c, hh, ll = df["close"].values, df["high"].values, df["low"].values
        for t in range(lo, hi):
            a = atr.iloc[t]
            if not np.isfinite(a):
                continue
            for d in DIST:
                for kind in ("supporto", "resistenza"):
                    s = 1 if kind == "supporto" else -1
                    level = c[t] - s * d * a
                    grp = classify(f, t, level)
                    brk = level - s * 0.3 * a            # rottura: oltre il livello di 0.3 ATR
                    tgt = c[t] + s * 1.5 * a
                    out = "nulla"
                    for j in range(t + 1, t + 1 + H):
                        hit_b = ll[j] <= brk if s > 0 else hh[j] >= brk
                        hit_t = hh[j] >= tgt if s > 0 else ll[j] <= tgt
                        if hit_b:
                            out = "rottura"; break
                        if hit_t:
                            out = "rimbalzo"; break
                    k = (kind, d, grp)
                    a_ = acc.setdefault(k, [0, 0, 0, 0]); a_[0] += 1
                    a_[{"rimbalzo": 1, "rottura": 2, "nulla": 3}[out]] += 1
    print(f"# Qualità dei livelli ({'fuori campione' if window == 'oos' else 'in campione'}), pool di monete\n")
    print("| tipo | distanza (ATR) | gruppo | eventi | rimbalzo % | rottura % | nulla % |")
    print("|---|---|---|---|---|---|---|")
    for (kind, d, grp), (n_, b, r, z) in sorted(acc.items()):
        print(f"| {kind} | {d} | {grp} | {n_} | {b / n_ * 100:.1f} | {r / n_ * 100:.1f} | {z / n_ * 100:.1f} |")
    print("\nNota: gli eventi di barre vicine si sovrappongono e le monete sono correlate: le differenze di pochi punti percentuali non sono significative.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "oos")
