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


def stats(r: pd.Series, bars_per_year=365) -> dict:
    """Metriche su una serie di rendimenti log per barra (le barre crypto sono 365/anno)."""
    if len(r) < 2:
        return {"n_bars": int(len(r))}
    eq = r.cumsum()
    dd = (eq - eq.cummax()).min()
    sd = r.std(ddof=1)
    dsd = np.sqrt((r.clip(upper=0) ** 2).mean())  # deviazione al ribasso su TUTTE le barre
    active = (r != 0)
    return {
        "n_bars": int(len(r)),
        "total_return_pct": float((np.exp(eq.iloc[-1]) - 1) * 100),
        "cagr_pct": float((np.exp(eq.iloc[-1] * bars_per_year / len(r)) - 1) * 100),
        "sharpe": float(r.mean() / sd * np.sqrt(bars_per_year)) if sd > 0 else None,
        "sortino": float(r.mean() / dsd * np.sqrt(bars_per_year)) if dsd and dsd > 0 else None,
        "max_drawdown_pct": float((np.exp(dd) - 1) * 100),
        "exposure_pct": float(active.mean() * 100),
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
    test_slice = slice(cut, None)
    # per le prime barre del test la posizione dipende da segnali del train: è corretto (causale)
    return {
        "best_params": {"entry_n": best["entry_n"], "exit_n": best["exit_n"], "ema_n": ema_n},
        "grid_train": sorted(grid, key=lambda g: -(g["train_sharpe"] or -9)),
        "train": stats(r.iloc[:cut]), "test": stats(r.iloc[test_slice]),
        "buy_hold_train": stats(bh.iloc[:cut]), "buy_hold_test": stats(bh.iloc[test_slice]),
        "trades_total": count_trades(df, best["entry_n"], best["exit_n"], ema_n),
        "split_date": str(df.index[cut])[:10],
        "costs_bps_per_side": cost_bps + slippage_bps,
    }


def study(symbols=("BTCUSDT", "ETHUSDT"), interval="1d", **kw) -> dict:
    """Esegue il walk-forward sui simboli presenti nel database locale."""
    from . import storage
    out = {}
    for s in symbols:
        df = storage.load_bars(s, interval)
        out[s] = walk_forward(df, **kw) if len(df) else {"error": "nessun dato nel DB (eseguire la pipeline)"}
    return out
