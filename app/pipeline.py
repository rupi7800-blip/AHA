"""Orchestrazione: aggiornamento dati, analisi multidisciplinare, previsioni, scanner, alert.

Ciclo: fetch -> salva barre -> per ogni asset (in processi paralleli) analisi + walk-forward
-> rete delle correlazioni -> panoramica -> risoluzione previsioni scadute -> alert.
Le analisi pesanti (walk-forward) vengono ricalcolate solo quando arriva una nuova barra."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from . import storage
from .analysis import indicators as ind
from .analysis import stats_models as sm
from .backtest import backtest_rule
from . import signals, tracking, validation
from .data.adapters import ADAPTERS
from .data.onchain import COINMETRICS, FEARGREED, Glassnode

GLASSNODE = Glassnode()
from .predict.model import HORIZONS, INTRADAY_HORIZONS, walk_forward
from .scanner import RULES, active_signals, score_opportunity
from .plans import build_plans

ENGINE_VERSION = "0.4.0-aha"

log = logging.getLogger("aha")
ROOT = Path(__file__).resolve().parent.parent
UNIVERSE = json.loads((ROOT / "config" / "universe.json").read_text())

DEFAULT_SETTINGS = {
    "enabled_markets": list(UNIVERSE["markets"].keys()),
    "disabled_instruments": [],
    "custom_instruments": [],
    "watchlist": [],
    "weights": {"tecnica": 1.0, "statistica": 1.0, "complessita": 0.5, "quantitativa": 1.0,
                "posizionamento": 0.7, "predittiva": 1.2, "news": 0.5},
    "horizons": list(HORIZONS.keys()) + list(INTRADAY_HORIZONS.keys()),
    "enabled_rules": [r.id for r in RULES] + ["model_edge"],
    "risk_max_atr_pct": 8.0,
    "min_reliability": 0.5,
    "alert_kinds": ["anomalia", "volume", "regime", "divergenza", "flusso", "convergenza", "watchlist", "previsione"],
    "cost_bps": 5.0, "slippage_bps": 5.0,
    "refresh_minutes": 30,
}

STATE = {"running": False, "last_run": None, "last_error": None, "progress": "", "done": 0, "total": 0}


def settings() -> dict:
    # copia: il valore in cache è condiviso e qui viene modificato
    s = json.loads(json.dumps(storage.kv_get("settings") or {}))
    out = json.loads(json.dumps(DEFAULT_SETTINGS))
    for k, v in s.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def save_settings(new: dict) -> dict:
    cur = settings()
    cur.update({k: v for k, v in new.items() if k in DEFAULT_SETTINGS})
    storage.kv_set("settings", cur)
    return cur


def instruments(all_=False) -> list[dict]:
    s = settings()
    lst = UNIVERSE["instruments"] + s.get("custom_instruments", [])
    if all_:
        return lst
    return [i for i in lst if i["market"] in s["enabled_markets"] and i["id"] not in s["disabled_instruments"]]


def instrument(iid: str) -> dict | None:
    return next((i for i in instruments(True) if i["id"] == iid), None)


def clean(o):
    """Rende un oggetto serializzabile in JSON standard: NaN/inf -> None, tipi numpy -> Python."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return float(o) if np.isfinite(o) else None
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    return o


# ------------------------------------------------------------------ dati
FULL_REFRESH_DAYS = 7   # una volta a settimana riscarica tutta la storia (split, rettifiche della fonte)


def _last_ts(inst_id, iv):
    with storage.conn() as c:
        r = c.execute("SELECT MAX(ts) FROM bars WHERE inst=? AND interval=?", (inst_id, iv)).fetchone()
    return pd.Timestamp(r[0]) if r and r[0] else None


def fetch_instrument(inst: dict, force=False):
    intervals = ["1d"] + (["1h"] if inst["source"] == "binance" else [])
    refresh = settings()["refresh_minutes"]
    for iv in intervals:
        info = storage.fetch_info(inst["id"], iv)
        if info and not force:
            age = (pd.Timestamp.now("UTC") - pd.Timestamp(info["fetched_at"])).total_seconds() / 60
            if age < refresh and info["status"] == "ok":
                continue
        # download incrementale: se la storia c'è già scarichiamo solo il tratto finale
        last = _last_ts(inst["id"], iv)
        full_at = storage.kv_get(f"fullfetch:{inst['id']}:{iv}")
        stale_full = not full_at or (pd.Timestamp.now("UTC") - pd.Timestamp(full_at)).days >= FULL_REFRESH_DAYS
        since = None if (last is None or stale_full) else last
        adapter = ADAPTERS[inst["source"]]
        res = adapter.bars(inst["symbol"], iv, since=since)
        if res.status == "ok" and since is not None and len(res.df):
            # coerenza: la barra in comune deve coincidere, altrimenti (split, rettifica) storia completa
            prev = storage.load_bars_since(inst["id"], iv, res.df.index[0])
            common = prev.index.intersection(res.df.index)
            if len(common):
                a, b = prev.loc[common, "close"].astype(float), res.df.loc[common, "close"].astype(float)
                if ((a - b).abs() / b.abs().replace(0, np.nan)).max() > 0.01:
                    since = None
                    res = adapter.bars(inst["symbol"], iv, since=None)
        if res.status == "ok":
            if since is None:
                storage.replace_bars(inst["id"], iv, res.df)
                storage.kv_set(f"fullfetch:{inst['id']}:{iv}", storage.utcnow())
            else:
                storage.save_bars(inst["id"], iv, res.df)
            if "taker_buy_ratio" in res.meta:
                storage.save_series(f"takerbuy:{inst['id']}:{iv}", res.meta["taker_buy_ratio"].tail(400))
        storage.log_fetch(inst["id"], iv, res.source, res.status, res.delay, res.message)


ONCHAIN_REFRESH_H = 12
ONCHAIN_COLS = ("sopr", "nvt", "mvrv", "ex_netflow_usd", "ex_inflow_usd", "ex_outflow_usd", "active_addresses", "market_cap", "nvt_proxy")


def _fresh(iid, iv, hours):
    info = storage.fetch_info(iid, iv)
    return bool(info) and (pd.Timestamp.now("UTC") - pd.Timestamp(info["fetched_at"])).total_seconds() < hours * 3600


def fetch_onchain(inst: dict, force=False):
    """Metriche on-chain gratuite (CoinMetrics community): ogni 12 ore, anche se la moneta non è coperta
    (in quel caso resta registrato l'errore e i segnali usano solo tecnica e sentiment)."""
    if _fresh(inst["id"], "onchain", ONCHAIN_REFRESH_H) and not force:
        return
    res = COINMETRICS.metrics(inst.get("cm") or inst["id"].lower())
    if res.status == "ok":
        for col in res.df.columns:
            storage.save_series(f"onchain:{inst['id']}:{col}", res.df[col])
    storage.log_fetch(inst["id"], "onchain", res.source, res.status, res.delay, res.message)
    asset = (inst.get("cm") or inst["id"]).lower()
    if asset in Glassnode.ASSETS:      # SOPR e NVT reali: solo con chiave valida; senza, restano i proxy gratuiti
        gn = GLASSNODE.metrics(asset)
        if gn.status == "ok":
            for col in ("sopr", "nvt"):
                if col in gn.df:
                    storage.save_series(f"onchain:{inst['id']}:{col}", gn.df[col])
        storage.log_fetch(inst["id"], "glassnode", gn.source, gn.status, gn.delay, gn.message)


def fetch_fear_greed(force=False):
    if _fresh("_global", "fear_greed", 6) and not force:
        return
    res = FEARGREED.index()
    if res.status == "ok":
        storage.save_series("fear_greed:global", res.df["fear_greed"])
    storage.log_fetch("_global", "fear_greed", res.source, res.status, res.delay, res.message)


# ------------------------------------------------------------------ analisi di un asset
def _freshness(inst, df, info):
    if df.empty:
        return "mancante", 0.0
    last = df.index[-1]
    age_days = (pd.Timestamp.now("UTC") - last).total_seconds() / 86400
    limit = 40 if inst.get("frequency") == "monthly" else (2 if inst["source"] == "binance" else 5)
    if info and info.get("status") != "ok":
        return f"obsoleto (ultimo aggiornamento fallito: {info.get('message', '')[:80]})", 0.4
    if age_days > limit:
        return f"obsoleto (ultima barra {last.date()})", 0.5
    return "aggiornato", 1.0


def analyze_instrument(inst: dict, cfg: dict, db_path: str) -> dict:
    """Eseguita in un processo separato: legge da SQLite, calcola, restituisce un dict JSON-serializzabile."""
    storage.init(Path(db_path))
    storage._KV_CACHE.clear()   # processo separato: non fidarsi della cache ereditata
    df = storage.load_bars(inst["id"], "1d")
    info = storage.fetch_info(inst["id"], "1d")
    fresh, reliability = _freshness(inst, df, info)
    meta = {"id": inst["id"], "name": inst["name"], "market": inst["market"], "source": info["source"] if info else inst["source"],
            "delay": info["delay"] if info else None, "fetched_at": info["fetched_at"] if info else None,
            "fetch_status": info["status"] if info else "mai scaricato", "fetch_message": info["message"] if info else "",
            "n_bars": int(len(df)), "first_bar": str(df.index[0].date()) if len(df) else None,
            "last_bar": str(df.index[-1].date()) if len(df) else None, "freshness": fresh,
            "data_type": "reale", "monthly": inst.get("frequency") == "monthly", "kind": inst.get("kind", "price")}
    if inst["source"] == "binance" and len(df):
        meta["note"] = "L'ultima barra giornaliera è in formazione (giornata UTC non chiusa)."
    out = {"meta": meta, "unavailable": []}
    if len(df) < 60:
        out["error"] = "dati insufficienti per l'analisi"
        return out
    level = inst.get("kind") == "yield" or (df["close"] <= 0).any()
    # per i segnali usiamo solo barre chiuse: per Binance scartiamo la barra in formazione
    dfa = df.iloc[:-1] if inst["source"] == "binance" else df
    feat = ind.feature_frame(dfa)
    last = feat.iloc[-1]
    c = dfa["close"]
    ret = feat["ret1"]
    price = float(c.iloc[-1])
    meta["last_close"] = price
    meta["chg_1d"] = float(c.iloc[-1] - c.iloc[-2]) if level else float(c.iloc[-1] / c.iloc[-2] - 1)

    def chg(n):
        if len(c) <= n:
            return None
        return float(c.iloc[-1] - c.iloc[-1 - n]) if level else float(c.iloc[-1] / c.iloc[-1 - n] - 1)
    meta["chg"] = {"1g": chg(1), "1s": chg(5), "1m": chg(21), "3m": chg(63), "1a": chg(252)}
    meta["level_series"] = bool(level)

    # ---------------- A. tecnica
    sma50, sma200 = ind.sma(c, 50), ind.sma(c, 200)
    wk = dfa.resample("W-FRI").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["close"])
    wsma = ind.sma(wk["close"], 30)

    def trend(x, m):
        if not np.isfinite(m.iloc[-1]):
            return "n.d."
        slope = m.iloc[-1] - m.iloc[-6] if len(m) > 6 else 0
        if x.iloc[-1] > m.iloc[-1] and slope > 0: return "rialzista"
        if x.iloc[-1] < m.iloc[-1] and slope < 0: return "ribassista"
        return "laterale/incerto"
    tech = {
        "trend_primario": trend(wk["close"], wsma) if len(wk) > 40 else trend(c, sma200),
        "trend_secondario": trend(c, sma50),
        "rsi14": float(last["rsi14"]), "adx": float(last["adx"]), "di_diff": float(last["di_diff"]),
        "macd_hist_norm": float(last["macd_hist_n"]) if np.isfinite(last["macd_hist_n"]) else None,
        "atr": float(last["atr"]), "atr_pct": float(last["atr_pct"] * 100),
        "bb_pos": float(last["bb_pos"]), "bb_width_pct": float(last["bb_width_pct"]) if np.isfinite(last["bb_width_pct"]) else None,
        "sma50": float(sma50.iloc[-1]) if np.isfinite(sma50.iloc[-1]) else None,
        "sma200": float(sma200.iloc[-1]) if np.isfinite(sma200.iloc[-1]) else None,
        "vwap20_dist_atr": float(last["vwap20_dist"]) if np.isfinite(last["vwap20_dist"]) else None,
        "hi55": float(last["hi55"]), "lo55": float(last["lo55"]),
    }
    sup, res = ind.swing_levels(dfa)
    tech["supporti"], tech["resistenze"] = [float(x) for x in sup], [float(x) for x in res]
    tech["fibonacci"] = ind.fibonacci(dfa)
    h1 = storage.load_bars(inst["id"], "1h")
    if len(h1) > 50:
        h1c = h1.iloc[:-1]["close"]
        tech["trend_intraday"] = trend(h1c, ind.sma(h1c, 20))
        tech["intraday_source"] = "barre orarie Binance"
    else:
        tech["trend_intraday"] = None
        out["unavailable"].append({"what": "Analisi intraday", "reason": "barre intraday disponibili solo per le crypto (Binance) nella fase 1"})
    tf = [tech["trend_primario"], tech["trend_secondario"], tech.get("trend_intraday")]
    tf = [x for x in tf if x and x != "n.d."]
    tech["allineamento_timeframe"] = "allineati" if len(set(tf)) == 1 else "non allineati"
    if dfa["volume"].isna().all():
        out["unavailable"].append({"what": "Volumi, VWAP, profilo dei volumi", "reason": "la fonte non fornisce volumi per questo strumento"})
    else:
        # profilo dei volumi (ultimi 120 giorni): volume per fascia di prezzo
        d = dfa.tail(120)
        bins = np.linspace(d["low"].min(), d["high"].max(), 25)
        tp = (d["high"] + d["low"] + d["close"]) / 3
        hist, _ = np.histogram(tp, bins=bins, weights=d["volume"].fillna(0))
        poc = float((bins[hist.argmax()] + bins[hist.argmax() + 1]) / 2)
        tech["volume_profile"] = {"bins": [float(x) for x in bins], "volume": [float(x) for x in hist], "poc": poc}
    out["technical"] = tech

    # ---------------- B. statistica
    g = sm.garch11_fit(ret.dropna().values) if not meta["monthly"] else None
    stat = {
        "zscore20": float(last["z20"]), "zscore_sma200": float(last["z_sma200"]) if np.isfinite(last["z_sma200"]) else None,
        "regression_120g": sm.regression_channel(c) if not level else None,
        "distribution": sm.distribution_stats(ret),
        "vol_regime": sm.vol_regime(ret),
        "regime_breaks": sm.cusum_breaks(ret),
        "mean_reversion": sm.ou_half_life(c if level else np.log(c)),
    }
    if g:
        stat["garch"] = {k: float(v) for k, v in g.items() if k != "cond_vol_series"}
        stat["garch"]["vol_ann_now"] = float(np.sqrt(g["h_next"] * 252))
        stat["garch"]["vol_ann_long_run"] = float(np.sqrt(g["long_run_var"] * 252))
    out["statistical"] = stat

    # ---------------- C. sistemi complessi
    r1 = ret.dropna().values
    h_all = sm.dfa_hurst(r1[-2000:])
    h_recent = sm.dfa_hurst(r1[-500:])
    alpha, k = sm.hill_tail_index(r1[-3000:])
    out["complexity"] = {
        "hurst_dfa": {"value": h_all, "recent_500": h_recent, "validity": "validato",
                      "note": "Stimatore DFA. 0,5 = nessuna memoria; con 500-2000 osservazioni l'errore è circa ±0,05, quindi valori tra 0,45 e 0,55 non indicano memoria."},
        "permutation_entropy": {"value": sm.permutation_entropy(r1[-500:]), "validity": "sperimentale",
                                "note": "1 = indistinguibile dal rumore. Misura valida; il legame con rendimenti futuri non è dimostrato."},
        "tail_alpha": {"value": alpha, "k": k, "validity": "validato",
                       "note": "Esponente di Hill della coda dei rendimenti assoluti. Valori 2,5-4 sono tipici; più basso = eventi estremi più frequenti."},
        "early_warning": dict(sm.early_warning(feat) or {}, validity="sperimentale",
                              note="Aumento congiunto di autocorrelazione e varianza (rallentamento critico). Evidenza mista sui mercati."),
        "fractal_dimension": {"value": (2 - h_all) if np.isfinite(h_all) else None, "validity": "analogia",
                              "note": "D = 2 - H per un moto browniano frazionario: è una riscrittura di Hurst, non un'informazione nuova."},
    }

    # ---------------- D. quantitativa
    q = {f"mom_{n}g_volaj": float(last[f"mom{n}"]) if np.isfinite(last[f"mom{n}"]) else None for n in (21, 63, 126, 252)}
    q["volume_z"] = float(last["vol_z"]) if np.isfinite(last["vol_z"]) else None
    q["cmf20"] = float(last["cmf20"]) if np.isfinite(last["cmf20"]) else None
    tb = storage.load_series(f"takerbuy:{inst['id']}:1d")
    if len(tb) > 30:
        q["taker_buy_ratio_7g"] = float(tb.tail(8).iloc[:-1].mean())
        q["taker_buy_ratio_90g"] = float(tb.tail(91).iloc[:-1].mean())
        q["taker_note"] = "Quota di volume eseguito da ordini aggressivi in acquisto su Binance: proxy di order flow su un solo exchange."
    out["quant"] = q
    for what, reason in (
        ("Opzioni: volatilità implicita, gamma exposure, put/call ratio", "richiede dati opzioni (Massive Options, Unusual Whales) - fase 3"),
        ("Order flow e liquidità del book", "richiede dati tick/quote (Massive Advanced) - fase 3" if inst["source"] != "binance" else "book completo non scaricato nella fase 1"),
        ("Short interest", "richiede FMP/EODHD o dati FINRA - fase 2"),
        ("Flussi ETF e istituzionali", "nessuna fonte gratuita affidabile - fase 2/3"),
    ):
        out["unavailable"].append({"what": what, "reason": reason})
    if inst["source"] == "binance":
        out["unavailable"].append({"what": "Flussi on-chain e movimenti delle balene", "reason": "richiede Glassnode Professional o simili - fase 4"})
        out["unavailable"].append({"what": "Open interest e liquidazioni dei derivati crypto", "reason": "richiede CoinGlass o API futures - fase 3"})

    # ---------------- previsioni
    preds = {}
    if not meta["monthly"]:
        for hname, h in HORIZONS.items():
            if hname not in cfg["horizons"]:
                continue
            wf = walk_forward(feat, c, h, level=level, step=63 if h <= 21 else 126)
            sig_h = sm.garch_forecast_vol(g, h) if g else float(last["vol"] * np.sqrt(h))
            wf["expected_vol_pct"] = float(sig_h * 100) if not level else None
            wf["expected_move_abs"] = float(sig_h * (1 if level else price))
            wf["bands"] = sm.monte_carlo_bands(ret, price, h, sig_h) if not level else {
                "p5": price - 1.645 * sig_h, "p50": price, "p95": price + 1.645 * sig_h}
            preds[hname] = wf
        if len(h1) > 700:
            f1 = ind.feature_frame(h1.iloc[:-1])
            for hname, h in INTRADAY_HORIZONS.items():
                if hname in cfg["horizons"]:
                    wf = walk_forward(f1, h1.iloc[:-1]["close"], h, step=100)
                    wf["timeframe"] = "1h"
                    preds[hname] = wf
    else:
        out["unavailable"].append({"what": "Previsioni", "reason": "serie mensile: troppo poche osservazioni per una validazione fuori campione"})
    out["predictions"] = preds
    out["levels"] = {"inversione_supporti": tech["supporti"], "inversione_resistenze": tech["resistenze"],
                     "accelerazione_sopra": tech["hi55"], "accelerazione_sotto": tech["lo55"],
                     "note": "Livelli tecnici (minimi/massimi locali e estremi a 55 giorni) dove storicamente si concentrano inversioni o accelerazioni. Non sono previsioni."}

    # ---------------- segnali e opportunità
    sigs = active_signals(feat, cfg["enabled_rules"])
    if "model_edge" in cfg["enabled_rules"]:
        for hname, wf in preds.items():
            if wf.get("shown_as_probability") and wf.get("probabilities"):
                base = wf["models"]["base_rate"].get("current")
                pr = wf["probabilities"]
                for cls_i, cls, dirn in ((2, "up", 1), (0, "down", -1)):
                    if base and pr[cls] - base[cls_i] > 0.08:
                        sel = wf["models"][wf["selected"]]
                        sigs.append({"rule": "model_edge", "name": f"Modello: probabilità di {'rialzo' if dirn > 0 else 'ribasso'} sopra la media ({hname})",
                                     "family": "predittiva", "direction": dirn, "intensity": float(min(100, (pr[cls] - base[cls_i]) * 500)),
                                     "horizon_bars": wf["horizon_bars"],
                                     "why": f"Il modello {wf['selected']} stima {pr[cls]:.0%} contro un tasso storico del {base[cls_i]:.0%}; skill fuori campione {sel['brier_skill']:+.3f} su {sel['n_oos']} previsioni.",
                                     "invalidation": "Il prezzo esce dalla banda 5-95% prevista o la skill del modello scende sotto zero al prossimo ricalcolo."})
    out["signals"] = sigs
    try:
        out["simple"] = build_plans(inst, dfa, feat, sigs, cfg["cost_bps"], cfg["slippage_bps"])
    except Exception as e:
        out["simple"] = {"tradable": False, "reason": f"errore nel calcolo dei piani: {e}", "plans": []}
    atr_pct = float(last["atr_pct"] * 100) if not level else None
    opp = score_opportunity(sigs, cfg["weights"], None, atr_pct or 0, reliability)
    if opp:
        best_h = None
        for hn in ("1 mese", "1 settimana", "1-3g"):
            if hn in preds:
                best_h = hn; break
        opp.update({"risk_atr_pct": atr_pct, "reliability": reliability,
                    "potential_move": preds[best_h]["expected_move_abs"] if best_h else None,
                    "potential_horizon": best_h,
                    "probability": (preds[best_h]["probabilities"] if best_h and preds[best_h].get("shown_as_probability") else None)})
    out["opportunity"] = opp
    # ---------------- segnale AHA (tecnica + on-chain + sentiment) con prova storica
    try:
        oc = {c: storage.load_series(f"onchain:{inst['id']}:{c}") for c in ONCHAIN_COLS}
        out["aha"] = signals.build_signal(dfa, {k: v for k, v in oc.items() if len(v)}, storage.load_series("fear_greed:global"))
    except Exception as e:
        out["aha"] = {"direction": 0, "action": "ERRORE", "why": f"errore nel calcolo del segnale: {e}"}
    # serie per i grafici (ultimi 3 anni)
    tail = dfa.tail(756)
    m, ub, lb, _ = ind.bollinger(dfa["close"])
    out["chart"] = {"t": [str(x.date()) for x in tail.index], "o": tail["open"].round(6).tolist(), "h": tail["high"].round(6).tolist(),
                    "l": tail["low"].round(6).tolist(), "c": tail["close"].round(6).tolist(),
                    "v": tail["volume"].fillna(0).round(0).tolist(),
                    "sma50": sma50.tail(756).round(6).tolist(), "sma200": sma200.tail(756).round(6).tolist(),
                    "bb_up": ub.tail(756).round(6).tolist(), "bb_lo": lb.tail(756).round(6).tolist(),
                    "rsi": feat["rsi14"].tail(756).round(2).tolist(), "vwap": ind.rolling_vwap(dfa).tail(756).round(6).tolist()}
    if g:
        cv = g["cond_vol_series"][-756:] * np.sqrt(252) * 100
        out["chart"]["garch_vol"] = np.round(cv, 2).tolist()
    out["_returns"] = {str(k.date()): float(v) for k, v in ret.tail(800).dropna().items()}
    out["_features_last"] = {k: (float(v) if np.isfinite(v) else None) for k, v in last.items() if isinstance(v, (int, float, np.floating))}
    return clean(out)


# ------------------------------------------------------------------ alert
def _scenarios(a):
    lv = a["levels"]; meta = a["meta"]
    p = a["predictions"].get("1 settimana") or next(iter(a["predictions"].values()), None)
    band = p.get("bands") if p else None
    up = f"sopra {lv['accelerazione_sopra']:.4g} (massimo a 55 giorni) il movimento può accelerare" if lv.get("accelerazione_sopra") else ""
    dn = f"sotto {lv['accelerazione_sotto']:.4g} (minimo a 55 giorni) può accelerare al ribasso" if lv.get("accelerazione_sotto") else ""
    rng = f"intervallo 5-95% a una settimana: {band['p5']:.4g} - {band['p95']:.4g}" if band else ""
    return [x for x in (up, dn, rng) if x]


def make_alerts(a: dict, prev: dict | None, cfg: dict) -> int:
    if "signals" not in a:
        return 0
    kinds = set(cfg["alert_kinds"]); iid = a["meta"]["id"]; asof = a["meta"]["last_bar"]; n = 0
    base = {"asset": a["meta"]["name"], "price": a["meta"].get("last_close"), "scenarios": _scenarios(a),
            "data": f"{a['meta']['source']}, ultima barra {asof}, {a['meta']['freshness']}"}

    def add(kind, sev, what, indicators, why, invalid):
        nonlocal n
        if kind in kinds:
            n += storage.add_alert(iid, kind, asof, sev, dict(base, what=what, indicators=indicators, why=why, invalidation=invalid))
    st, f = a["statistical"], a["_features_last"]
    if f.get("z20") is not None and abs(f["z20"]) > 3:
        add("anomalia", 70, f"Movimento anomalo: z-score a 20 giorni {f['z20']:+.1f}", ["z-score 20g"],
            "Scostamenti oltre 3 deviazioni standard sono rari in una distribuzione normale (~0,3%); con code pesanti sono più frequenti ma restano eventi da monitorare.",
            "Rientro dello z-score entro ±2.")
    for s in a["signals"]:
        kind = {"volume_spike": "volume", "vol_regime_up": "regime", "bear_div": "divergenza", "bull_div": "divergenza"}.get(s["rule"])
        if kind:
            add(kind, s["intensity"], s["name"], [s["name"]], s["why"], s["invalidation"])
    # inversione di flusso (proxy): cambio di segno del CMF con intensità
    if prev and prev.get("_features_last", {}).get("cmf20") is not None and f.get("cmf20") is not None:
        c0, c1 = prev["_features_last"]["cmf20"], f["cmf20"]
        if np.sign(c0) != np.sign(c1) and abs(c1) > 0.1:
            add("flusso", 50, f"Inversione del flusso prezzo-volume (CMF da {c0:+.2f} a {c1:+.2f})", ["Chaikin Money Flow 20g"],
                "Il saldo tra chiusure nella parte alta e bassa del range, pesato per volume, ha cambiato segno. È una stima indiretta, non un flusso di capitali misurato.",
                "CMF che torna al segno precedente entro 5 sedute.")
    if st.get("regime_breaks"):
        last_b = st["regime_breaks"][-1]
        if last_b["date"][:10] >= str((pd.Timestamp(asof) - pd.Timedelta(days=5)).date()):
            add("regime", 55, f"Cambio di regime di varianza (CUSUM): {last_b['type']}", ["CUSUM sulla varianza"],
                "Il test sequenziale CUSUM ha rilevato una variazione persistente della varianza dei rendimenti.", "Varianza che torna al livello precedente.")
    opp = a.get("opportunity")
    if opp and opp["convergence"]:
        dirn = "rialzo" if opp["direction"] > 0 else "ribasso"
        fams = [s["name"] for s in a["signals"] if s["direction"] == opp["direction"]]
        add("convergenza", 85, f"{opp['n_families_agree']} famiglie di analisi indipendenti indicano {dirn}", fams,
            "Più metodi con basi diverse (tecnica, statistica, quantitativa, posizionamento, modello) concordano: la convergenza riduce il rischio che il segnale sia rumore di un solo metodo, non lo elimina.",
            "Anche uno solo dei livelli di invalidazione dei segnali componenti viene superato.")
    for w in cfg.get("watchlist", []):
        if w.get("id") != iid:
            continue
        px = a["meta"].get("last_close")
        if w.get("above") not in (None, "") and px is not None and px > float(w["above"]):
            add("watchlist", 90, f"Prezzo sopra la soglia impostata ({w['above']})", ["soglia utente"], "Soglia della watchlist superata.", "Ritorno sotto la soglia.")
        if w.get("below") not in (None, "") and px is not None and px < float(w["below"]):
            add("watchlist", 90, f"Prezzo sotto la soglia impostata ({w['below']})", ["soglia utente"], "Soglia della watchlist superata.", "Ritorno sopra la soglia.")
    return n


# ------------------------------------------------------------------ registro e verifica previsioni
def log_predictions(a: dict):
    if not a.get("predictions"):
        return
    for hname, wf in a["predictions"].items():
        for m in {wf["selected"], "base_rate"}:
            r = wf["models"].get(m, {})
            cur = r.get("current")
            if not cur:
                continue
            storage.log_prediction({
                "inst": a["meta"]["id"], "horizon": hname, "horizon_bars": wf["horizon_bars"], "model": m,
                "created_at": storage.utcnow(), "data_asof": r.get("asof"), "close_asof": None,
                "threshold": wf.get("threshold_now"), "p_down": cur[0], "p_flat": cur[1], "p_up": cur[2],
                "skill": r.get("brier_skill"), "shown_as_probability": int(bool(wf.get("shown_as_probability") and m == wf["selected"]))})


def resolve_predictions():
    for p in storage.open_predictions():
        tf = "1h" if p["horizon"].startswith("intraday") else "1d"
        df = storage.load_bars(p["inst"], tf)
        if df.empty or not p["data_asof"]:
            continue
        t0 = pd.Timestamp(p["data_asof"])
        if t0.tzinfo is None:
            t0 = t0.tz_localize("UTC")
        after = df[df.index > t0]
        if len(after) <= p["horizon_bars"]:   # serve una barra CHIUSA oltre l'orizzonte
            continue
        c0 = df.loc[df.index <= t0, "close"].iloc[-1]
        c1 = after["close"].iloc[p["horizon_bars"] - 1]
        inst = instrument(p["inst"]) or {}
        level = inst.get("kind") == "yield"
        r = float(c1 - c0) if level else float(np.log(c1 / c0))
        thr = p["threshold"] or 0
        cls = "up" if r > thr else "down" if r < -thr else "flat"
        storage.resolve_prediction(p["id"], r, cls)


def live_calibration():
    rows = storage.predictions(resolved_only=True)
    if not rows:
        return {"n": 0}
    df = pd.DataFrame(rows)
    out = {"n": int(len(df)), "by_model": {}}
    for m, g in df.groupby("model"):
        P = g[["p_down", "p_flat", "p_up"]].values
        y = g["realized_class"].map({"down": 0, "flat": 1, "up": 2}).values
        Y = np.eye(3)[y]
        out["by_model"][m] = {"n": int(len(g)), "brier": float(((P - Y) ** 2).sum(1).mean()),
                              "hit_rate": float((P.argmax(1) == y).mean())}
    return out


# ------------------------------------------------------------------ globale
def global_analysis(results: dict):
    rets = {}
    for iid, a in results.items():
        if a.get("_returns") and not a["meta"].get("monthly") and not a["meta"].get("level_series"):
            rets[a["meta"]["name"]] = pd.Series(a["_returns"])
    R = pd.DataFrame(rets)
    R.index = pd.to_datetime(R.index)
    R = R.sort_index()
    net = sm.correlation_network(R) if len(R) else None
    arh = sm.absorption_ratio_history(R) if len(R) else pd.Series(dtype=float)
    out = {}
    if net:
        out["network"] = {k: v for k, v in net.items() if k != "corr"}
        out["network"]["corr_matrix"] = {"labels": list(net["corr"].columns), "z": np.round(net["corr"].values, 3).tolist()}
        out["absorption_ratio_history"] = {str(k.date()): round(float(v), 4) for k, v in arh.items()}
        if len(arh) > 20:
            out["network"]["absorption_ratio_percentile"] = float((arh < arh.iloc[-1]).mean() * 100)
    out["spillover"] = {t: sm.lead_lag_spillover(R, t) for t in ("Bitcoin", "Ethereum") if t in R}
    # rotazione: performance relativa per gruppo (proxy di dove va il capitale)
    groups = {}
    for iid, a in results.items():
        m = a.get("meta", {})
        if not m.get("chg") or m.get("level_series"):
            continue
        groups.setdefault(m["market"], []).append(m["chg"])
    rot = {}
    for g, lst in groups.items():
        rot[g] = {h: float(np.nanmean([x[h] for x in lst if x.get(h) is not None])) if any(x.get(h) is not None for x in lst) else None
                  for h in ("1s", "1m", "3m")}
    out["rotation"] = rot
    # dominanza e forza relativa: Bitcoin contro la media delle altcoin
    def g(iid, h="1m"):
        a = results.get(iid); return a["meta"]["chg"].get(h) if a and "chg" in a.get("meta", {}) else None
    btc = g("BTC"); alts = [x for x in (g(i) for i in results if i != "BTC") if x is not None]
    if btc is not None and alts:
        out["btc_vs_alts_1m"] = {"btc": float(btc), "alts_mean": float(np.mean(alts)), "spread": float(btc - np.mean(alts))}
    return out


# thread di download per fonte: rispettano comunque i limiti di frequenza di ciascuna API
FETCH_WORKERS = {"binance": 4}


def download_all(insts, force=False):
    """Scarica i prezzi in parallelo (thread per fonte, nel rispetto dei limiti di ciascuna API)."""
    n_done = [0]
    lock = threading.Lock()

    def one(inst):
        try:
            fetch_instrument(inst, force)
        except Exception as e:
            storage.log_fetch(inst["id"], "1d", inst["source"], "errore", "-", str(e)[:300])
        with lock:
            n_done[0] += 1
            STATE["progress"] = f"download {n_done[0]}/{len(insts)} ({inst['name']})"

    def onchain():
        try:
            fetch_fear_greed(force)
        except Exception as e:
            log.warning("fear&greed: %s", e)
        for inst in insts:
            try:
                fetch_onchain(inst, force)
            except Exception as e:
                storage.log_fetch(inst["id"], "onchain", "CoinMetrics", "errore", "-", str(e)[:300])

    oc_thread = threading.Thread(target=onchain, name="dl-onchain", daemon=True)
    oc_thread.start()
    pools = {src: ThreadPoolExecutor(max_workers=n, thread_name_prefix=f"dl-{src}") for src, n in FETCH_WORKERS.items()}
    other = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dl-other")
    try:
        futs = [pools.get(inst["source"], other).submit(one, inst) for inst in insts]
        for f in futs:
            f.result()
    finally:
        for p in list(pools.values()) + [other]:
            p.shutdown(wait=True)
    oc_thread.join(timeout=300)


_cycle_lock = threading.Lock()


def run_cycle(force=False):
    # il lock evita due cicli contemporanei (pulsante "Aggiorna" premuto mentre parte quello programmato)
    if not _cycle_lock.acquire(blocking=False):
        return
    STATE.update(running=True, last_error=None, progress="download dati")
    try:
        cfg = settings()
        insts = instruments()
        STATE["total"] = len(insts); STATE["done"] = 0
        download_all(insts, force)
        STATE["progress"] = "analisi"
        results, prev_all = {}, storage.kv_get("analysis_index") or {}
        db = str(storage.DB_PATH)
        todo = []
        for inst in insts:
            prev = storage.kv_get(f"analysis:{inst['id']}")
            df_last = storage.load_bars(inst["id"], "1d").tail(1)
            last = str(df_last.index[-1]) if len(df_last) else None
            key = f"{ENGINE_VERSION}|{last}|{json.dumps(cfg['horizons'])}|{json.dumps(cfg['enabled_rules'])}|{json.dumps(cfg['weights'])}"
            if prev and prev.get("_key") == key and not force and inst["source"] != "binance":
                results[inst["id"]] = prev
                STATE["done"] += 1
            else:
                todo.append((inst, key, prev))
        workers = max(1, (os.cpu_count() or 2) - 1)
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(analyze_instrument, inst, cfg, db): (inst, key, prev) for inst, key, prev in todo}
            for fu in as_completed(futs):
                inst, key, prev = futs[fu]
                try:
                    a = fu.result()
                    a["_key"] = key
                    a["meta"]["analyzed_at"] = storage.utcnow()
                    storage.kv_set(f"analysis:{inst['id']}", a)
                    results[inst["id"]] = a
                    if "signals" in a:
                        make_alerts(a, prev, cfg)
                        log_predictions(a)
                except Exception as e:
                    log.error("analisi %s: %s", inst["id"], traceback.format_exc())
                    results[inst["id"]] = {"meta": {"id": inst["id"], "name": inst["name"], "market": inst["market"]},
                                           "error": f"errore di analisi: {e}"}
                STATE["done"] += 1
                STATE["progress"] = f"analisi {STATE['done']}/{STATE['total']}"
        STATE["progress"] = "rete delle correlazioni"
        storage.kv_set("global", clean(global_analysis(results)))
        storage.kv_set("analysis_index", list(results.keys()))
        STATE["progress"] = "validazione sul pool di monete"
        try:
            val = clean(validation.run(results))
            storage.kv_set("aha_validation", val)
            for iid, a in results.items():       # l'affidabilità di ogni segnale è quella del pool per il suo verso
                if a.get("aha"):
                    validation.attach(a["aha"], val)
                    storage.kv_set(f"analysis:{iid}", a)
            tracking.record(results)             # registro in avanti: la verifica fuori campione vera
            tracking.evaluate(lambda c: storage.load_bars(c, "1d").iloc[:-1])
        except Exception:
            log.error("validazione: %s", traceback.format_exc())
        resolve_predictions()
        STATE["last_run"] = storage.utcnow()
        storage.kv_set("last_run", STATE["last_run"])   # sopravvive al riavvio del server
        STATE["progress"] = "completato"
    except Exception as e:
        STATE["last_error"] = traceback.format_exc()
        log.error(STATE["last_error"])
    finally:
        STATE["running"] = False
        _cycle_lock.release()


def scheduler():
    while True:
        run_cycle()
        time.sleep(max(5, settings()["refresh_minutes"]) * 60)


def start_scheduler():
    t = threading.Thread(target=scheduler, daemon=True)
    t.start()
    return t
