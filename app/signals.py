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
from .levels import LevelFinder, plan_exit, trailing_stop

TRAIN_FRAC = 0.6
HOLDOUT_DAYS = 70     # ultime 10 settimane: riservate, da guardare una sola volta a fine sviluppo
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
    "horizon": 60, "atr_stop": 2.0, "atr_tp": 3.0, "min_votes": 2,
    "trail": 1,                               # 1 = niente target: lo stop segue il prezzo sugli ultimi minimi/massimi confermati
    "exit_sr": 1,                             # 1 = stop/target da supporti e resistenze, 0 = multipli fissi di ATR
    "min_rr": 1.5, "stop_buf": 0.3, "tp_buf": 0.15, "min_strength": 1.6,
    "w_trend": 1.0, "w_mr": 0.0, "w_chain": 1.0,   # pesi dei gruppi di voti: trend (EMA, MACD), ritorno alla media (RSI, Bollinger), on-chain/sentiment
    "regime": 1, "near_level": 0, "near_atr": 1.0,  # filtri d'ingresso: regime = solo nel verso dell'EMA200
    "cost_bps": 10.0, "slippage_bps": 10.0, "stop_slip_bps": 10.0,   # per lato; slippage extra sugli stop
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
class Trader:
    """Simula UN trade per barra di segnale con uscite da supporti/resistenze (o ATR fisso se exit_sr=0).

    Regole realistiche e prudenti:
    - segnale alla chiusura della barra i; ingresso all'APERTURA di i+1; piano (stop/target) deciso con dati fino a i;
    - se l'ingresso è già oltre stop o target il trade è scartato;
    - gap: se l'apertura di una barra supera lo stop, l'uscita avviene all'apertura (perdita peggiore di -1R);
    - se nella stessa barra sono toccati stop e target si assume lo stop;
    - costi su entrata e uscita (commissione + slippage) e slippage extra sugli stop;
    - uscita a tempo dopo `horizon` barre. Non si modella il funding dei perpetual (dichiarato)."""

    def __init__(self, df: pd.DataFrame, atr: pd.Series, p: dict):
        self.df, self.p = df, p
        self.o, self.h, self.l, self.c = (df[k].values for k in ("open", "high", "low", "close"))
        self.atr = atr.values
        self.n = len(df)
        self.finder = LevelFinder(df, atr, p) if p.get("exit_sr", 1) else None
        self._out: dict = {}

    def outcome(self, i: int, side: int) -> dict | None:
        key = (i, side)
        if key not in self._out:
            self._out[key] = self._compute(i, side)
        return self._out[key]

    def _compute(self, i: int, side: int):
        p = self.p; h = int(p["horizon"]); e = i + 1
        if e + h >= self.n:
            return None
        a, ref = self.atr[i], self.c[i]
        if not (np.isfinite(a) and a > 0):
            return None
        if p.get("exit_sr", 1):
            pl = plan_exit(side, ref, a, self.finder.important(i), p, need_tp=not p.get("trail", 0))
            if not pl["ok"]:
                return {"skip": pl["why"]}
            stop, tp, bs, bt = pl["stop"], pl["tp"], pl["basis_stop"], pl["basis_tp"]
        else:
            stop, tp, bs, bt = ref - side * p["atr_stop"] * a, ref + side * p["atr_tp"] * a, "ATR", "ATR"
        entry = self.o[e]
        if side * (entry - stop) <= 0 or (tp is not None and side * (tp - entry) <= 0):
            return {"skip": "ingresso oltre stop o target"}
        stop0 = stop
        exit_px, why, j = self.c[e + h - 1], "tempo", e + h - 1
        for j in range(e, e + h):
            if j > e and (self.o[j] <= stop if side > 0 else self.o[j] >= stop):
                exit_px, why = self.o[j], "stop"; break                       # gap oltre lo stop
            hit_stop = self.l[j] <= stop if side > 0 else self.h[j] >= stop
            hit_tp = tp is not None and (self.h[j] >= tp if side > 0 else self.l[j] <= tp)
            if hit_stop:
                exit_px, why = stop, "stop"; break
            if hit_tp:
                exit_px, why = tp, "target"; break
            if p.get("trail", 0) and self.finder is not None:
                stop = trailing_stop(self.finder, side, e, j, stop, self.atr[j], p["stop_buf"])
        cost = 2 * (p["cost_bps"] + p["slippage_bps"]) / 1e4 + (p["stop_slip_bps"] / 1e4 if why == "stop" else 0.0)
        gross = side * (exit_px / entry - 1)
        risk = abs(entry - stop0) / entry
        return {"i": i, "e": e, "bars": j - e + 1, "side": side, "gross": float(gross), "cost": float(cost),
                "net": float(gross - cost), "r": float((gross - cost) / risk), "exit": why,
                "rr": None if tp is None else float(abs(tp - entry) / abs(entry - stop0)), "risk_pct": float(risk * 100),
                "basis_stop": bs, "basis_tp": bt, "date": str(self.df.index[i])[:10]}

    def allowed(self, i: int, side: int) -> bool:
        """Filtri d'ingresso opzionali: regime (EMA200) e vicinanza a un livello importante."""
        p = self.p
        if p.get("regime", 0):
            e200 = self._ema200[i]
            if not np.isfinite(e200) or side * (self.c[i] - e200) <= 0:
                return False
        if p.get("near_level", 0) and self.finder is not None:
            a = self.atr[i]; c = self.c[i]; ok = False
            for z in self.finder.important(i):
                d = (c - z.high) / a if side > 0 else (z.low - c) / a        # distanza dal livello, a favore del trade
                if -0.3 <= d <= p["near_atr"]:
                    ok = True; break
            if not ok:
                return False
        return True

    @property
    def _ema200(self):
        if not hasattr(self, "_e200"):
            self._e200 = ind.ema(self.df["close"], 200).values
        return self._e200


def simulate(trader: Trader, score: pd.Series, min_votes: float, start: int = 0) -> tuple[list[dict], dict]:
    """Un trade alla volta per moneta. Restituisce (trade, motivi di scarto)."""
    sc = score.values; n = trader.n; h = int(trader.p["horizon"])
    trades, skipped, i = [], {}, start
    while i < n - h - 2:
        if abs(sc[i]) >= min_votes:
            d = 1 if sc[i] > 0 else -1
            if trader.allowed(i, d):
                o = trader.outcome(i, d)
                if o and "skip" not in o:
                    trades.append(o)
                    i = o["e"] + o["bars"]
                    continue
                if o:
                    skipped[o["skip"]] = skipped.get(o["skip"], 0) + 1
        i += 1
    return trades, skipped


def stats(trades: list[dict]) -> dict:
    """Metriche descrittive di un insieme di operazioni (guadagno medio, non solo % di vincite)."""
    if not trades:
        return {"n": 0}
    net = np.array([t["net"] for t in trades]); r = np.array([t["r"] for t in trades])
    w, l = net[net > 0], net[net <= 0]
    ex = pd.Series([t["exit"] for t in trades]).value_counts(normalize=True)
    rng = np.random.default_rng(0)
    boots = rng.choice(net, (1000, len(net))).mean(1) if len(net) > 1 else net
    return {"n": int(len(net)), "win_rate": float((net > 0).mean()),
            "avg_win_pct": float(w.mean() * 100) if len(w) else None, "avg_loss_pct": float(l.mean() * 100) if len(l) else None,
            "payoff": float(w.mean() / -l.mean()) if len(w) and len(l) and l.mean() < 0 else None,
            "expectancy_pct": float(net.mean() * 100), "expectancy_r": float(r.mean()),
            "ci90_pct": [float(np.quantile(boots, .05) * 100), float(np.quantile(boots, .95) * 100)],
            "profit_factor": float(w.sum() / -l.sum()) if len(l) and l.sum() < 0 else None,
            "worst_r": float(r.min()), "median_r": float(np.median(r)),
            "target_rate": float(ex.get("target", 0)), "stop_rate": float(ex.get("stop", 0)), "time_rate": float(ex.get("tempo", 0))}


def by_side(trades: list[dict]) -> dict:
    return {"all": stats(trades), "long": stats([t for t in trades if t["side"] > 0]), "short": stats([t for t in trades if t["side"] < 0])}


def random_timing(trader: Trader, lo: int, hi: int) -> dict:
    """'Caso' descrittivo: aspettativa di entrare a OGNI barra del periodo [lo, hi) con le STESSE regole di uscita."""
    out = {}
    for name, side in (("long", 1), ("short", -1)):
        res = [trader.outcome(i, side) for i in range(lo, hi)]
        res = [x for x in res if x and "skip" not in x]
        out[name] = {"n": len(res), "expectancy_pct": float(np.mean([x["net"] for x in res]) * 100) if res else None,
                     "expectancy_r": float(np.mean([x["r"] for x in res])) if res else None,
                     "win_rate": float(np.mean([x["net"] > 0 for x in res])) if res else None}
    return out


# ------------------------------------------------------------------ costruzione del segnale
GROUPS = {"ema": "trend", "macd": "trend", "rsi": "mr", "bb": "mr",
          "mvrv": "chain", "exchange_flow": "chain", "sopr": "chain", "nvt": "chain", "fear_greed": "chain"}
FAMILIES = {"ema": "tecnica", "rsi": "tecnica", "macd": "tecnica", "bb": "tecnica",
            "mvrv": "on-chain", "exchange_flow": "on-chain", "sopr": "on-chain", "nvt": "on-chain", "fear_greed": "sentiment"}


def compute_votes(df, onchain, fear_greed, params):
    votes = {name: VOTERS[name](df, params) for name in VOTERS}
    votes.update(onchain_votes(df, onchain, fear_greed, params))
    w = {"trend": params["w_trend"], "mr": params["w_mr"], "chain": params["w_chain"]}
    score = sum(v * w[GROUPS[k]] for k, v in votes.items())
    return votes, score


def build_signal(df: pd.DataFrame, onchain: dict[str, pd.Series] | None = None, fear_greed: pd.Series | None = None,
                 overrides: dict | None = None, tune_params: bool = True, holdout_days: int = HOLDOUT_DAYS) -> dict:
    """df: barre giornaliere CHIUSE (open, high, low, close, volume), indice UTC.
    Le ultime `holdout_days` barre sono un periodo RISERVATO: non servono a scegliere i parametri e le loro statistiche
    sono calcolate a parte (`history.holdout`), da guardare una sola volta a fine sviluppo."""
    onchain = onchain or {}
    base = dict(DEFAULTS, **{k: v for k, v in (overrides or {}).items() if k in DEFAULTS})
    if len(df) < 400:
        return {"direction": 0, "action": "DATI INSUFFICIENTI", "why": f"servono almeno 400 barre, ce ne sono {len(df)}"}
    n = len(df)
    n_pre = n - holdout_days
    params, tuning = (tune(df.iloc[:n_pre], base) if tune_params else (base, {}))
    for k in (overrides or {}):               # i valori impostati dall'utente hanno la precedenza sulla ricerca automatica
        if k in base:
            params[k] = base[k]

    votes, score = compute_votes(df, onchain, fear_greed, params)
    atr = ind.atr(df)
    trader = Trader(df, atr, params)
    trades, skipped = simulate(trader, score, params["min_votes"])
    split = int(n_pre * TRAIN_FRAC)
    d_split, d_hold = str(df.index[split])[:10], str(df.index[n_pre])[:10]
    def _ends_before(t, limit):               # scarto di sicurezza: un trade che finisce nel periodo riservato non entra nel fuori campione
        return t["e"] + t["bars"] - 1 < limit
    seg = {"in_sample": [t for t in trades if t["date"] < d_split],
           "out_of_sample": [t for t in trades if d_split <= t["date"] < d_hold and _ends_before(t, n_pre)],
           "holdout": [t for t in trades if t["date"] >= d_hold]}
    hist = {k: by_side(v) for k, v in seg.items()}
    hist.update(split_date=d_split, holdout_start=d_hold, skipped=skipped,
                random_out_of_sample=random_timing(trader, split, n_pre - int(params["horizon"])),
                recent_trades=[{k: t[k] for k in ("date", "side", "net", "r", "exit", "bars")} for t in trades[-60:]])

    last = float(df["close"].iloc[-1]); a = float(atr.iloc[-1]); sc = float(score.iloc[-1]); t_last = n - 1
    direction = int(np.sign(sc)) if abs(sc) >= params["min_votes"] else 0
    zones = trader.finder.important(t_last) if trader.finder else []
    near = sorted(zones, key=lambda z: abs(z.price - last))[:8]
    out = {
        "asof": str(df.index[-1])[:10], "price": last, "score": sc, "min_votes": float(params["min_votes"]),
        "direction": direction,
        "votes": [{"indicator": k, "family": FAMILIES[k], "vote": int(v.iloc[-1])} for k, v in votes.items()],
        "params": {k: params[k] for k in DEFAULTS}, "tuning": {k: {"chosen": v["chosen"], "note": v["note"]} for k, v in tuning.items()},
        "history": hist, "levels": [z.as_dict() for z in sorted(near, key=lambda z: z.price)],
        "onchain_used": sorted(k for k in votes if FAMILIES[k] != "tecnica"),
        "onchain_missing": [x for x in ("mvrv", "exchange_flow", "sopr", "nvt", "fear_greed") if x not in votes],
        "note": "Statistiche descrittive su una singola moneta. L'affidabilità si valuta sul pool di tutte le monete.",
    }
    plan = None
    if direction and trader.allowed(t_last, direction):
        if trader.finder:
            plan = plan_exit(direction, last, a, trader.finder.important(t_last), params, need_tp=not params["trail"])
        else:
            plan = {"ok": True, "stop": last - direction * params["atr_stop"] * a, "tp": last + direction * params["atr_tp"] * a,
                    "basis_stop": "ATR", "basis_tp": "ATR", "rr": params["atr_tp"] / max(params["atr_stop"], 1e-9),
                    "risk_pct": params["atr_stop"] * a / last * 100}
    if direction and plan and plan["ok"]:
        d = direction
        tp, basis_tp, rr, tp_ind = plan["tp"], plan["basis_tp"], plan["rr"], False
        if params["trail"] and trader.finder:       # niente target nella strategia: si indica il prossimo livello importante
            ind_plan = plan_exit(d, last, a, trader.finder.important(t_last), dict(params, min_rr=1.0), need_tp=True)
            tp, basis_tp, rr, tp_ind = (ind_plan.get("tp"), ind_plan.get("basis_tp"), ind_plan.get("rr"), True) if ind_plan["ok"] else (None, "nessun livello importante a favore", None, True)
        out.update(action="ACQUISTA (long)" if d > 0 else "VENDI / SHORT", side="long" if d > 0 else "short",
                   entry=last, stop_loss=plan["stop"], take_profit=tp, basis_stop=plan["basis_stop"],
                   basis_tp=basis_tp, tp_is_indicative=tp_ind, horizon_days=int(params["horizon"]), risk_reward=rr,
                   risk_pct=plan["risk_pct"], atr=a, atr_pct=a / last * 100,
                   exit_rule=("Stop mobile: dopo l'ingresso lo stop si sposta " + ("sotto l'ultimo minimo" if d > 0 else "sopra l'ultimo massimo")
                              + f" confermato, mai in direzione sfavorevole; uscita a tempo dopo {int(params['horizon'])} giorni. "
                              "Il take profit è solo un livello indicativo (presa parziale)." if params["trail"] else
                              f"Uscita su stop, target o dopo {int(params['horizon'])} giorni."))
        if d > 0 and "mvrv" in votes and int(votes["mvrv"].iloc[-1]) == 1:
            out["positioning"] = "Zona di accumulo: MVRV sotto la soglia storica di fondo."
        elif d < 0 and "mvrv" in votes and int(votes["mvrv"].iloc[-1]) == -1:
            out["positioning"] = "Zona di distribuzione: MVRV sopra la soglia storica di picco."
    else:
        out.update(action="ATTENDI", side=None)
        if direction:
            out["skipped_reason"] = (plan or {}).get("why") or "filtri d'ingresso non soddisfatti (regime o distanza dai livelli)"
    out["_trades"] = [[t["date"], t["side"], round(t["gross"], 5), round(t["cost"], 5), round(t["r"], 4), round(t["risk_pct"], 3)]
                      for t in seg["out_of_sample"]]
    # esiti per barra (serve al test del caso sul pool di monete): solo periodo fuori campione e riservato
    lo, hi = split, n_pre - int(params["horizon"])        # solo ingressi il cui trade si chiude prima del periodo riservato
    out["_outcomes"] = {"dates": [str(x)[:10] for x in df.index[lo:hi]],
                        "long": [_o(trader.outcome(i, 1)) for i in range(lo, hi)],
                        "short": [_o(trader.outcome(i, -1)) for i in range(lo, hi)]}
    return out


def _o(x):
    return None if (not x or "skip" in x) else [round(x["net"], 5), round(x["r"], 4)]
