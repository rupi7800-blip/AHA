"""Strategia trend-following: canale di Donchian + filtro di regime EMA 60.

Regole (long/flat, tutto causale):
  - Regime: close > EMA(ema_n).
  - Entrata: close supera il massimo dei `entry_n` massimi PRECEDENTI (canale spostato di una barra,
    così la barra corrente non entra nel proprio livello) con il regime attivo.
  - Uscita: close sotto il minimo degli `exit_n` minimi precedenti, oppure close sotto l'EMA.
Esecuzione: segnale visto a chiusura della barra t, ingresso/uscita alla chiusura di t+1
(stessa convenzione di app/backtest.py). Costi e slippage per lato, in punti base.
Validazione: griglia (entry_n, exit_n) scelta sul 60% iniziale, valutata sul 40% finale (mai visto)."""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from .analysis.indicators import ema


def donchian(df: pd.DataFrame, entry_n: int, exit_n: int):
    """Canali di Donchian già spostati di una barra (solo informazione passata)."""
    up = df["high"].rolling(entry_n, min_periods=entry_n).max().shift(1)
    lo = df["low"].rolling(exit_n, min_periods=exit_n).min().shift(1)
    return up, lo


def positions(df: pd.DataFrame, entry_n=55, exit_n=20, ema_n=60) -> pd.Series:
    """Posizione desiderata (0/1) decisa alla chiusura di ogni barra."""
    up, lo = donchian(df, entry_n, exit_n)
    e = ema(df["close"], ema_n)
    c = df["close"]
    enter = ((c > up) & (c > e)).values
    leave = ((c < lo) | (c < e)).values
    pos = np.zeros(len(c))
    cur = 0.0
    for i in range(len(c)):
        if cur == 0.0 and enter[i]:
            cur = 1.0
        elif cur == 1.0 and leave[i]:
            cur = 0.0
        pos[i] = cur
    return pd.Series(pos, index=df.index)


def run(df: pd.DataFrame, entry_n=55, exit_n=20, ema_n=60, cost_bps=5.0, slippage_bps=5.0) -> pd.Series:
    """Rendimenti logaritmici netti per barra. Segnale a t, esecuzione alla chiusura di t+1:
    la posizione guadagna il rendimento da t+1 a t+2, quindi lag 2 sulla serie delle posizioni."""
    pos = positions(df, entry_n, exit_n, ema_n)
    eff = pos.shift(2).fillna(0.0)
    ret = np.log(df["close"]).diff().fillna(0.0)
    turnover = eff.diff().abs().fillna(eff.abs())
    return eff * ret - turnover * (cost_bps + slippage_bps) / 1e4


def effective_positions(df: pd.DataFrame, entry_n=55, exit_n=20, ema_n=60) -> pd.Series:
    """Posizione realmente detenuta per barra (segnale a t, esecuzione a t+1: lag 2)."""
    return positions(df, entry_n, exit_n, ema_n).shift(2).fillna(0.0)


def stats(r: pd.Series, bars_per_year=365, exposure: pd.Series | None = None) -> dict:
    """Metriche su una serie di rendimenti log per barra (le barre crypto sono 365/anno).
    `exposure`: posizione detenuta per barra; se assente l'esposizione è stimata da r != 0."""
    if len(r) < 2:
        return {"n_bars": int(len(r))}
    eq = r.cumsum()
    dd = (eq - eq.cummax()).min()
    sd = r.std(ddof=1)
    dsd = np.sqrt((r.clip(upper=0) ** 2).mean())  # deviazione al ribasso su TUTTE le barre
    expo = float(exposure.mean()) if exposure is not None else float((r != 0).mean())
    return {
        "n_bars": int(len(r)),
        "total_return_pct": float((np.exp(eq.iloc[-1]) - 1) * 100),
        "cagr_pct": float((np.exp(eq.iloc[-1] * bars_per_year / len(r)) - 1) * 100),
        "sharpe": float(r.mean() / sd * np.sqrt(bars_per_year)) if sd > 0 else None,
        "sortino": float(r.mean() / dsd * np.sqrt(bars_per_year)) if dsd and dsd > 0 else None,
        "max_drawdown_pct": float((np.exp(dd) - 1) * 100),
        "exposure_pct": expo * 100,
    }


def count_trades(df: pd.DataFrame, entry_n, exit_n, ema_n) -> int:
    p = positions(df, entry_n, exit_n, ema_n)
    return int((p.diff().fillna(p) == 1).sum())


def walk_forward(df: pd.DataFrame, entries=(20, 40, 55, 100), exits=(10, 20), ema_n=60,
                 split=0.6, cost_bps=5.0, slippage_bps=5.0) -> dict:
    """Sceglie (entry_n, exit_n) sul primo `split` della storia (massimo Sharpe), poi li valuta sul resto.
    Gli indicatori sono calcolati sull'intera serie (causali), il confronto è fatto sul tratto di test."""
    cut = int(len(df) * split)
    if cut < ema_n + max(entries) + 10 or len(df) - cut < 30:
        return {"error": "storia insufficiente", "n_bars": int(len(df))}
    grid = []
    for en, ex in itertools.product(entries, exits):
        if ex >= en:
            continue
        r = run(df, en, ex, ema_n, cost_bps, slippage_bps)
        s = stats(r.iloc[:cut])
        grid.append({"entry_n": en, "exit_n": ex, "train_sharpe": s.get("sharpe"),
                     "train_return_pct": s.get("total_return_pct")})
    ranked = [g for g in grid if g["train_sharpe"] is not None]
    if not ranked:
        return {"error": "nessuna combinazione valida", "grid": grid}
    best = max(ranked, key=lambda g: g["train_sharpe"])
    r = run(df, best["entry_n"], best["exit_n"], ema_n, cost_bps, slippage_bps)
    bh = np.log(df["close"]).diff().fillna(0.0)
    eff = effective_positions(df, best["entry_n"], best["exit_n"], ema_n)
    pos = positions(df, best["entry_n"], best["exit_n"], ema_n)
    entries_mask = pos.diff().fillna(pos) == 1
    test_slice = slice(cut, None)
    # per le prime barre del test la posizione dipende da segnali del train: è corretto (causale)
    test_expo = float(eff.iloc[test_slice].mean())
    return {
        "best_params": {"entry_n": best["entry_n"], "exit_n": best["exit_n"], "ema_n": ema_n},
        "grid_train": sorted(grid, key=lambda g: -(g["train_sharpe"] or -9)),
        "train": stats(r.iloc[:cut], exposure=eff.iloc[:cut]),
        "test": stats(r.iloc[test_slice], exposure=eff.iloc[test_slice]),
        "buy_hold_train": stats(bh.iloc[:cut]), "buy_hold_test": stats(bh.iloc[test_slice]),
        # B&H scalato alla stessa esposizione media della strategia: il confronto equo sul rischio
        "buy_hold_test_scaled": stats(bh.iloc[test_slice] * test_expo),
        "trades_total": int(entries_mask.sum()),
        "trades_test": int(entries_mask.iloc[cut:].sum()),
        "split_date": str(df.index[cut])[:10],
        "costs_bps_per_side": cost_bps + slippage_bps,
    }


def rolling_walk_forward(df: pd.DataFrame, entries=(20, 40, 55, 100), exits=(10, 20), ema_n=60,
                         train_bars=1095, test_bars=365, cost_bps=5.0, slippage_bps=5.0) -> dict:
    """Walk-forward rolling: per ogni finestra sceglie (entry_n, exit_n) sulle ultime `train_bars` barre
    (massimo Sharpe) e le applica alle successive `test_bars`; poi la finestra avanza di `test_bars`.
    I rendimenti fuori campione di tutte le finestre sono concatenati: ogni barra del tratto OOS è
    valutata con parametri scelti solo su dati precedenti. Indicatori causali calcolati sull'intera serie."""
    combos = [(en, ex) for en, ex in itertools.product(entries, exits) if ex < en]
    if len(df) < train_bars + test_bars:
        return {"error": "storia insufficiente", "n_bars": int(len(df))}
    runs = {c: run(df, c[0], c[1], ema_n, cost_bps, slippage_bps) for c in combos}
    effs = {c: effective_positions(df, c[0], c[1], ema_n) for c in combos}
    bh = np.log(df["close"]).diff().fillna(0.0)
    oos, oos_eff, windows = [], [], []
    start = 0
    while start + train_bars + test_bars <= len(df):
        a, b, c_ = start, start + train_bars, start + train_bars + test_bars
        scored = []
        for combo in combos:
            s = stats(runs[combo].iloc[a:b]).get("sharpe")
            if s is not None:
                scored.append((s, combo))
        if scored:
            best_s, best = max(scored)
            r_test, e_test = runs[best].iloc[b:c_], effs[best].iloc[b:c_]
        else:  # nessuna combinazione valida nel train: resta flat
            best_s, best = None, None
            r_test, e_test = pd.Series(0.0, index=df.index[b:c_]), pd.Series(0.0, index=df.index[b:c_])
        oos.append(r_test); oos_eff.append(e_test)
        st = stats(r_test, exposure=e_test)
        windows.append({"test_from": str(df.index[b])[:10], "test_to": str(df.index[c_ - 1])[:10],
                        "params": {"entry_n": best[0], "exit_n": best[1]} if best else None,
                        "train_sharpe": best_s, "test_sharpe": st.get("sharpe"),
                        "test_return_pct": st.get("total_return_pct"),
                        "bh_return_pct": stats(bh.iloc[b:c_]).get("total_return_pct")})
        start += test_bars
    r_oos, e_oos = pd.concat(oos), pd.concat(oos_eff)
    bh_oos = bh.loc[r_oos.index]
    expo = float(e_oos.mean())
    sharpes = [w["test_sharpe"] for w in windows if w["test_sharpe"] is not None]
    wins_vs_bh = sum(1 for w in windows if w["test_return_pct"] is not None and w["test_return_pct"] > w["bh_return_pct"])
    se = 1 / np.sqrt(len(r_oos) / 365)  # errore standard approssimato dello Sharpe annualizzato
    return {
        "windows": windows, "n_windows": len(windows),
        "oos_from": str(r_oos.index[0])[:10], "oos_to": str(r_oos.index[-1])[:10],
        "oos": stats(r_oos, exposure=e_oos), "buy_hold_oos": stats(bh_oos),
        "buy_hold_oos_scaled": stats(bh_oos * expo),
        "windows_beating_bh": wins_vs_bh,
        "windows_positive": int(sum(1 for w in windows if (w["test_return_pct"] or 0) > 0)),
        "test_sharpe_mean": float(np.mean(sharpes)) if sharpes else None,
        "sharpe_se_approx": float(se),
        "param_choices": [(w["params"]["entry_n"], w["params"]["exit_n"]) if w["params"] else None for w in windows],
    }


def study(symbols=("BTCUSDT", "ETHUSDT"), interval="1d", **kw) -> dict:
    """Esegue il walk-forward sui simboli presenti nel database locale."""
    from . import storage
    out = {}
    for s in symbols:
        df = storage.load_bars(s, interval)
        out[s] = walk_forward(df, **kw) if len(df) else {"error": "nessun dato nel DB (eseguire la pipeline)"}
    return out
