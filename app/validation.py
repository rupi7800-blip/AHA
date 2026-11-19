"""Validazione sul POOL di monete: l'affidabilità non si giudica sulla singola moneta (poche operazioni, monete correlate).

Per ogni verso (long / short) separatamente, sul solo periodo fuori campione:
- aspettativa netta per operazione in multipli di rischio R (1R = distanza dall'ingresso allo stop), profit factor, payoff;
- intervallo di confidenza dell'aspettativa con bootstrap a BLOCCHI DI MESE (tutte le monete insieme: le monete sono correlate);
- "caso": gli stessi trade vengono spostati nel calendario di un numero casuale di giorni (lo STESSO per tutte le monete,
  in modo circolare in una finestra comune) e si rivalutano con le stesse uscite: si conserva numero di operazioni,
  quota long/short, raggruppamento temporale e correlazione tra monete. p = (k+1)/(N+1);
- correzione di Holm tra i due versi; robustezza: costi doppi, senza i 5 trade migliori, senza una moneta alla volta.
Il periodo riservato (dati dal 2026-09-10, `signals.HOLDOUT_START`) non entra in nessuna statistica: i suoi trade non sono neppure memorizzati."""
from __future__ import annotations

import numpy as np

N_NULL, N_BOOT = 999, 2000


def _split(coin: dict, window: str):
    a, b = coin["split"], coin["hold"]
    if window == "oos":
        return lambda d: a <= d < b
    return lambda d: d >= b


def _gather(coins: dict, side: int, window: str):
    rows, per_coin = [], {}
    for cid, c in coins.items():
        inw = _split(c, window)
        for t in c["trades"]:                       # t = [date, side, gross, cost, r, risk_pct]
            if t[1] == side and inw(t[0]):
                rows.append((cid, *t))
    return rows


def _pf(net):
    w, l = net[net > 0].sum(), -net[net <= 0].sum()
    return float(w / l) if l > 0 else None


def _null(coins: dict, side: int, window: str, rng) -> np.ndarray:
    """Media di R dei trade con il calendario spostato, per ciascuno degli N_NULL spostamenti.

    Lo spostamento è in GIORNI ed è lo stesso per tutte le monete, circolare in un'unica finestra comune (dalla prima data
    fuori campione di tutte le monete fino alla fine). Una data spostata fuori dalla finestra di una moneta, o priva di esito
    (nessun piano di uscita), non produce un trade: si conserva così l'allineamento tra monete."""
    key = "long" if side > 0 else "short"
    prep, starts, ends = [], [], []
    for c in coins.values():
        inw = _split(c, window)
        look = {}
        for d, x in zip(c["out"]["dates"], c["out"][key]):
            if inw(d) and x is not None:
                look[d] = x[1]
        sig = [t[0] for t in c["trades"] if t[1] == side and inw(t[0])]
        if sig:
            prep.append((look, sig))
            ds = [d for d in c["out"]["dates"] if inw(d)]
            starts.append(ds[0]); ends.append(ds[-1])
    if not prep:
        return np.array([])
    import pandas as pd
    t0 = pd.Timestamp(min(starts)); W = (pd.Timestamp(max(ends)) - t0).days + 1
    shifts = rng.integers(max(10, W // 20), max(11, W - W // 20), N_NULL)      # esclude spostamenti quasi nulli
    base = [(look, np.array([(pd.Timestamp(d) - t0).days for d in sig])) for look, sig in prep]
    out = np.empty(N_NULL)
    for k, s in enumerate(shifts):
        vals = []
        for look, offs in base:
            for o in (offs + s) % W:
                x = look.get(str((t0 + pd.Timedelta(days=int(o))).date()))
                if x is not None:
                    vals.append(x)
        out[k] = np.mean(vals) if vals else np.nan
    return out


def pooled_side(coins: dict, side: int, window: str = "oos", seed: int = 0, in_sample_exp_r: float | None = None) -> dict:
    rng = np.random.default_rng(seed)
    rows = _gather(coins, side, window)
    label = "long" if side > 0 else "short"
    if not rows:
        return {"side": label, "n": 0, "grade": "non valutabile", "why": "nessuna operazione nel periodo"}
    cid = np.array([r[0] for r in rows]); date = [r[1] for r in rows]
    gross = np.array([r[3] for r in rows]); cost = np.array([r[4] for r in rows]); R = np.array([r[5] for r in rows])
    risk = np.array([r[6] for r in rows]) / 100
    net = gross - cost
    months = np.array([d[:7] for d in date])
    um = np.unique(months)
    # bootstrap a blocchi di mese
    groups = [np.where(months == m)[0] for m in um]
    boots = np.array([R[np.concatenate([groups[j] for j in rng.integers(0, len(groups), len(groups))])].mean() for _ in range(N_BOOT)])
    null = _null(coins, side, window, rng)
    null = null[np.isfinite(null)]
    obs = float(R.mean())
    p = float((np.sum(null >= obs) + 1) / (len(null) + 1)) if len(null) else None
    R2 = (gross - 2 * cost) / risk
    top5_removed = float(np.sort(R)[:-5].mean()) if len(R) > 5 else None
    loco = {c: float(R[cid != c].mean()) for c in np.unique(cid)} if len(np.unique(cid)) > 1 else {}
    res = {"side": label, "n": int(len(R)), "months": int(len(um)), "coins": int(len(np.unique(cid))),
           "expectancy_r": obs, "expectancy_pct": float(net.mean() * 100), "win_rate": float((net > 0).mean()),
           "avg_win_pct": float(net[net > 0].mean() * 100) if (net > 0).any() else None,
           "avg_loss_pct": float(net[net <= 0].mean() * 100) if (net <= 0).any() else None,
           "payoff": float(net[net > 0].mean() / -net[net <= 0].mean()) if (net > 0).any() and (net <= 0).any() and net[net <= 0].mean() < 0 else None,
           "profit_factor": _pf(net), "worst_r": float(R.min()),
           "ci95_r": [float(np.quantile(boots, .025)), float(np.quantile(boots, .975))],
           "p_vs_chance": p, "chance_mean_r": float(np.nanmean(null)) if len(null) else None, "n_null": int(len(null)),
           "robust": {"double_costs_r": float(R2.mean()), "without_top5_r": top5_removed,
                      "worst_leave_one_coin_out_r": min(loco.values()) if loco else None},
           "in_sample_expectancy_r": in_sample_exp_r}
    return res


def holm(ps: dict[str, float | None]) -> dict[str, float | None]:
    items = sorted([(k, v) for k, v in ps.items() if v is not None], key=lambda kv: kv[1])
    m, out, run = len(items), {k: None for k in ps}, 0.0
    for j, (k, v) in enumerate(items):
        run = max(run, min(1.0, (m - j) * v))
        out[k] = run
    return out


def grade(r: dict, p_holm: float | None) -> tuple[str, str]:
    n, mo = r["n"], r.get("months", 0)
    if n < 30 or mo < 6:
        return "non valutabile", f"{n} operazioni su {mo} mesi nel periodo fuori campione (servono almeno 30 operazioni e 6 mesi)"
    e, p, pf = r["expectancy_r"], r["p_vs_chance"], r["profit_factor"]
    rb = r["robust"]
    if e <= 0:
        return "bassa", f"aspettativa netta {e:+.2f}R: in perdita dopo i costi"
    if p is None or p >= 0.10:
        return "bassa", f"aspettativa {e:+.2f}R ma non distinguibile dal caso (p={p:.2f})"
    robust_ok = all(x is not None and x > 0 for x in (rb["double_costs_r"], rb["without_top5_r"])) and (rb["worst_leave_one_coin_out_r"] or 0) > 0
    if (n >= 100 and mo >= 12 and r["ci95_r"][0] > 0 and p_holm is not None and p_holm < 0.05 and (pf or 0) >= 1.3 and robust_ok):
        return "alta", f"{n} operazioni, {e:+.2f}R, IC95% sopra zero, p corretto {p_holm:.3f}, PF {pf:.2f}, regge a costi doppi e senza i 5 migliori"
    ins = r.get("in_sample_expectancy_r")
    if n >= 50 and p < 0.05 and (pf or 0) >= 1.15 and (ins is None or ins > 0):
        return "media", f"{n} operazioni, {e:+.2f}R, p={p:.3f} contro il caso, PF {pf:.2f}: indizio, non ancora confermato (IC o robustezza insufficienti)"
    return "bassa", f"aspettativa {e:+.2f}R, p={p:.3f}: indizi troppo deboli o troppo pochi dati per fidarsi"


def run(analyses: dict[str, dict], window: str = "oos", key: str | None = None) -> dict:
    """analyses: {id: analisi con `aha` (con _trades, _outcomes, history)}. Restituisce la validazione del pool."""
    coins = {}
    for cid, a in analyses.items():
        s = (a or {}).get("aha") or {}
        if key:                                  # strategia alternativa (es. "simple") dentro l'analisi
            s = s.get(key) or {}
        if not s.get("_trades") or not s.get("_outcomes"):
            continue
        h = s["history"]
        coins[cid] = {"trades": s["_trades"], "out": s["_outcomes"], "split": h["split_date"], "hold": h["holdout_start"],
                      "ins": h["in_sample"]}
    out = {"n_coins": len(coins), "window": window, "sides": {}}
    ins_exp = {}
    for label in ("long", "short"):
        n = sum(c["ins"][label].get("n", 0) for c in coins.values())
        ins_exp[label] = (sum(c["ins"][label].get("n", 0) * c["ins"][label].get("expectancy_r", 0) for c in coins.values()) / n) if n else None
    res = {lab: pooled_side(coins, side, window, in_sample_exp_r=ins_exp[lab]) for lab, side in (("long", 1), ("short", -1))}
    ph = holm({k: v.get("p_vs_chance") for k, v in res.items()})
    for k, v in res.items():
        if v["n"]:
            v["p_holm"] = ph[k]
            v["grade"], v["why"] = grade(v, ph[k])
        out["sides"][k] = v
    return out


def _brief(v: dict, n_coins: int) -> dict:
    return {"grade": v.get("grade", "non valutabile"), "why": v.get("why"), "n": v.get("n", 0),
            "expectancy_r": v.get("expectancy_r"), "expectancy_pct": v.get("expectancy_pct"), "win_rate": v.get("win_rate"),
            "avg_win_pct": v.get("avg_win_pct"), "avg_loss_pct": v.get("avg_loss_pct"), "payoff": v.get("payoff"),
            "profit_factor": v.get("profit_factor"), "p_vs_chance": v.get("p_vs_chance"), "ci95_r": v.get("ci95_r"),
            "scope": f"pool di {n_coins} monete, solo periodo fuori campione"}


def attach(sig: dict, val: dict | None, default_params: bool = True) -> dict:
    """Aggiunge al segnale l'affidabilità del POOL per il suo verso (e per entrambi i versi)."""
    sides = (val or {}).get("sides") or {}
    nc = (val or {}).get("n_coins", 0)
    if not sides:
        sig["reliability"] = {"grade": "non valutabile", "why": "validazione del pool non ancora calcolata: premi “Aggiorna dati”", "n": 0}
        sig["reliability_by_side"] = {}
        return sig
    if not default_params:
        sig["reliability"] = {"grade": "non applicabile", "why": "hai modificato i parametri: la validazione sul pool vale solo per i parametri automatici; "
                              "le statistiche sotto sono descrittive", "n": 0}
        sig["reliability_by_side"] = {}
        return sig
    by = {k: _brief(v, nc) for k, v in sides.items()}
    sig["reliability_by_side"] = by
    sig["reliability"] = by.get(sig.get("side")) or {"grade": "nessun segnale", "why": "nessun segnale attivo: vedi l'affidabilità storica dei due versi", "n": 0}
    return sig
