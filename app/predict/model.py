"""Motore predittivo.

Per ogni strumento e orizzonte h si classifica il rendimento futuro in tre classi:
  ribasso  : r(t, t+h) < -k * sigma_t * sqrt(h)
  laterale : |r| <= k * sigma_t * sqrt(h)
  rialzo   : r >  k * sigma_t * sqrt(h)
con sigma_t = volatilità EWMA nota al tempo t (nessuna informazione futura).

Modelli confrontati:
  1. base_rate   - frequenze storiche delle tre classi (il riferimento da battere)
  2. logit       - regressione logistica multinomiale regolarizzata sulle feature causali
  3. analog      - frequenze condizionate a regime di momentum e di volatilità (tabella empirica)

Validazione walk-forward: finestra di stima crescente, riaddestramento ogni `step` barre,
con 'embargo' di h barre tra fine stima e previsione, così le etichette di stima non si
sovrappongono al periodo previsto (de Prado 2018). Le metriche sono calcolate SOLO sulle
previsioni fuori campione.

Una probabilità viene mostrata come tale solo se il modello ha Brier Skill Score > 0 rispetto
al base rate su almeno MIN_OOS previsioni fuori campione; altrimenti si mostra il base rate
con l'avviso che il modello non ha vantaggio dimostrato.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore", category=UserWarning)

HORIZONS = {"1-3g": 3, "1 settimana": 5, "1 mese": 21, "3 mesi": 63, "6-12 mesi": 189}
INTRADAY_HORIZONS = {"intraday (4h)": 4, "intraday (24h)": 24}
FEATURES = ["mom1", "mom5", "mom21", "mom63", "mom252", "rsi14", "macd_hist_n", "adx", "di_diff", "bb_pos",
            "bb_width_pct", "z20", "dist_sma200", "vol_ratio", "dist_hi55_atr", "dist_lo55_atr", "vol_z", "cmf20"]
CLASSES = ["down", "flat", "up"]
K_THRESHOLD = 0.5
MIN_TRAIN = 500
MIN_OOS = 150


def make_labels(close: pd.Series, vol: pd.Series, h: int, k=K_THRESHOLD, level=False):
    fwd = (close.shift(-h) - close) if level else np.log(close.shift(-h) / close)
    thr = k * vol * np.sqrt(h)
    y = pd.Series(np.where(fwd > thr, 2, np.where(fwd < -thr, 0, 1)), index=close.index, dtype=float)
    y[fwd.isna() | thr.isna()] = np.nan
    return y, fwd, thr


def brier(P: np.ndarray, y: np.ndarray) -> float:
    Y = np.eye(3)[y.astype(int)]
    return float(((P - Y) ** 2).sum(1).mean())


def logloss(P, y):
    return float(-np.log(np.clip(P[np.arange(len(y)), y.astype(int)], 1e-6, 1)).mean())


def reliability(P: np.ndarray, y: np.ndarray, cls=2, bins=8):
    """Curva di calibrazione per una classe: probabilità media prevista vs frequenza osservata."""
    p = P[:, cls]; o = (y == cls).astype(float)
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    out = []
    for i in range(bins):
        m = (p >= edges[i]) & (p <= edges[i + 1]) if i == bins - 1 else (p >= edges[i]) & (p < edges[i + 1])
        if m.sum() >= 10:
            out.append({"pred": float(p[m].mean()), "obs": float(o[m].mean()), "n": int(m.sum())})
    return out


class BaseRate:
    name = "base_rate"
    def fit(self, X, y): self.p = np.bincount(y.astype(int), minlength=3) / len(y); return self
    def predict_proba(self, X): return np.tile(self.p, (len(X), 1))


class Logit:
    name = "logit"
    def fit(self, X, y):
        self.m = make_pipeline(StandardScaler(), LogisticRegression(C=0.05, max_iter=500))
        self.m.fit(X, y.astype(int))
        self.cls = self.m.classes_
        return self
    def predict_proba(self, X):
        P = np.zeros((len(X), 3)); P[:, self.cls.astype(int)] = self.m.predict_proba(X); return P


class Analog:
    """Tabella empirica: frequenze delle classi condizionate a (terzile momentum 63g, terzile vol_ratio),
    con smoothing verso il base rate (prior di Dirichlet, peso 30 osservazioni)."""
    name = "analog"
    def _cells(self, X):
        return np.digitize(X[:, 0], self.q_m) * 3 + np.digitize(X[:, 1], self.q_v)
    def fit(self, X, y):
        self.q_m = np.quantile(X[:, 0], [1 / 3, 2 / 3]); self.q_v = np.quantile(X[:, 1], [1 / 3, 2 / 3])
        base = np.bincount(y.astype(int), minlength=3) / len(y)
        c = self._cells(X)
        self.tab = {}
        for cell in range(9):
            yy = y[c == cell].astype(int)
            counts = np.bincount(yy, minlength=3)
            self.tab[cell] = (counts + 30 * base) / (counts.sum() + 30)
        return self
    def predict_proba(self, X):
        return np.array([self.tab[c] for c in self._cells(X)])


MODEL_CLASSES = {"base_rate": BaseRate, "logit": Logit, "analog": Analog}


def _xy(feat: pd.DataFrame, model: str):
    if model == "analog":
        return feat[["mom63", "vol_ratio"]]
    cols = [c for c in FEATURES if c in feat and feat[c].notna().mean() > 0.5]
    return feat[cols]


def walk_forward(feat: pd.DataFrame, close: pd.Series, h: int, level=False, step=63, models=("logit", "analog")):
    """Restituisce metriche fuori campione e la previsione corrente per ciascun modello."""
    y, fwd, thr = make_labels(close, feat["vol"], h, level=level)
    out = {"horizon_bars": h, "models": {}, "n_obs": int(y.notna().sum())}
    for m in ("base_rate",) + tuple(models):
        Xall = _xy(feat, m)
        valid_x = Xall.notna().all(axis=1)
        idx = Xall.index[valid_x]
        if len(idx) < MIN_TRAIN + 2 * h + 50:
            out["models"][m] = {"status": "storia insufficiente", "n_oos": 0}
            continue
        Xv = Xall.loc[idx].values
        yv = y.loc[idx].values
        oos_P, oos_y, oos_t = [], [], []
        start = MIN_TRAIN + h
        last_lab = len(idx) - 1 - h  # ultima posizione con etichetta nota (al momento più recente)
        for s in range(start, last_lab + 1, step):
            train_end = s - h            # embargo: le etichette di stima terminano prima di s
            tr = np.arange(0, train_end)
            tr = tr[np.isfinite(yv[tr])]
            te = np.arange(s, min(s + step, last_lab + 1))
            te = te[np.isfinite(yv[te])]
            if len(tr) < MIN_TRAIN or len(te) == 0:
                continue
            mdl = MODEL_CLASSES[m]().fit(Xv[tr], yv[tr])
            oos_P.append(mdl.predict_proba(Xv[te])); oos_y.append(yv[te]); oos_t.extend(idx[te])
        res = {"status": "ok"}
        if oos_P:
            P = np.vstack(oos_P); yy = np.concatenate(oos_y)
            res.update(n_oos=int(len(yy)), brier=brier(P, yy), logloss=logloss(P, yy),
                       reliability_up=reliability(P, yy, 2), reliability_down=reliability(P, yy, 0),
                       hit_rate=float((P.argmax(1) == yy).mean()), oos_start=str(oos_t[0])[:10])
            res["_P"], res["_y"] = P, yy
        else:
            res["n_oos"] = 0
        # modello finale: tutte le etichette note fino ad ora (fine stima = ultima etichetta nota)
        tr = np.arange(0, last_lab + 1); tr = tr[np.isfinite(yv[tr])]
        if len(tr) >= MIN_TRAIN:
            mdl = MODEL_CLASSES[m]().fit(Xv[tr], yv[tr])
            res["current"] = mdl.predict_proba(Xv[-1:])[0].tolist()
            res["asof"] = str(idx[-1])
        out["models"][m] = res
    # skill rispetto al base rate sugli stessi periodi fuori campione
    br = out["models"].get("base_rate", {})
    for m in models:
        r = out["models"].get(m, {})
        if r.get("n_oos", 0) and br.get("n_oos", 0):
            n = min(r["n_oos"], br["n_oos"])
            b_m = brier(r["_P"][-n:], r["_y"][-n:]); b_b = brier(br["_P"][-n:], br["_y"][-n:])
            r["brier_skill"] = float(1 - b_m / b_b) if b_b > 0 else float("nan")
            # incertezza della skill: bootstrap a blocchi (blocco = h) delle differenze di Brier
            Y = np.eye(3)[r["_y"][-n:].astype(int)]
            d = ((br["_P"][-n:] - Y) ** 2).sum(1) - ((r["_P"][-n:] - Y) ** 2).sum(1)
            rng = np.random.default_rng(0); blk = max(1, h); nb = max(1, n // blk)
            boots = [d[np.concatenate([np.arange(s, min(s + blk, n)) for s in rng.integers(0, max(1, n - blk), nb)])].mean()
                     for _ in range(300)]
            r["skill_ci_low"] = float(np.quantile(boots, 0.05) / (b_b if b_b else 1))
    for r in out["models"].values():
        r.pop("_P", None); r.pop("_y", None)
    # scelta: il modello con skill positiva più alta e intervallo inferiore > 0
    cands = [(m, r) for m, r in out["models"].items() if m != "base_rate" and r.get("n_oos", 0) >= MIN_OOS
             and r.get("brier_skill", -1) > 0 and r.get("skill_ci_low", -1) > 0 and "current" in r]
    if cands:
        m, r = max(cands, key=lambda x: x[1]["brier_skill"])
        out["selected"] = m; out["shown_as_probability"] = True
        out["probabilities"] = dict(zip(CLASSES, r["current"]))
        # combinazione: media dei modelli con skill positiva (pesata per skill), mostrata a parte
        pos = [(r2["brier_skill"], np.array(r2["current"])) for m2, r2 in cands]
        w = np.array([s for s, _ in pos]); w = w / w.sum()
        out["ensemble"] = dict(zip(CLASSES, (sum(wi * p for wi, (_, p) in zip(w, pos))).tolist()))
    else:
        out["selected"] = "base_rate"; out["shown_as_probability"] = False
        cur = out["models"].get("base_rate", {}).get("current")
        out["probabilities"] = dict(zip(CLASSES, cur)) if cur else None
    out["threshold_now"] = float(thr.iloc[-1]) if np.isfinite(thr.iloc[-1]) else float(K_THRESHOLD * feat["vol"].iloc[-1] * np.sqrt(h))
    return out
