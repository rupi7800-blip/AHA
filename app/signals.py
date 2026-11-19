"""Motore dei segnali AHA: analisi tecnica (EMA, RSI, MACD, Bollinger) + on-chain + sentiment.

Per ogni moneta:
1. ogni indicatore tecnico vota +1 (rialzo), -1 (ribasso) o 0;
2. i parametri di ciascun indicatore sono scelti su una griglia usando SOLO il primo 60% della storia;
3. le metriche on-chain (MVRV, flussi exchange) e il sentiment (Fear & Greed) votano con soglie esplicite;
4. il segnale composito scatta quando la somma dei voti raggiunge `min_votes`;
5. il segnale è provato sull'intera storia con gli stessi stop/target che propone (ATR), costi inclusi, e
   le percentuali di successo sono riportate separatamente per il periodo di scelta dei parametri (in campione)
   e per il restante 40% (fuori campione). L'affidabilità dipende SOLO dal periodo fuori campione.

Tutto è causale: il voto al tempo t usa solo dati fino alla chiusura di t (on-chain ritardato di un giorno)."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .analysis import indicators as ind

TRAIN_FRAC = 0.6
MIN_TUNE_N = 30

DEFAULTS = {
    "ema_fast": 20, "ema_slow": 50,
    "rsi_n": 14, "rsi_lo": 30.0, "rsi_hi": 70.0,
    "macd_f": 12, "macd_s": 26, "macd_sig": 9,
    "bb_n": 20, "bb_k": 2.0,
    "mvrv_lo": 1.0, "mvrv_hi": 3.5,          # soglie storiche indicate: <1 zona di fondo, >3,5 zona di picco
    "sopr_lo": 1.0, "sopr_hi": 1.03,          # SOPR (media 7g): <1 vendite in perdita (capitolazione), alto = presa di profitto
    "nvt_pct": 0.9,                           # NVT sopra il 90° percentile (o sotto il 10°) dell'ultimo anno
    "flow_z": 2.0,                            # z-score (90g) del netflow exchange a 7g
    "fg_lo": 20.0, "fg_hi": 80.0,             # Fear & Greed: paura/avidità estreme (contrarian)
    "horizon": 14, "atr_stop": 2.0, "atr_tp": 3.0, "min_votes": 2,
    "cost_bps": 5.0, "slippage_bps": 5.0,
}

GRIDS = {
    "ema": [dict(ema_fast=f, ema_slow=s) for f, s in [(10, 30), (12, 26), (20, 50), (50, 200)]],
    "rsi": [dict(rsi_n=n, rsi_lo=lo, rsi_hi=hi) for n, lo, hi in [(14, 30, 70), (14, 25, 75), (9, 30, 70), (21, 35, 65)]],
    "macd": [dict(macd_f=f, macd_s=s, macd_sig=g) for f, s, g in [(12, 26, 9), (8, 21, 5), (19, 39, 9)]],
    "bb": [dict(bb_n=n, bb_k=k) for n, k in [(20, 2.0), (20, 2.5), (14, 2.0), (30, 2.0)]],
}


# ------------------------------------------------------------------ voti tecnici
def vote_ema(df, p):
    f, s = ind.ema(df["close"], int(p["ema_fast"])), ind.ema(df["close"], int(p["ema_slow"]))
    return np.sign(f - s).fillna(0)


def vote_rsi(df, p):
    r = ind.rsi(df["close"], int(p["rsi_n"]))
    return pd.Series(np.where(r < p["rsi_lo"], 1, np.where(r > p["rsi_hi"], -1, 0)), index=df.index).where(r.notna(), 0)


def vote_macd(df, p):
    m = ind.macd(df["close"], int(p["macd_f"]), int(p["macd_s"]), int(p["macd_sig"]))
    line, sig = m[0], m[1]
    return np.sign(line - sig).fillna(0)


def vote_bb(df, p):
    _, up, lo, _w = ind.bollinger(df["close"], int(p["bb_n"]), float(p["bb_k"]))
    c = df["close"]
    return pd.Series(np.where(c < lo, 1, np.where(c > up, -1, 0)), index=df.index).where(up.notna(), 0)


VOTERS = {"ema": vote_ema, "rsi": vote_rsi, "macd": vote_macd, "bb": vote_bb}


def _fwd(close: pd.Series, h: int) -> pd.Series:
    return close.shift(-h) / close - 1


def tune(df: pd.DataFrame, base: dict, train_frac=TRAIN_FRAC) -> tuple[dict, dict]:
    """Sceglie per ogni indicatore la combinazione di parametri con la migliore statistica t
    del rendimento a `horizon` barre, usando solo il periodo di stima. Restituisce (parametri, dettagli)."""
    h = int(base["horizon"])
    n_train = int(len(df) * train_frac)
    fwd = _fwd(df["close"], h).iloc[:n_train - h]
    params, detail = dict(base), {}
    for name, grid in GRIDS.items():
        best, best_t, rows = None, -np.inf, []
        for g in grid:
            v = VOTERS[name](df, dict(base, **g)).iloc[:n_train - h]
            m = v != 0
            if m.sum() < MIN_TUNE_N:
                continue
            x = (v[m] * fwd[m]).dropna()
            if len(x) < MIN_TUNE_N or x.std() == 0:
                continue
            t = float(x.mean() / x.std() * np.sqrt(len(x)))
            rows.append({"params": g, "t": round(t, 2), "n": int(len(x))})
            if t > best_t:
                best, best_t = g, t
        if best:
            params.update(best)
        detail[name] = {"chosen": best or {k: base[k] for k in grid[0]}, "t_train": None if best is None else round(best_t, 2),
                        "tested": rows, "note": "scelto sul primo 60% della storia" if best else "dati insufficienti: parametri predefiniti"}
    return params, detail


# ------------------------------------------------------------------ voti on-chain e sentiment
def _align(s: pd.Series | None, index: pd.DatetimeIndex, lag=1) -> pd.Series | None:
    if s is None or len(s) == 0:
        return None
    s = s.copy(); s.index = pd.to_datetime(s.index, utc=True).normalize()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s.reindex(index.normalize()).ffill(limit=3).shift(lag).set_axis(index)


def onchain_votes(df: pd.DataFrame, onchain: dict[str, pd.Series], fear_greed: pd.Series | None, p: dict) -> dict[str, pd.Series]:
    """Voti dalle metriche disponibili. Le metriche mancanti semplicemente non votano."""
    out = {}
    mv = _align(onchain.get("mvrv"), df.index)
    if mv is not None and mv.notna().any():
        out["mvrv"] = pd.Series(np.where(mv < p["mvrv_lo"], 1, np.where(mv > p["mvrv_hi"], -1, 0)), index=df.index).where(mv.notna(), 0)
    nf = _align(onchain.get("ex_netflow_usd"), df.index)
    if nf is not None and nf.notna().sum() > 120:
        n7 = nf.rolling(7, min_periods=5).sum()
        z = (n7 - n7.rolling(90, min_periods=60).mean()) / n7.rolling(90, min_periods=60).std()
        # afflusso netto insolito verso gli exchange = pressione di vendita; deflusso = accumulo
        out["exchange_flow"] = pd.Series(np.where(z > p["flow_z"], -1, np.where(z < -p["flow_z"], 1, 0)), index=df.index).where(z.notna(), 0)
    so = _align(onchain.get("sopr"), df.index)
    if so is not None and so.notna().sum() > 30:
        m7 = so.rolling(7, min_periods=5).mean()
        out["sopr"] = pd.Series(np.where(m7 < p["sopr_lo"], 1, np.where(m7 > p["sopr_hi"], -1, 0)), index=df.index).where(m7.notna(), 0)
    nv = _align(onchain.get("nvt"), df.index)
    if nv is not None and nv.notna().sum() > 200:
        pct = nv.rolling(365, min_periods=180).rank(pct=True)
        out["nvt"] = pd.Series(np.where(pct > p["nvt_pct"], -1, np.where(pct < 1 - p["nvt_pct"], 1, 0)), index=df.index).where(pct.notna(), 0)
    fg = _align(fear_greed, df.index)
    if fg is not None and fg.notna().any():
        out["fear_greed"] = pd.Series(np.where(fg < p["fg_lo"], 1, np.where(fg > p["fg_hi"], -1, 0)), index=df.index).where(fg.notna(), 0)
    return out


# ------------------------------------------------------------------ simulazione dei trade
def simulate(df: pd.DataFrame, score: pd.Series, p: dict, atr: pd.Series, start: int = 0, stop_at: int | None = None) -> list[dict]:
    """Un trade alla volta. Segnale a chiusura della barra i -> ingresso all'apertura di i+1;
    stop e target a multipli dell'ATR; se nella stessa barra toccano entrambi si assume lo stop (ipotesi prudente);
    uscita a tempo dopo `horizon` barre. Costi e slippage su entrata e uscita."""
    h = int(p["horizon"]); mv = int(p["min_votes"])
    o, hi, lo, c = (df[k].values for k in ("open", "high", "low", "close"))
    sc = score.values; a = atr.values
    n = len(c) if stop_at is None else min(stop_at, len(c))
    cost = 2 * (p["cost_bps"] + p["slippage_bps"]) / 1e4
    trades, i = [], start
    while i < n - h - 1:
        if abs(sc[i]) >= mv and np.isfinite(a[i]) and a[i] > 0:
            d = 1 if sc[i] > 0 else -1
            e = i + 1
            entry = o[e]
            stop = entry - d * p["atr_stop"] * a[i]
            tp = entry + d * p["atr_tp"] * a[i]
            exit_px, why, j = c[e + h - 1], "tempo", e + h - 1
            for j in range(e, e + h):
                hit_stop = lo[j] <= stop if d > 0 else hi[j] >= stop
                hit_tp = hi[j] >= tp if d > 0 else lo[j] <= tp
                if hit_stop:
                    exit_px, why = stop, "stop"; break
                if hit_tp:
                    exit_px, why = tp, "target"; break
            net = d * (exit_px / entry - 1) - cost
            trades.append({"date": str(df.index[i])[:10], "dir": d, "net": float(net), "exit": why, "bars": int(j - e + 1)})
            i = j + 1
        else:
            i += 1
    return trades


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    r = np.array([t["net"] for t in trades])
    w, l = r[r > 0], r[r <= 0]
    ex = pd.Series([t["exit"] for t in trades]).value_counts(normalize=True)
    return {"n": int(len(r)), "win_rate": float((r > 0).mean()), "mean_net_pct": float(r.mean() * 100),
            "profit_factor": float(w.sum() / -l.sum()) if len(l) and l.sum() < 0 else None,
            "target_rate": float(ex.get("target", 0)), "stop_rate": float(ex.get("stop", 0)), "time_rate": float(ex.get("tempo", 0))}


def reliability(oos: dict, train: dict, base_oos: dict | None) -> dict:
    """Affidabilità: guarda solo il periodo fuori campione. Spiega sempre il motivo."""
    n = oos.get("n", 0)
    if n < 15:
        return {"grade": "non valutabile", "score": None, "why": f"solo {n} operazioni fuori campione (minimo 15)"}
    wr, mean = oos["win_rate"], oos["mean_net_pct"]
    base_wr = (base_oos or {}).get("win_rate")
    if mean <= 0 or (base_wr is not None and wr <= base_wr):
        return {"grade": "bassa", "score": 25,
                "why": f"fuori campione: successo {wr:.0%}, rendimento netto medio {mean:+.2f}%"
                       + (f", non meglio dell'ingresso casuale ({base_wr:.0%})" if base_wr is not None else "")}
    if n >= 30 and wr >= 0.55 and train.get("mean_net_pct", 0) > 0:
        return {"grade": "alta", "score": 80, "why": f"fuori campione {n} operazioni, successo {wr:.0%}, netto {mean:+.2f}%, positivo anche in campione"}
    return {"grade": "media", "score": 55, "why": f"fuori campione {n} operazioni, successo {wr:.0%}, netto {mean:+.2f}%: campione limitato o non confermato in campione"}


# ------------------------------------------------------------------ costruzione del segnale
def build_signal(df: pd.DataFrame, onchain: dict[str, pd.Series] | None = None, fear_greed: pd.Series | None = None,
                 overrides: dict | None = None, tune_params: bool = True) -> dict:
    """df: barre giornaliere CHIUSE (open, high, low, close, volume), indice UTC."""
    onchain = onchain or {}
    base = dict(DEFAULTS, **{k: v for k, v in (overrides or {}).items() if k in DEFAULTS})
    if len(df) < 400:
        return {"direction": 0, "action": "DATI INSUFFICIENTI", "why": f"servono almeno 400 barre, ce ne sono {len(df)}"}
    fixed = set(overrides or {})
    params, tuning = (tune(df, base) if tune_params else (base, {}))
    for k in fixed:                       # i valori impostati dall'utente hanno la precedenza sulla ricerca automatica
        if k in base:
            params[k] = base[k]

    votes = {name: VOTERS[name](df, params) for name in VOTERS}
    votes.update(onchain_votes(df, onchain, fear_greed, params))
    families = {"ema": "tecnica", "rsi": "tecnica", "macd": "tecnica", "bb": "tecnica",
                "mvrv": "on-chain", "exchange_flow": "on-chain", "sopr": "on-chain", "nvt": "on-chain", "fear_greed": "sentiment"}
    score = sum(votes.values())
    atr = ind.atr(df)
    n = len(df); split = int(n * TRAIN_FRAC)
    trades = simulate(df, score, params, atr)
    tr_in = [t for t in trades if t["date"] < str(df.index[split])[:10]]
    tr_out = [t for t in trades if t["date"] >= str(df.index[split])[:10]]
    s_in, s_out, s_all = summarize(tr_in), summarize(tr_out), summarize(trades)
    # confronto: stesso orizzonte, stessa direzione prevalente, ingresso a ogni barra (non solo sui segnali)
    side = 1 if (score > 0).sum() >= (score < 0).sum() else -1
    base_trades = simulate(df, pd.Series(side * params["min_votes"], index=df.index), params, atr)
    b_out = summarize([t for t in base_trades if t["date"] >= str(df.index[split])[:10] and t["dir"] == side])
    rel = reliability(s_out, s_in, b_out)

    last = float(df["close"].iloc[-1]); a = float(atr.iloc[-1]); sc = float(score.iloc[-1])
    direction = int(np.sign(sc)) if abs(sc) >= params["min_votes"] else 0
    reasons = []
    for k, v in votes.items():
        x = int(v.iloc[-1])
        reasons.append({"indicator": k, "family": families[k], "vote": x})
    out = {
        "asof": str(df.index[-1])[:10], "price": last, "score": sc, "min_votes": int(params["min_votes"]),
        "direction": direction, "votes": reasons,
        "params": {k: params[k] for k in DEFAULTS}, "tuning": tuning,
        "history": {"all": s_all, "in_sample": s_in, "out_of_sample": s_out, "baseline_out_of_sample": b_out,
                    "split_date": str(df.index[split])[:10],
                    "recent_trades": trades[-15:]},
        "reliability": rel,
        "onchain_used": sorted(k for k in votes if families[k] != "tecnica"),
        "onchain_missing": [x for x in ("mvrv", "exchange_flow", "sopr", "nvt", "fear_greed") if x not in votes],
        "note": "Percentuali storiche non garantiscono risultati futuri. Si valuta solo la parte fuori campione.",
    }
    if direction:
        d = direction
        out.update(action="ACQUISTA (long)" if d > 0 else "VENDI / SHORT", side="long" if d > 0 else "short",
                   entry=last, stop_loss=last - d * params["atr_stop"] * a, take_profit=last + d * params["atr_tp"] * a,
                   horizon_days=int(params["horizon"]), risk_reward=params["atr_tp"] / params["atr_stop"],
                   atr=a, atr_pct=a / last * 100)
        accumulation = d > 0 and "mvrv" in votes and int(votes["mvrv"].iloc[-1]) == 1
        if accumulation:
            out["positioning"] = "Zona di accumulo: MVRV sotto la soglia storica di fondo."
        elif d < 0 and "mvrv" in votes and int(votes["mvrv"].iloc[-1]) == -1:
            out["positioning"] = "Zona di distribuzione: MVRV sopra la soglia storica di picco."
    else:
        out.update(action="ATTENDI", side=None)
    return out
