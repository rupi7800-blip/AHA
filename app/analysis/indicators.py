"""Indicatori tecnici e statistici calcolati in modo causale: il valore al tempo t usa
solo dati fino a t. Questo è il requisito che rende validi backtest e walk-forward."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s, n): return s.rolling(n, min_periods=n).mean()
def ema(s, n): return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(close, f=12, s=26, sig=9):
    m = ema(close, f) - ema(close, s)
    signal = ema(m, sig)
    return m, signal, m - signal


def true_range(df):
    pc = df["close"].shift()
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df, n=14):
    return true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def adx(df, n=14):
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    pdi = 100 * pd.Series(plus_dm, df.index).ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr
    mdi = 100 * pd.Series(minus_dm, df.index).ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean(), pdi, mdi


def bollinger(close, n=20, k=2):
    m = sma(close, n)
    sd = close.rolling(n, min_periods=n).std()
    return m, m + k * sd, m - k * sd, (2 * k * sd) / m


def rolling_vwap(df, n=20):
    if df["volume"].isna().all():
        return pd.Series(np.nan, index=df.index)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    v = df["volume"]
    return (tp * v).rolling(n, min_periods=n).sum() / v.rolling(n, min_periods=n).sum()


def cmf(df, n=20):
    """Chaikin Money Flow: proxy di accumulazione/distribuzione da prezzo e volume."""
    if df["volume"].isna().all():
        return pd.Series(np.nan, index=df.index)
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng
    return (mfm * df["volume"]).rolling(n, min_periods=n).sum() / df["volume"].rolling(n, min_periods=n).sum()


def obv(df):
    if df["volume"].isna().all():
        return pd.Series(np.nan, index=df.index)
    return (np.sign(df["close"].diff()).fillna(0) * df["volume"].fillna(0)).cumsum()


def rolling_percentile(s, n=252):
    """Percentile del valore corrente nella sua finestra passata (0-100), causale."""
    return s.rolling(n, min_periods=max(20, n // 4)).apply(lambda w: (w[:-1] < w[-1]).mean() * 100 if len(w) > 1 else np.nan, raw=True)


def zscore(s, n=20):
    return (s - s.rolling(n, min_periods=n).mean()) / s.rolling(n, min_periods=n).std()


def ewma_vol(ret, lam=0.94):
    return np.sqrt((ret ** 2).ewm(alpha=1 - lam, adjust=False, min_periods=20).mean())


def swing_levels(df, lookback=250, window=5, n=3):
    """Supporti/resistenze: massimi e minimi locali (frattali di Williams) recenti, raggruppati."""
    d = df.tail(lookback)
    h, l = d["high"].values, d["low"].values
    highs, lows = [], []
    for i in range(window, len(d) - window):
        if h[i] == h[i - window:i + window + 1].max():
            highs.append(h[i])
        if l[i] == l[i - window:i + window + 1].min():
            lows.append(l[i])
    last = d["close"].iloc[-1]
    res = sorted([x for x in highs if x > last])[:n]
    sup = sorted([x for x in lows if x < last], reverse=True)[:n]
    return sup, res


def fibonacci(df, lookback=250):
    d = df.tail(lookback)
    hi, lo = d["high"].max(), d["low"].min()
    up = d["high"].idxmax() > d["low"].idxmin()
    levels = {}
    for f in (0.236, 0.382, 0.5, 0.618, 0.786):
        levels[f"{f:.3f}"] = hi - (hi - lo) * f if up else lo + (hi - lo) * f
    return {"high": hi, "low": lo, "trend_up": bool(up), "levels": levels}


def _ac1(w):
    a, b = w[:-1], w[1:]
    sa, sb = a.std(), b.std()
    if not np.isfinite(sa) or sa == 0 or sb == 0:
        return np.nan
    return float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb))


def feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Matrice di feature causali usata da scanner, backtest e motore predittivo."""
    c = df["close"]
    logp = np.log(c.where(c > 0))
    # per serie di rendimenti/spread (possono essere <=0) usiamo differenze semplici
    is_level = (c <= 0).any()
    ret = c.diff() if is_level else logp.diff()
    f = pd.DataFrame(index=df.index)
    f["close"] = c
    f["ret1"] = ret
    vol = ewma_vol(ret)
    f["vol"] = vol
    f["vol20"] = ret.rolling(20, min_periods=20).std()
    f["vol100"] = ret.rolling(100, min_periods=60).std()
    f["vol_ratio"] = f["vol20"] / f["vol100"]
    for n in (1, 5, 21, 63, 126, 252):
        r = c.diff(n) if is_level else logp.diff(n)
        f[f"mom{n}"] = r / (vol * np.sqrt(n))       # momentum aggiustato per volatilità
    f["rsi14"] = rsi(c)
    m, s, hist = macd(c)
    f["macd_hist_n"] = hist / (c.abs().rolling(20).mean() * vol)  # istogramma normalizzato
    a = atr(df)
    f["atr"] = a
    f["atr_pct"] = a / c.abs()
    adxv, pdi, mdi = adx(df)
    f["adx"] = adxv
    f["di_diff"] = pdi - mdi
    mid, ub, lb, bw = bollinger(c)
    f["bb_pos"] = (c - lb) / (ub - lb)
    f["bb_width"] = bw
    f["bb_width_pct"] = rolling_percentile(bw.abs(), 252)
    f["z20"] = zscore(c, 20)
    f["z_sma200"] = (c - sma(c, 200)) / (c.rolling(200, min_periods=200).std())
    f["dist_sma50"] = (c - sma(c, 50)) / a
    f["dist_sma200"] = (c - sma(c, 200)) / a
    f["hi55"] = df["high"].rolling(55, min_periods=55).max()
    f["lo55"] = df["low"].rolling(55, min_periods=55).min()
    f["dist_hi55_atr"] = (f["hi55"] - c) / a
    f["dist_lo55_atr"] = (c - f["lo55"]) / a
    if not df["volume"].isna().all():
        lv = np.log(df["volume"].where(df["volume"] > 0))
        f["vol_z"] = zscore(lv, 20)
        f["cmf20"] = cmf(df)
        f["vwap20_dist"] = (c - rolling_vwap(df)) / a
    else:
        f["vol_z"] = np.nan; f["cmf20"] = np.nan; f["vwap20_dist"] = np.nan
    # autocorrelazione e varianza mobili (segnali di "critical slowing down")
    f["ac1_60"] = ret.rolling(60, min_periods=60).apply(_ac1, raw=True)
    return f
