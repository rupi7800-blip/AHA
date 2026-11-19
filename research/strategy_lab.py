"""Laboratorio delle strategie: confronta varianti sul POOL di monete.

Metodo (vedi app/validation.py):
- scelta SOLO sul periodo iniziale (in campione, primo 60% dei dati precedenti al periodo riservato);
- tutte le varianti sono poi riportate fuori campione SENZA riscegliere; il periodo riservato (ultime 10 settimane)
  non viene mai stampato qui;
- correzione di Holm sul totale dei confronti variante x verso.

Uso:  AHA_DB=/percorso/db.sqlite python research/strategy_lab.py > research/lab_results.md"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import pipeline as pl, signals, storage, validation  # noqa: E402

VARIANTS = {
    "V0 uscite ATR fisse (2/3)": {"exit_sr": 0},
    "V1 uscite da supporti/resistenze": {},
    "V2 V1 + regime (EMA200)": {"regime": 1},
    "V3 V1 + 3 voti minimi": {"min_votes": 3},
    "V4 V1 solo trend (senza ritorno alla media)": {"w_mr": 0},
    "V5 V1 solo ritorno alla media (senza trend)": {"w_trend": 0},
    "V6 V1 + ingresso vicino a livello": {"near_level": 1},
    "V7 V1 senza on-chain/sentiment": {"w_chain": 0},
}


VARIANTS2 = {   # secondo giro, ESPLORATIVO: nato guardando il rapporto vincita/perdita del primo giro
    "T1 trend + stop mobile, 30 gg": {"trail": 1, "horizon": 30, "w_mr": 0},
    "T2 trend + stop mobile, 60 gg": {"trail": 1, "horizon": 60, "w_mr": 0},
    "T3 T2 + regime (EMA200)": {"trail": 1, "horizon": 60, "w_mr": 0, "regime": 1},
    "T4 tutti i voti + stop mobile, 30 gg": {"trail": 1, "horizon": 30},
    "T5 T2 + 3 voti minimi": {"trail": 1, "horizon": 60, "w_mr": 0, "min_votes": 3},
}
ROUND = sys.argv[1] if len(sys.argv) > 1 else "1"


def load(cid):
    df = storage.load_bars(cid, "1d").iloc[:-1]
    oc = {c: storage.load_series(f"onchain:{cid}:{c}") for c in pl.ONCHAIN_COLS}
    return df, {k: v for k, v in oc.items() if len(v)}, storage.load_series("fear_greed:global")


def main():
    storage.init(storage.DB_PATH)
    ids = [i["id"] for i in pl.UNIVERSE["instruments"]]
    data = {i: load(i) for i in ids}
    results = {}
    for name, over in (VARIANTS2 if ROUND == "2" else VARIANTS).items():
        analyses, skipped = {}, {}
        for cid, (df, oc, fg) in data.items():
            if len(df) < 400:
                continue
            s = signals.build_signal(df, oc, fg, overrides=over)
            analyses[cid] = {"aha": s}
            for k, v in s["history"]["skipped"].items():
                skipped[k] = skipped.get(k, 0) + v
        v = validation.run(analyses, "oos")
        ins = {}
        for side in ("long", "short"):
            n = sum(a["aha"]["history"]["in_sample"][side].get("n", 0) for a in analyses.values())
            e = sum(a["aha"]["history"]["in_sample"][side].get("n", 0) * a["aha"]["history"]["in_sample"][side].get("expectancy_r", 0)
                    for a in analyses.values()) / n if n else None
            ins[side] = {"n": n, "expectancy_r": e}
        results[name] = {"validation": v, "in_sample": ins, "skipped": skipped}
        print(f"fatto {name}", file=sys.stderr)
    # Holm su tutte le combinazioni variante x verso
    prev = {}
    if ROUND == "2" and (Path(__file__).parent / "lab_results.json").exists():       # Holm su TUTTI i confronti dei due giri
        old = json.load(open(Path(__file__).parent / "lab_results.json"))["results"]
        prev = {f"{n}|{s}": r["validation"]["sides"][s].get("p_vs_chance") for n, r in old.items() for s in ("long", "short")}
    ps = dict(prev)
    ps.update({f"{n}|{s}": r["validation"]["sides"][s].get("p_vs_chance") for n, r in results.items() for s in ("long", "short")})
    ph = validation.holm(ps)
    out_name = "lab_results_round2.json" if ROUND == "2" else "lab_results.json"
    json.dump({"results": results, "holm": ph}, open(Path(__file__).parent / out_name, "w"), default=str, indent=1)
    f = lambda x, d=2: "–" if x is None else f"{x:+.{d}f}"
    print("# Risultati del laboratorio (pool di %d monete)\n" % len(ids))
    print("Scelta sul periodo in campione; i valori fuori campione NON sono stati usati per scegliere. R = multiplo del rischio, netto dei costi.\n")
    print("| Variante | verso | IN CAMPIONE n | exp R | FUORI CAMPIONE n | exp R | exp % | win % | payoff | PF | IC95% R | p caso | p Holm | grado |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for n, r in results.items():
        for s in ("long", "short"):
            o = r["validation"]["sides"][s]; i = r["in_sample"][s]
            ci = o.get("ci95_r")
            print(f"| {n} | {s} | {i['n']} | {f(i['expectancy_r'])} | {o['n']} | {f(o.get('expectancy_r'))} | {f(o.get('expectancy_pct'))} | "
                  f"{(o.get('win_rate') or 0) * 100:.0f} | {f(o.get('payoff'))} | {f(o.get('profit_factor'))} | "
                  f"{'–' if not ci else f'[{ci[0]:+.2f}, {ci[1]:+.2f}]'} | {f(o.get('p_vs_chance'))} | {f(ph[f'{n}|{s}'])} | {o.get('grade')} |")
    best = max(results, key=lambda n: (results[n]["in_sample"]["long"]["n"] * (results[n]["in_sample"]["long"]["expectancy_r"] or 0)
                                       + results[n]["in_sample"]["short"]["n"] * (results[n]["in_sample"]["short"]["expectancy_r"] or 0))
               / max(1, results[n]["in_sample"]["long"]["n"] + results[n]["in_sample"]["short"]["n"]))
    print(f"\nVariante con la migliore aspettativa IN CAMPIONE (unico criterio di scelta): **{best}**")
    print("\nOperazioni scartate dal piano di uscita (motivo: numero):")
    for n, r in results.items():
        print(f"- {n}: {r['skipped']}")


if __name__ == "__main__":
    main()
