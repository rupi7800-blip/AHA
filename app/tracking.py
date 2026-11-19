"""Registro IN AVANTI dei segnali: la prova fuori campione vera.

A ogni aggiornamento ogni segnale attivo viene registrato (moneta, verso, data, parametri). Più avanti, quando le barre
successive esistono, lo stesso esito si calcola con le STESSE regole del backtest (`signals.Trader`): ingresso
all'apertura della barra dopo il segnale, stop da livelli e stop mobile, costi, gap. Nessun dato futuro è usato al momento
della registrazione e i risultati non possono essere "aggiustati" a posteriori."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import signals, storage
from .analysis import indicators as ind

KEY = "aha_track"


def load() -> list[dict]:
    return list(storage.kv_get(KEY) or [])


def record(analyses: dict[str, dict]) -> int:
    log = load()
    seen = {(e["coin"], e["asof"], e["side"]) for e in log}
    n = 0
    for cid, a in analyses.items():
        s = (a or {}).get("aha") or {}
        if s.get("side") and (cid, s["asof"], s["side"]) not in seen:
            log.append({"coin": cid, "side": s["side"], "asof": s["asof"], "entry_ref": s["entry"], "stop": s["stop_loss"],
                        "tp_indicative": s.get("take_profit"), "params": s["params"], "status": "aperto",
                        "logged_at": storage.utcnow()})
            n += 1
    if n:
        storage.kv_set(KEY, log)
    return n


def evaluate(loader) -> list[dict]:
    """loader(coin) -> DataFrame di barre CHIUSE. Aggiorna lo stato delle voci aperte."""
    log, changed = load(), False
    cache: dict[str, tuple] = {}
    for e in log:
        if e["status"] != "aperto":
            continue
        df = loader(e["coin"])
        if df is None or len(df) < 50 or e["asof"] not in set(str(x)[:10] for x in df.index):
            continue
        if e["coin"] not in cache:
            cache[e["coin"]] = (df, ind.atr(df))
        df, atr = cache[e["coin"]]
        i = [str(x)[:10] for x in df.index].index(e["asof"])
        tr = signals.Trader(df, atr, e["params"])
        o = tr.outcome(i, 1 if e["side"] == "long" else -1)
        if o is None:
            continue                                  # barre successive ancora insufficienti: resta aperto
        changed = True
        if "skip" in o:
            e.update(status="scartato", why=o["skip"])
        else:
            e.update(status="chiuso", net=o["net"], r=o["r"], exit=o["exit"], bars=o["bars"])
    if changed:
        storage.kv_set(KEY, log)
    return log


def summary(log: list[dict]) -> dict:
    closed = [e for e in log if e["status"] == "chiuso"]
    out = {"n_logged": len(log), "n_open": sum(e["status"] == "aperto" for e in log), "n_closed": len(closed)}
    for side in ("long", "short", "all"):
        xs = [e for e in closed if side == "all" or e["side"] == side]
        if xs:
            r = np.array([e["r"] for e in xs]); net = np.array([e["net"] for e in xs])
            out[side] = {"n": len(xs), "expectancy_r": float(r.mean()), "expectancy_pct": float(net.mean() * 100),
                         "win_rate": float((net > 0).mean())}
    return out
