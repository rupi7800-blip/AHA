"""Scanner delle opportunità e regole dei segnali.

Ogni regola è una funzione vettoriale causale feat -> Serie booleana (segnale attivo al tempo t)
più una direzione attesa (+1 rialzo, -1 ribasso, 0 movimento senza direzione). Le stesse
regole sono usate dallo scanner (ultima barra) e dal backtest (tutta la storia), così ciò che
lo scanner segnala è esattamente ciò che è stato verificato storicamente."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


@dataclass
class Rule:
    id: str
    name: str
    family: str          # tecnica | statistica | complessita | quantitativa | posizionamento | predittiva
    direction: int       # +1, -1, 0
    horizon: int         # barre su cui il backtest valuta l'esito
    fn: Callable[[pd.DataFrame], pd.Series]
    intensity: Callable[[pd.DataFrame], pd.Series]
    why: str
    invalidation: str


def _b(s): return s.fillna(False).astype(bool)


RULES: list[Rule] = [
    Rule("squeeze", "Volatilità in compressione", "tecnica", 0, 21,
         lambda f: _b(f["bb_width_pct"] < 10),
         lambda f: (10 - f["bb_width_pct"]).clip(0, 10) * 10,
         "L'ampiezza delle Bande di Bollinger è nel 10% più basso dell'ultimo anno: fasi di bassa volatilità "
         "tendono a essere seguite da espansioni (la volatilità è mean-reverting), ma la direzione non è nota.",
         "Il segnale decade se l'ampiezza torna sopra il 25° percentile senza rottura dei livelli."),
    Rule("near_breakout_up", "Vicino a breakout rialzista", "tecnica", 1, 21,
         lambda f: _b((f["dist_hi55_atr"] >= 0) & (f["dist_hi55_atr"] < 0.5) & (f["adx"] < 30)),
         lambda f: (1 - f["dist_hi55_atr"] / 0.5).clip(0, 1) * 100,
         "Il prezzo è a meno di mezzo ATR dal massimo a 55 giorni (livello dei sistemi trend-following classici).",
         "Chiusura sotto la media a 50 giorni o ritorno a più di 1,5 ATR dal massimo."),
    Rule("breakout_up", "Breakout rialzista confermato", "tecnica", 1, 21,
         lambda f: _b(f["close"] > f["hi55"].shift(1)),
         lambda f: ((f["close"] - f["hi55"].shift(1)) / f["atr"]).clip(0, 2) * 50,
         "Chiusura sopra il massimo dei 55 giorni precedenti.",
         "Chiusura di nuovo sotto il livello rotto (falso breakout)."),
    Rule("false_breakout_up", "Falso breakout rialzista", "tecnica", -1, 10,
         lambda f: _b((f["close"].shift(1) > f["hi55"].shift(2)) & (f["close"] < f["hi55"].shift(2))),
         lambda f: ((f["hi55"].shift(2) - f["close"]) / f["atr"]).clip(0, 2) * 50,
         "Ieri chiusura sopra il massimo a 55 giorni, oggi di nuovo sotto: rottura respinta.",
         "Nuova chiusura sopra il massimo."),
    Rule("breakout_down", "Breakdown ribassista confermato", "tecnica", -1, 21,
         lambda f: _b(f["close"] < f["lo55"].shift(1)),
         lambda f: ((f["lo55"].shift(1) - f["close"]) / f["atr"]).clip(0, 2) * 50,
         "Chiusura sotto il minimo dei 55 giorni precedenti.",
         "Chiusura di nuovo sopra il livello rotto."),
    Rule("bear_div", "Divergenza ribassista RSI", "tecnica", -1, 10,
         lambda f: _b((f["close"] >= f["close"].rolling(20).max()) & (f["rsi14"] < f["rsi14"].rolling(20).max() - 5)
                      & (f["rsi14"].rolling(20).max() > 65)),
         lambda f: (f["rsi14"].rolling(20).max() - f["rsi14"]).clip(0, 20) * 5,
         "Nuovo massimo a 20 giorni del prezzo con RSI sotto il proprio massimo recente: il momentum non conferma.",
         "RSI che torna sopra il massimo precedente."),
    Rule("bull_div", "Divergenza rialzista RSI", "tecnica", 1, 10,
         lambda f: _b((f["close"] <= f["close"].rolling(20).min()) & (f["rsi14"] > f["rsi14"].rolling(20).min() + 5)
                      & (f["rsi14"].rolling(20).min() < 35)),
         lambda f: (f["rsi14"] - f["rsi14"].rolling(20).min()).clip(0, 20) * 5,
         "Nuovo minimo a 20 giorni del prezzo con RSI sopra il proprio minimo recente.",
         "RSI che torna sotto il minimo precedente."),
    Rule("oversold_z", "Eccesso statistico al ribasso", "statistica", 1, 5,
         lambda f: _b(f["z20"] < -2.2),
         lambda f: (-f["z20"] - 2).clip(0, 2) * 50,
         "Prezzo oltre 2,2 deviazioni standard sotto la media a 20 giorni: possibile rimbalzo di mean reversion.",
         "Nuovi minimi con z-score in peggioramento o rottura del minimo a 55 giorni."),
    Rule("overbought_z", "Eccesso statistico al rialzo", "statistica", -1, 5,
         lambda f: _b(f["z20"] > 2.2),
         lambda f: (f["z20"] - 2).clip(0, 2) * 50,
         "Prezzo oltre 2,2 deviazioni standard sopra la media a 20 giorni.",
         "Nuovi massimi con z-score in aumento: in trend forti gli eccessi persistono."),
    Rule("vol_regime_up", "Cambio di regime di volatilità", "statistica", 0, 10,
         lambda f: _b((f["vol_ratio"] > 1.5) & (f["vol_ratio"].shift(5) <= 1.5)),
         lambda f: (f["vol_ratio"] - 1).clip(0, 2) * 50,
         "La volatilità a 20 giorni ha superato di 1,5 volte quella a 100 giorni: passaggio a un regime più turbolento.",
         "Rapporto di volatilità che rientra sotto 1,2."),
    Rule("volume_spike", "Volume anomalo", "quantitativa", 0, 5,
         lambda f: _b(f["vol_z"] > 2.5),
         lambda f: (f["vol_z"] - 2).clip(0, 3) * 33,
         "Volume oltre 2,5 deviazioni standard sopra la media a 20 giorni (scala logaritmica).",
         "Il volume torna nella norma senza movimento di prezzo."),
    Rule("accumulation", "Possibile accumulazione (proxy prezzo-volume)", "quantitativa", 1, 21,
         lambda f: _b((f["cmf20"] > 0.15) & (f["mom21"].abs() < 0.5)),
         lambda f: (f["cmf20"] - 0.1).clip(0, 0.3) * 333,
         "Chaikin Money Flow positivo con prezzo laterale: le chiusure avvengono nella parte alta del range con volume. "
         "È una stima da prezzo e volume, non un dato di flussi reali.",
         "CMF che torna negativo o rottura del minimo a 55 giorni."),
    Rule("momentum_strong", "Momentum forte e trend definito", "quantitativa", 1, 21,
         lambda f: _b((f["mom63"] > 1.0) & (f["adx"] > 25) & (f["dist_sma200"] > 0)),
         lambda f: f["mom63"].clip(0, 2) * 50,
         "Rendimento a 3 mesi aggiustato per volatilità superiore a 1 deviazione standard, ADX > 25, prezzo sopra la media a 200 giorni. "
         "Il time-series momentum è una delle anomalie più documentate (Moskowitz, Ooi, Pedersen 2012).",
         "Chiusura sotto la media a 50 giorni o ADX sotto 20."),
    Rule("momentum_weak", "Momentum negativo e trend definito", "quantitativa", -1, 21,
         lambda f: _b((f["mom63"] < -1.0) & (f["adx"] > 25) & (f["dist_sma200"] < 0)),
         lambda f: (-f["mom63"]).clip(0, 2) * 50,
         "Rendimento a 3 mesi aggiustato per volatilità inferiore a -1 deviazione standard con trend definito sotto la media a 200 giorni.",
         "Chiusura sopra la media a 50 giorni o ADX sotto 20."),
]
RULES_BY_ID = {r.id: r for r in RULES}

OPPORTUNITY_TYPES = {
    "accumulazione": ["accumulation"], "volumi anomali": ["volume_spike"],
    "prossimi a breakout": ["near_breakout_up", "squeeze"], "breakout": ["breakout_up", "breakout_down", "false_breakout_up"],
    "volatilità in compressione": ["squeeze"], "divergenze": ["bear_div", "bull_div"],
    "inversione (mean reversion)": ["oversold_z", "overbought_z"], "momentum": ["momentum_strong", "momentum_weak"],
    "cambio di regime": ["vol_regime_up"],
    "probabilità elevata (modello)": ["model_edge"],
}


def active_signals(feat: pd.DataFrame, enabled_rules=None) -> list[dict]:
    out = []
    for r in RULES:
        if enabled_rules and r.id not in enabled_rules:
            continue
        need = {"vol_z": r.id in ("volume_spike",), "cmf20": r.id == "accumulation"}
        if (need["vol_z"] and feat["vol_z"].isna().all()) or (need["cmf20"] and feat["cmf20"].isna().all()):
            continue
        sig = r.fn(feat)
        if len(sig) and bool(sig.iloc[-1]):
            inten = r.intensity(feat).iloc[-1]
            out.append({"rule": r.id, "name": r.name, "family": r.family, "direction": r.direction,
                        "intensity": float(np.clip(inten if np.isfinite(inten) else 50, 0, 100)),
                        "horizon_bars": r.horizon, "why": r.why, "invalidation": r.invalidation})
    return out


def score_opportunity(signals: list[dict], weights: dict, pred: dict | None, risk: float, reliability: float):
    """Punteggio composito trasparente: somma pesata delle intensità per famiglia di analisi,
    con bonus di convergenza se più famiglie indipendenti concordano sulla direzione."""
    if not signals:
        return None
    fam_score = {}
    for s in signals:
        fam_score[s["family"]] = max(fam_score.get(s["family"], 0), s["intensity"])
    total = sum(weights.get(f, 1.0) * v for f, v in fam_score.items())
    dirs = [s["direction"] for s in signals if s["direction"] != 0]
    net = int(np.sign(sum(dirs))) if dirs else 0
    agree = len({s["family"] for s in signals if s["direction"] == net and net != 0})
    convergence = agree >= 3
    score = total * (1.25 if convergence else 1.0) * reliability
    return {"score": float(score), "direction": net, "families": sorted(fam_score), "convergence": convergence,
            "n_families_agree": agree}
