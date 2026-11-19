"""Modelli statistici e strumenti dei sistemi complessi.

Ogni funzione documenta il proprio livello di validazione:
  - "validato": metodo con letteratura statistica consolidata e stima ben definita
  - "sperimentale": metodo valido in sé, ma con evidenza debole o mista come segnale di mercato
  - "analogia": interpretazione fisica usata come metafora, non come misura
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import optimize, stats

# ------------------------------------------------------------------ GARCH(1,1)
def garch11_fit(ret: np.ndarray):
    """GARCH(1,1) gaussiano stimato per massima verosimiglianza. Validato (Bollerslev 1986)."""
    r = np.asarray(ret, float)
    r = r[np.isfinite(r)]
    if len(r) < 250:
        return None
    r = r[-2500:] - r[-2500:].mean()
    var0 = r.var()

    from scipy.signal import lfilter

    def hser(w, a, b):
        # h[0]=var0; h[t] = w + a r[t-1]^2 + b h[t-1]  (filtro IIR, vettoriale)
        u = np.concatenate([[var0], w + a * r[:-1] ** 2])
        return lfilter([1.0], [1.0, -b], u)

    def nll(p):
        w, a, b = p
        if w <= 0 or a < 0 or b < 0 or a + b >= 0.999:
            return 1e10
        h = hser(w, a, b)
        return 0.5 * np.sum(np.log(h) + r ** 2 / h)

    best = None
    for a0, b0 in ((0.05, 0.9), (0.1, 0.85), (0.03, 0.95)):
        x0 = [var0 * (1 - a0 - b0), a0, b0]
        res = optimize.minimize(nll, x0, method="Nelder-Mead", options={"maxiter": 2000, "xatol": 1e-10, "fatol": 1e-6})
        if best is None or res.fun < best.fun:
            best = res
    w, a, b = best.x
    h = hser(w, a, b)
    h_next = w + a * r[-1] ** 2 + b * h[-1]
    return {"omega": w, "alpha": a, "beta": b, "persistence": a + b, "h_next": h_next,
            "long_run_var": w / (1 - a - b), "cond_vol_series": np.sqrt(h)}


def garch_forecast_vol(fit, horizon: int) -> float:
    """Volatilità cumulata prevista su 'horizon' periodi (deviazione standard del rendimento)."""
    if fit is None:
        return float("nan")
    p, lr, h = fit["persistence"], fit["long_run_var"], fit["h_next"]
    total = sum(lr + p ** k * (h - lr) for k in range(horizon))
    return float(np.sqrt(total))


# ------------------------------------------------------------------ Hurst (DFA)
def dfa_hurst(ret: np.ndarray, min_n=10, max_frac=0.25):
    """Detrended Fluctuation Analysis (Peng et al. 1994). Validato come stimatore;
    H≈0.5 assenza di memoria, >0.5 persistenza, <0.5 anti-persistenza. Errore tipico ±0.05
    su 1000 osservazioni: differenze piccole da 0.5 non sono significative."""
    x = np.asarray(ret, float)
    x = x[np.isfinite(x)]
    if len(x) < 200:
        return float("nan")
    y = np.cumsum(x - x.mean())
    N = len(y)
    scales = np.unique(np.floor(np.logspace(np.log10(min_n), np.log10(N * max_frac), 15)).astype(int))
    F = []
    for s in scales:
        k = N // s
        segs = y[:k * s].reshape(k, s)
        t = np.arange(s)
        A = np.vstack([t, np.ones(s)]).T
        coef, *_ = np.linalg.lstsq(A, segs.T, rcond=None)
        resid = segs.T - A @ coef
        F.append(np.sqrt((resid ** 2).mean()))
    F = np.array(F)
    ok = F > 0
    slope = np.polyfit(np.log(scales[ok]), np.log(F[ok]), 1)[0]
    return float(slope)


# ------------------------------------------------------------------ entropia
def permutation_entropy(x: np.ndarray, order=4, delay=1) -> float:
    """Entropia di permutazione normalizzata (Bandt & Pompe 2002), in [0,1].
    1 = sequenza indistinguibile dal rumore; valori bassi = struttura ordinale.
    Validato come misura; come segnale predittivo sui mercati: sperimentale."""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x) - (order - 1) * delay
    if n < 50:
        return float("nan")
    idx = np.arange(order) * delay
    patterns = np.array([np.argsort(x[i + idx], kind="stable") for i in range(n)])
    codes = (patterns * (order ** np.arange(order))).sum(1)
    _, counts = np.unique(codes, return_counts=True)
    p = counts / counts.sum()
    from math import factorial, log
    return float(-(p * np.log(p)).sum() / log(factorial(order)))


# ------------------------------------------------------------------ code a legge di potenza
def hill_tail_index(ret: np.ndarray, tail_frac=0.05):
    """Stimatore di Hill dell'esponente di coda (validato). Rendimenti finanziari tipici:
    alfa tra 2.5 e 4 ('legge cubica'). Alfa più basso = code più pesanti = eventi estremi più frequenti."""
    x = np.abs(np.asarray(ret, float))
    x = x[np.isfinite(x) & (x > 0)]
    if len(x) < 300:
        return float("nan"), 0
    x = np.sort(x)[::-1]
    k = max(20, int(len(x) * tail_frac))
    alpha = 1.0 / np.mean(np.log(x[:k] / x[k]))
    return float(alpha), k


# ------------------------------------------------------------------ regime e cambi di regime
def vol_regime(ret: pd.Series):
    """Regime di volatilità: percentile della volatilità a 20 giorni sulla storia degli ultimi 3 anni."""
    v = ret.rolling(20, min_periods=20).std()
    hist = v.dropna().tail(756)
    if len(hist) < 100:
        return None
    pct = float((hist < hist.iloc[-1]).mean() * 100)
    label = "bassa" if pct < 25 else "normale" if pct < 75 else "alta" if pct < 95 else "estrema"
    return {"percentile": pct, "label": label, "vol20_ann": float(hist.iloc[-1] * np.sqrt(252))}


def cusum_breaks(ret: pd.Series, h=5.0, lookback=500):
    """CUSUM sulla varianza standardizzata (Page 1954): segnala cambiamenti di regime recenti.
    Validato come test sequenziale; la soglia h controlla i falsi allarmi."""
    r = ret.dropna().tail(lookback)
    if len(r) < 120:
        return []
    base = r.iloc[:60].var()
    z = r ** 2 / base - 1
    s_pos = s_neg = 0.0
    breaks = []
    for t, v in z.items():
        s_pos = max(0.0, s_pos + v - 0.5)
        s_neg = max(0.0, s_neg - v - 0.5)
        if s_pos > h or s_neg > h:
            breaks.append({"date": t.isoformat(), "type": "aumento varianza" if s_pos > h else "calo varianza"})
            s_pos = s_neg = 0.0
            i = r.index.get_loc(t)
            base = r.iloc[max(0, i - 59):i + 1].var() or base
    return breaks[-5:]


def ou_half_life(series: pd.Series):
    """Tempo di dimezzamento di un processo Ornstein-Uhlenbeck stimato con regressione AR(1)
    sui livelli (validato). Significativo solo se il coefficiente è negativo e il test ADF lo conferma."""
    y = series.dropna().tail(500)
    if len(y) < 100:
        return None
    dy = y.diff().dropna()
    ylag = y.shift().dropna().loc[dy.index]
    b, a, r, p, se = stats.linregress(ylag.values, dy.values)
    hl = -np.log(2) / b if b < 0 else float("inf")
    try:
        from statsmodels.tsa.stattools import adfuller
        adf_p = float(adfuller(y.values, maxlag=1, autolag=None, result_object=False)[1])
    except Exception:
        adf_p = float("nan")
    return {"half_life": float(hl), "beta": float(b), "p_value": float(p), "adf_p": adf_p}


def regression_channel(close: pd.Series, n=120):
    """Regressione lineare su log-prezzo negli ultimi n giorni: pendenza annualizzata, R², posizione nel canale."""
    y = np.log(close.dropna().tail(n).where(lambda s: s > 0)).dropna()
    if len(y) < n // 2:
        return None
    x = np.arange(len(y))
    b, a, r, p, se = stats.linregress(x, y.values)
    resid = y.values - (a + b * x)
    sd = resid.std()
    return {"slope_ann_pct": float((np.exp(b * 252) - 1) * 100), "r2": float(r ** 2),
            "z_in_channel": float(resid[-1] / sd) if sd > 0 else 0.0, "p_value": float(p)}


def distribution_stats(ret: pd.Series):
    r = ret.dropna().tail(1000)
    if len(r) < 100:
        return None
    jb = stats.jarque_bera(r.values)
    return {"skew": float(stats.skew(r)), "excess_kurtosis": float(stats.kurtosis(r)),
            "jarque_bera_p": float(jb.pvalue), "n": int(len(r))}


def monte_carlo_bands(ret: pd.Series, last: float, horizon: int, sigma_h: float, n=4000, seed=7):
    """Bande di prezzo per bootstrap a blocchi dei rendimenti storici, riscalati alla volatilità
    GARCH prevista. Descrive la dispersione plausibile, NON una direzione."""
    r = ret.dropna().tail(1500).values
    if len(r) < 250 or not np.isfinite(sigma_h):
        return None
    rng = np.random.default_rng(seed)
    r = (r - r.mean()) / r.std()
    block = 5
    sims = np.zeros(n)
    nb = int(np.ceil(horizon / block))
    starts = rng.integers(0, len(r) - block, size=(n, nb))
    for j in range(nb):
        seg = np.stack([r[s:s + block] for s in starts[:, j]])
        sims += seg.sum(1)
    sims = sims[:] * (sigma_h / np.sqrt(nb * block))
    q = np.quantile(sims, [0.05, 0.25, 0.5, 0.75, 0.95])
    return {f"p{int(k*100)}": float(last * np.exp(v)) for k, v in zip([0.05, 0.25, 0.5, 0.75, 0.95], q)}


# ------------------------------------------------------------------ rete e rischio sistemico
def correlation_network(returns: pd.DataFrame, window=120):
    """Rete delle correlazioni sugli ultimi 'window' giorni comuni.
    - absorption ratio (Kritzman et al. 2011): quota di varianza spiegata dai primi 1/5 autovettori;
      valori alti = mercati più accoppiati, shock più trasmissibili. Validato in letteratura.
    - centralità ad autovettore per ogni asset: quanto è 'connesso' al resto."""
    R = returns.dropna(axis=1, thresh=int(window * 0.8)).tail(window).dropna()
    if R.shape[1] < 5 or len(R) < window * 0.6:
        return None
    C = np.corrcoef(R.values.T)
    vals, vecs = np.linalg.eigh(C)
    vals, vecs = vals[::-1], vecs[:, ::-1]
    k = max(1, R.shape[1] // 5)
    ar = float(vals[:k].sum() / vals.sum())
    cent = np.abs(vecs[:, 0]); cent = cent / cent.max()
    iu = np.triu_indices_from(C, 1)
    edges = [(R.columns[i], R.columns[j], float(C[i, j])) for i, j in zip(*iu) if abs(C[i, j]) >= 0.6]
    return {"absorption_ratio": ar, "avg_abs_corr": float(np.abs(C[iu]).mean()), "n_assets": int(R.shape[1]),
            "centrality": dict(zip(R.columns, cent.round(3).tolist())), "strong_edges": edges[:200],
            "corr": pd.DataFrame(C, index=R.columns, columns=R.columns)}


def absorption_ratio_history(returns: pd.DataFrame, window=120, step=5, points=150):
    R = returns.dropna(axis=1, thresh=int(len(returns.tail(window * 3)) * 0.8))
    out = {}
    idx = R.index
    for end in range(len(idx) - 1, max(window, len(idx) - points * step), -step):
        sub = R.iloc[end - window + 1:end + 1].dropna()
        if sub.shape[1] < 5 or len(sub) < window * 0.6:
            continue
        vals = np.linalg.eigvalsh(np.corrcoef(sub.values.T))[::-1]
        k = max(1, sub.shape[1] // 5)
        out[idx[end]] = vals[:k].sum() / vals.sum()
    return pd.Series(out).sort_index()


def lead_lag_spillover(returns: pd.DataFrame, target: str, lags=(1, 2, 3), window=500):
    """Diffusione degli shock: correlazione tra i rendimenti passati di altri asset e quello
    attuale del target (test di Granger semplificato). Validato come statistica; i nessi
    trovati vanno letti con cautela per il problema dei confronti multipli."""
    R = returns.tail(window)
    if target not in R:
        return []
    y = R[target]
    res = []
    for col in R.columns:
        if col == target:
            continue
        for L in lags:
            x = R[col].shift(L)
            d = pd.concat([x, y], axis=1).dropna()
            if len(d) < 200:
                continue
            r, p = stats.pearsonr(d.iloc[:, 0], d.iloc[:, 1])
            res.append({"source": col, "lag": L, "corr": float(r), "p": float(p), "n": len(d)})
    m = len(res) or 1
    sig = [x for x in res if x["p"] * m < 0.05]   # correzione di Bonferroni
    return sorted(sig, key=lambda x: -abs(x["corr"]))[:8]


def early_warning(feat: pd.DataFrame):
    """Segnali di allerta precoce dei sistemi vicini a transizione (Scheffer et al. 2009):
    aumento congiunto di varianza e autocorrelazione. Sperimentale sui mercati: l'evidenza
    empirica è mista e non va letto come previsione di crisi."""
    ac = feat["ac1_60"].dropna().tail(250)
    v = feat["vol20"].dropna().tail(250)
    if len(ac) < 120 or len(v) < 120:
        return None
    t = np.arange(len(ac))
    tau_ac = stats.kendalltau(t, ac.values)[0]
    tau_v = stats.kendalltau(np.arange(len(v)), v.values)[0]
    return {"kendall_tau_autocorr": float(tau_ac), "kendall_tau_variance": float(tau_v),
            "flag": bool(tau_ac > 0.3 and tau_v > 0.3)}
