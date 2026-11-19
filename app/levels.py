"""Supporti e resistenze CAUSALI e piani di uscita (stop loss / take profit) basati su di essi.

Come si individua un livello importante
1. Pivot: massimo (minimo) locale su una finestra di `w` barre per lato. Un pivot alla barra i è noto solo dalla
   barra i+w (conferma): `LevelFinder.at(t)` usa esclusivamente pivot confermati entro t, quindi non guarda il futuro.
2. Zone: i pivot vicini (entro `tol_atr` x ATR) si fondono in una zona. Un livello fa da supporto o resistenza a seconda
   di dove si trova il prezzo.
3. Forza: somma per i pivot della zona di (decadimento temporale con emivita `half_life` barre) x (1 + ampiezza della
   reazione dopo il pivot in ATR, limitata). Una zona è "importante" se ha almeno `min_touches` pivot e forza >= `min_strength`.

Come si sceglie l'uscita (long; lo short è speculare)
- STOP: appena sotto la zona di supporto importante più vicina il cui stop cada tra `min_stop_atr` e `max_stop_atr` ATR
  dall'ingresso, con un margine `stop_buf` ATR sotto la zona (le rotture di livello spesso sforano di poco).
- TARGET: appena prima della prima resistenza importante che offra un rischio/rendimento >= `min_rr`; entro `max_tp_atr` ATR.
  Se la resistenza vicina non basta si passa alla successiva; se nessuna basta l'operazione è scartata.
- Se sopra il prezzo non c'è nessuna resistenza (massimi storici, spazio libero) il target è a `free_tp_atr` x ATR e la base
  è dichiarata "spazio libero"; se sotto non c'è alcun supporto lo stop è a `fallback_stop_atr` x ATR e la base è "ATR"."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

LEVEL_DEFAULTS = {
    "pivot_w": 5, "lookback": 600, "tol_atr": 0.6, "half_life": 250, "min_touches": 2, "min_strength": 1.6,
    "min_stop_atr": 0.8, "max_stop_atr": 4.0, "stop_buf": 0.3, "tp_buf": 0.15, "max_tp_atr": 9.0,
    "min_rr": 1.5, "free_tp_atr": 3.0, "fallback_stop_atr": 2.0,
}


@dataclass
class Zone:
    low: float
    high: float
    price: float
    strength: float
    touches: int
    last_idx: int

    def as_dict(self):
        return {"low": self.low, "high": self.high, "price": self.price, "strength": round(self.strength, 2),
                "touches": self.touches}


class LevelFinder:
    """Pre-calcola i pivot una sola volta; `at(t)` restituisce le zone note alla chiusura della barra t."""

    def __init__(self, df: pd.DataFrame, atr: pd.Series, p: dict | None = None):
        self.p = dict(LEVEL_DEFAULTS, **(p or {}))
        self.atr = atr.values
        hi, lo, n, w = df["high"].values, df["low"].values, len(df), int(self.p["pivot_w"])
        piv = []   # (indice del pivot, prezzo, indice di conferma, ampiezza reazione in ATR, tipo: +1 massimo / -1 minimo)
        for i in range(w, n - w):
            a = self.atr[i]
            if not np.isfinite(a) or a <= 0:
                continue
            seg_h, seg_l = hi[i - w:i + w + 1], lo[i - w:i + w + 1]
            if hi[i] >= seg_h.max() and (seg_h == hi[i]).argmax() == w:         # massimo locale (primo in caso di parità)
                react = (hi[i] - lo[i + 1:i + w + 1].min()) / a
                piv.append((i, hi[i], i + w, react, 1))
            if lo[i] <= seg_l.min() and (seg_l == lo[i]).argmax() == w:         # minimo locale
                react = (hi[i + 1:i + w + 1].max() - lo[i]) / a
                piv.append((i, lo[i], i + w, react, -1))
        self.piv = np.array(piv, dtype=float).reshape(-1, 5)
        self._cache: dict[int, list[Zone]] = {}

    def at(self, t: int) -> list[Zone]:
        if t in self._cache:
            return self._cache[t]
        p = self.p
        P = self.piv
        m = (P[:, 2] <= t) & (P[:, 0] >= t - p["lookback"])
        zones: list[Zone] = []
        a = self.atr[t]
        if m.any() and np.isfinite(a) and a > 0:
            sel = P[m]
            sel = sel[np.argsort(sel[:, 1])]
            tol = p["tol_atr"] * a
            group = [sel[0]]
            groups = []
            for row in sel[1:]:
                if row[1] - group[-1][1] <= tol:
                    group.append(row)
                else:
                    groups.append(group); group = [row]
            groups.append(group)
            for g in groups:
                g = np.array(g)
                age = t - g[:, 0]
                wgt = 0.5 ** (age / p["half_life"]) * (1 + np.minimum(g[:, 3], 3.0) / 3.0)
                pr = g[:, 1]
                zones.append(Zone(low=float(pr.min()), high=float(pr.max()), price=float(np.average(pr, weights=wgt)),
                                  strength=float(wgt.sum()), touches=int(len(g)), last_idx=int(g[:, 0].max())))
        self._cache[t] = zones
        return zones

    def important(self, t: int) -> list[Zone]:
        p = self.p
        return [z for z in self.at(t) if z.touches >= p["min_touches"] and z.strength >= p["min_strength"]]


def plan_exit(side: int, ref: float, atr: float, zones: list[Zone], p: dict | None = None, need_tp: bool = True) -> dict:
    """side=+1 long, -1 short. `ref` = prezzo di ingresso previsto. Restituisce stop, target e le loro basi."""
    q = dict(LEVEL_DEFAULTS, **(p or {}))
    if not (np.isfinite(atr) and atr > 0):
        return {"ok": False, "why": "ATR non disponibile"}
    d = side
    # livelli ordinati per distanza dall'ingresso, dalla parte contraria (stop) e a favore (target)
    against = sorted([z for z in zones if (z.high < ref if d > 0 else z.low > ref)],
                     key=lambda z: (ref - z.high) if d > 0 else (z.low - ref))
    toward = sorted([z for z in zones if (z.low > ref + 0.3 * atr if d > 0 else z.high < ref - 0.3 * atr)],
                    key=lambda z: (z.low - ref) if d > 0 else (ref - z.high))
    stop = basis_stop = None
    for z in against:
        s = (z.low - q["stop_buf"] * atr) if d > 0 else (z.high + q["stop_buf"] * atr)
        dist = d * (ref - s)
        if dist < q["min_stop_atr"] * atr:
            continue                      # troppo vicino: il rumore lo spazza via, si guarda il livello successivo
        if dist > q["max_stop_atr"] * atr:
            break                         # troppo lontano: rischio sproporzionato
        stop, basis_stop = s, f"sotto supporto {z.price:.6g} ({z.touches} tocchi)" if d > 0 else f"sopra resistenza {z.price:.6g} ({z.touches} tocchi)"
        break
    if stop is None:
        stop = ref - d * q["fallback_stop_atr"] * atr
        basis_stop = "ATR (nessun livello importante utilizzabile)"
    risk = d * (ref - stop)
    if not need_tp:       # trend-following: nessun target, l'uscita è lo stop che segue il prezzo
        return {"ok": True, "stop": float(stop), "tp": None, "basis_stop": basis_stop, "basis_tp": "stop che segue il prezzo",
                "rr": None, "risk_pct": float(risk / ref * 100), "stop_from_levels": basis_stop.startswith(("sotto", "sopra")), "tp_from_levels": False}
    tp = basis_tp = None
    for z in toward:
        tgt = (z.low - q["tp_buf"] * atr) if d > 0 else (z.high + q["tp_buf"] * atr)
        gain = d * (tgt - ref)
        if gain > q["max_tp_atr"] * atr:
            break
        if gain >= q["min_rr"] * risk:
            tp, basis_tp = tgt, f"prima di resistenza {z.price:.6g} ({z.touches} tocchi)" if d > 0 else f"prima di supporto {z.price:.6g} ({z.touches} tocchi)"
            break
    if tp is None:
        if not toward:
            tp = ref + d * q["free_tp_atr"] * atr
            basis_tp = "spazio libero (nessun livello a favore): ATR"
            if q["free_tp_atr"] * atr < q["min_rr"] * risk:
                return {"ok": False, "why": "rischio/rendimento insufficiente in spazio libero"}
        else:
            return {"ok": False, "why": f"nessun livello a favore con rischio/rendimento >= {q['min_rr']:g}"}
    return {"ok": True, "stop": float(stop), "tp": float(tp), "basis_stop": basis_stop, "basis_tp": basis_tp,
            "rr": float(d * (tp - ref) / risk), "risk_pct": float(risk / ref * 100),
            "stop_from_levels": basis_stop.startswith(("sotto", "sopra")), "tp_from_levels": basis_tp.startswith("prima")}


def trailing_stop(finder: "LevelFinder", side: int, e: int, j: int, stop: float, atr_j: float, buf: float) -> float:
    """Stop che segue il prezzo: dopo l'ingresso (barra e) si sposta oltre l'ultimo pivot CONFERMATO entro la barra j
    (minimo per i long, massimo per gli short), mai in direzione sfavorevole."""
    P = finder.piv
    kind = -1 if side > 0 else 1
    m = (P[:, 4] == kind) & (P[:, 0] >= e) & (P[:, 2] <= j)
    if not m.any():
        return stop
    sel = P[m]
    last = sel[np.argmax(sel[:, 2])]                 # pivot confermato più recente
    cand = last[1] - side * buf * atr_j
    return max(stop, cand) if side > 0 else min(stop, cand)
