"""Segnali semplici: trasforma i segnali dello scanner in piani operativi comprensibili
(entrata, uscita in perdita, uscita in profitto, scadenza) e li verifica sulla storia
dello stesso strumento con ESATTAMENTE le stesse regole di entrata e uscita.

Regole del piano (uguali in tempo reale e nel backtest):
  - entrata: alla chiusura della seduta successiva al segnale (per i segnali di attesa:
    al superamento del livello indicato)
  - stop (uscita in perdita): entrata -/+ k_stop x ATR
  - obiettivo (uscita in profitto): entrata +/- k_target x ATR
  - scadenza: se né stop né obiettivo vengono toccati entro N sedute, si esce alla chiusura
  - se nella stessa seduta vengono toccati sia stop sia obiettivo si assume lo stop (ipotesi prudente)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .scanner import RULES_BY_ID

# parametri per famiglia di segnale: (k_stop, k_target)
PARAMS = {
    "default": (1.5, 3.0),
    "oversold_z": (1.5, 2.0), "overbought_z": (1.5, 2.0),       # rimbalzi: obiettivo più vicino
    "bull_div": (1.5, 2.5), "bear_div": (1.5, 2.5), "false_breakout_up": (1.5, 2.5),
}
WAIT_RULES = {"squeeze", "vol_regime_up", "volume_spike"}       # segnali senza direzione
NOT_TRADABLE_KINDS = {"yield"}
NOT_TRADABLE_IDS = {"VIX"}

SIMPLE_TEXT = {
    "squeeze": "Il prezzo si muove pochissimo da settimane. Di solito dopo una fase così calma arriva un movimento forte, ma non si sa in quale direzione.",
    "near_breakout_up": "Il prezzo è vicinissimo al punto più alto degli ultimi 2-3 mesi. Se lo supera, spesso il rialzo continua.",
    "breakout_up": "Il prezzo ha appena superato il punto più alto degli ultimi 2-3 mesi: è il segnale classico di inizio di una nuova salita.",
    "false_breakout_up": "Il prezzo aveva superato il massimo recente ma è subito tornato sotto: la salita è stata respinta e può seguire una discesa.",
    "breakout_down": "Il prezzo è sceso sotto il punto più basso degli ultimi 2-3 mesi: spesso la discesa continua.",
    "bear_div": "Il prezzo ha fatto un nuovo massimo ma con meno forza di prima: la spinta al rialzo si sta esaurendo.",
    "bull_div": "Il prezzo ha fatto un nuovo minimo ma con meno forza di prima: la spinta al ribasso si sta esaurendo.",
    "oversold_z": "Il prezzo è sceso molto più del normale in poco tempo. Spesso dopo eccessi così arriva un rimbalzo.",
    "overbought_z": "Il prezzo è salito molto più del normale in poco tempo. Spesso dopo eccessi così arriva una correzione.",
    "vol_regime_up": "Il mercato è diventato improvvisamente più agitato del solito. Meglio aspettare che si capisca la direzione.",
    "volume_spike": "Oggi sono stati scambiati molti più titoli del normale: qualcosa sta succedendo. Aspetta la conferma del prezzo.",
    "accumulation": "Il prezzo è fermo ma i volumi indicano che si compra più di quanto si venda: possibile accumulo prima di una salita.",
    "momentum_strong": "Lo strumento sale con decisione da circa 3 mesi ed è sopra la sua media di lungo periodo: i trend forti tendono a durare.",
    "momentum_weak": "Lo strumento scende con decisione da circa 3 mesi ed è sotto la sua media di lungo periodo: i trend negativi tendono a durare.",
    "cot_extreme": "I grandi speculatori sono tutti dalla stessa parte: quando tutti hanno già comprato (o venduto), il movimento rischia di invertirsi.",
    "model_edge": "Il modello statistico, verificato sul passato, vede una probabilità di movimento più alta del normale.",
}


def simulate_plan(df: pd.DataFrame, atr: pd.Series, sig: np.ndarray, direction: int, horizon: int,
                  k_stop: float, k_target: float, cost_bps=5.0, slip_bps=5.0, trigger: pd.Series | None = None):
    """Simula il piano su tutta la storia. Restituisce l'elenco delle operazioni."""
    o, h, l, c = (df[x].values for x in ("open", "high", "low", "close"))
    a = atr.values
    n = len(c); i = 0; trades = []
    cost = 2 * (cost_bps + slip_bps) / 1e4
    while i < n - 2:
        if not sig[i] or not np.isfinite(a[i]):
            i += 1; continue
        e = i + 1
        entry = c[e]
        if trigger is not None:  # segnale di attesa: si entra solo se il livello viene superato entro `horizon` sedute
            lvl = trigger.iloc[i]
            hit = None
            for j in range(i + 1, min(n, i + 1 + horizon)):
                if (direction > 0 and h[j] > lvl) or (direction < 0 and l[j] < lvl):
                    hit = j; break
            if hit is None:
                i += 1; continue
            e = hit; entry = max(lvl, o[hit]) if direction > 0 else min(lvl, o[hit])
        stop = entry - direction * k_stop * a[i]
        target = entry + direction * k_target * a[i]
        exit_px, exit_i, why = None, None, "scadenza"
        for j in range(e + 1, min(n, e + 1 + horizon)):
            hit_stop = l[j] <= stop if direction > 0 else h[j] >= stop
            hit_tgt = h[j] >= target if direction > 0 else l[j] <= target
            if hit_stop:
                exit_px = min(o[j], stop) if direction > 0 else max(o[j], stop); exit_i = j; why = "stop"; break
            if hit_tgt:
                exit_px = max(o[j], target) if direction > 0 else min(o[j], target); exit_i = j; why = "obiettivo"; break
        if exit_px is None:
            exit_i = min(n - 1, e + horizon)
            if exit_i <= e:
                break
            exit_px = c[exit_i]
        r = direction * np.log(exit_px / entry) - cost
        trades.append({"date": str(df.index[e].date()), "ret": float(r), "exit": why, "bars": int(exit_i - e)})
        i = exit_i
    return trades


def plan_stats(trades):
    if not trades:
        return {"n": 0}
    r = np.array([t["ret"] for t in trades])
    half = len(r) // 2
    se = r.std(ddof=1) / np.sqrt(len(r)) if len(r) > 1 else np.nan
    return {"n": int(len(r)), "win_rate": float((r > 0).mean()), "avg_pct": float(r.mean() * 100),
            "avg_win_pct": float(r[r > 0].mean() * 100) if (r > 0).any() else None,
            "avg_loss_pct": float(r[r <= 0].mean() * 100) if (r <= 0).any() else None,
            "t_stat": float(r.mean() / se) if se and se > 0 else None,
            "first_half_avg_pct": float(r[:half].mean() * 100) if half else None,
            "second_half_avg_pct": float(r[half:].mean() * 100) if len(r) - half else None,
            "share_target": float(np.mean([t["exit"] == "obiettivo" for t in trades])),
            "share_stop": float(np.mean([t["exit"] == "stop" for t in trades])),
            "worst_pct": float(r.min() * 100), "best_pct": float(r.max() * 100)}


def quality(st):
    """Semaforo: verde = verificato, giallo = debole, rosso = non confermato dalla storia."""
    if st.get("n", 0) < 15:
        return "grigio", "Troppo pochi casi nel passato per giudicarlo"
    t = st.get("t_stat") or 0
    h1, h2 = st.get("first_half_avg_pct") or 0, st.get("second_half_avg_pct") or 0
    if st["avg_pct"] > 0 and t >= 2 and h1 > 0 and h2 > 0:
        return "verde", "Ha funzionato nel passato in modo costante"
    if st["avg_pct"] > 0:
        return "giallo", "In media positivo nel passato, ma non in modo affidabile"
    return "rosso", "Nel passato su questo strumento non ha funzionato"


def _exit_date(last_date: pd.Timestamp, bars: int, crypto: bool):
    if crypto:
        return str((last_date + pd.Timedelta(days=bars)).date())
    return str((last_date + pd.offsets.BDay(bars)).date())


def build_plans(inst: dict, df: pd.DataFrame, feat: pd.DataFrame, signals: list[dict], cost_bps=5.0, slip_bps=5.0):
    if inst.get("kind") in NOT_TRADABLE_KINDS or inst["id"] in NOT_TRADABLE_IDS or (df["close"] <= 0).any():
        return {"tradable": False, "reason": "indicatore non acquistabile direttamente (rendimento, spread o indice di volatilità)", "plans": []}
    crypto = inst.get("source") == "binance"
    last = feat.iloc[-1]; price = float(df["close"].iloc[-1]); atr = float(last["atr"])
    plans = []
    for s in signals:
        rid = s["rule"]
        if rid == "model_edge" or rid == "cot_extreme":
            rule_fn = None
        else:
            rule_fn = RULES_BY_ID[rid].fn
        horizon = int(s.get("horizon_bars") or 21)
        k_stop, k_target = PARAMS.get(rid, PARAMS["default"])
        if horizon > 21:  # orizzonti lunghi: stop e obiettivo più ampi, in proporzione alla volatilità attesa
            sc = float(np.sqrt(horizon / 21)); k_stop, k_target = k_stop * sc, k_target * sc
        if rid in WAIT_RULES:
            hi, lo = float(last["hi55"]), float(last["lo55"])
            # due piani condizionati: acquisto sopra il massimo, vendita sotto il minimo
            for d, lvl, label in ((1, hi, "sopra"), (-1, lo, "sotto")):
                trig = feat["hi55"] if d > 0 else feat["lo55"]
                tr = simulate_plan(df, feat["atr"], rule_fn(feat).values, d, horizon, k_stop, k_target, cost_bps, slip_bps, trigger=trig)
                st = plan_stats(tr); q, qtxt = quality(st)
                plans.append({"rule": rid, "name": s["name"], "type": "attesa", "direction": d,
                              "action": "ATTENDI: compra solo se supera" if d > 0 else "ATTENDI: vendi solo se scende sotto",
                              "entry": lvl, "entry_text": f"solo se il prezzo va {label} {lvl:.6g}",
                              "stop": lvl - d * k_stop * atr, "target": lvl + d * k_target * atr,
                              "exit_by_bars": 2 * horizon, "exit_by": _exit_date(df.index[-1], 2 * horizon, crypto),
                              "rr": k_target / k_stop, "simple": SIMPLE_TEXT.get(rid, s["why"]), "why": s["why"],
                              "invalidation": f"Il livello non viene raggiunto entro {horizon} sedute: il segnale scade.",
                              "history": st, "quality": q, "quality_text": qtxt, "intensity": s["intensity"], "family": s["family"]})
            continue
        d = s["direction"]
        if d == 0:
            continue
        if rule_fn is not None:
            tr = simulate_plan(df, feat["atr"], rule_fn(feat).values, d, horizon, k_stop, k_target, cost_bps, slip_bps)
            st = plan_stats(tr); q, qtxt = quality(st)
        else:
            st = {"n": 0, "note": "segnale verificato con un metodo diverso (validazione del modello o storia COT settimanale)"}
            q, qtxt = ("grigio", "Verificato con il metodo del modello, non con questo piano") if rid == "model_edge" else ("grigio", "Dati settimanali: verifica del piano non disponibile")
        plans.append({"rule": rid, "name": s["name"], "type": "diretto", "direction": d,
                      "action": "POSSIBILE ACQUISTO" if d > 0 else "POSSIBILE VENDITA",
                      "entry": price, "entry_text": f"intorno a {price:.6g} (prezzo di chiusura di oggi)",
                      "stop": price - d * k_stop * atr, "target": price + d * k_target * atr,
                      "exit_by_bars": horizon, "exit_by": _exit_date(df.index[-1], horizon, crypto),
                      "rr": k_target / k_stop, "simple": SIMPLE_TEXT.get(rid, s["why"]), "why": s["why"],
                      "invalidation": s["invalidation"], "history": st, "quality": q, "quality_text": qtxt,
                      "intensity": s["intensity"], "family": s["family"]})
    for p in plans:
        p["stop_pct"] = (p["stop"] / p["entry"] - 1) * 100
        p["target_pct"] = (p["target"] / p["entry"] - 1) * 100
    # piani in conflitto (acquisto e vendita diretti sullo stesso strumento): segnalati
    dirs = {p["direction"] for p in plans if p["type"] == "diretto"}
    return {"tradable": True, "plans": plans, "conflict": len(dirs) > 1, "price": price, "atr": atr,
            "asof": str(df.index[-1].date())}
