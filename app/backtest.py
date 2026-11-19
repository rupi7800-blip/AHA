"""Backtest dei segnali come studio di eventi con costi.

Per ogni occorrenza del segnale (calcolato causalmente) si entra alla CHIUSURA della barra
successiva a quella del segnale (si assume di vedere il segnale a chiusura e agire il giorno dopo)
e si esce dopo `horizon` barre. Operazioni sovrapposte sono escluse (un'operazione alla volta),
così la curva dei rendimenti non conta due volte lo stesso movimento.

Metriche: numero operazioni, % corrette, rendimento medio netto, confronto con il rendimento
medio incondizionato sullo stesso orizzonte (il vero 'vantaggio'), Sharpe e Sortino annualizzati,
massimo drawdown, rapporto guadagno/perdita medio. Walk-forward: stesse metriche separate
in prima metà (in-sample storico) e seconda metà, per verificare la stabilità del segnale."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .scanner import RULES_BY_ID


def _stats(rets: np.ndarray, horizon: int, bars_per_year=252):
    if len(rets) == 0:
        return {"n": 0}
    eq = np.cumsum(rets)
    dd = float((eq - np.maximum.accumulate(np.concatenate([[0], eq]))[1:]).min())
    per_year = bars_per_year / horizon
    sd = rets.std(ddof=1) if len(rets) > 1 else np.nan
    down = rets[rets < 0]
    dsd = np.sqrt((down ** 2).mean()) if len(down) else np.nan
    wins, losses = rets[rets > 0], rets[rets <= 0]
    return {
        "n": int(len(rets)), "hit_rate": float((rets > 0).mean()), "mean_net_pct": float(rets.mean() * 100),
        "sharpe": float(rets.mean() / sd * np.sqrt(per_year)) if sd and sd > 0 else None,
        "sortino": float(rets.mean() / dsd * np.sqrt(per_year)) if dsd and dsd > 0 else None,
        "max_drawdown_pct": float(dd * 100),
        "payoff": float(wins.mean() / -losses.mean()) if len(wins) and len(losses) and losses.mean() < 0 else None,
    }


def backtest_rule(df: pd.DataFrame, feat: pd.DataFrame, rule_id: str, cost_bps=5.0, slippage_bps=5.0,
                  horizon: int | None = None, direction: int | None = None):
    r = RULES_BY_ID[rule_id]
    h = horizon or r.horizon
    d = direction if direction is not None else (r.direction or 1)
    f = feat.copy(); f["close"] = df["close"]
    sig = r.fn(f).values
    c = df["close"].values
    level = (df["close"] <= 0).any()
    trades, i, n = [], 0, len(c)
    while i < n - h - 1:
        if sig[i]:
            e, x = i + 1, i + 1 + h
            gross = (c[x] - c[e]) / abs(c[e]) if level else np.log(c[x] / c[e])
            net = d * gross - 2 * (cost_bps + slippage_bps) / 1e4
            trades.append((df.index[e], net, d * gross))
            i = x
        else:
            i += 1
    rets = np.array([t[1] for t in trades])
    # rendimento incondizionato medio sullo stesso orizzonte (benchmark del vantaggio)
    fwd = (df["close"].shift(-h) / df["close"]).apply(np.log) if not level else df["close"].diff(h).shift(-h) / df["close"].abs()
    uncond = float(d * fwd.dropna().mean() * 100)
    half = len(rets) // 2
    res = {"rule": rule_id, "name": r.name, "horizon_bars": h, "direction": d,
           "costs_bps_round_trip": 2 * (cost_bps + slippage_bps), "all": _stats(rets, h),
           "first_half": _stats(rets[:half], h), "second_half": _stats(rets[half:], h),
           "unconditional_mean_pct": uncond,
           "trades": [{"date": str(t[0])[:10], "net_pct": round(t[1] * 100, 3)} for t in trades[-60:]],
           "equity": np.round(np.cumsum(rets) * 100, 3).tolist()}
    a = res["all"]
    if a["n"] >= 20:
        res["edge_vs_unconditional_pct"] = a["mean_net_pct"] - uncond
        # t-test sul rendimento medio netto meno l'incondizionato
        se = rets.std(ddof=1) / np.sqrt(len(rets))
        res["t_stat"] = float((rets.mean() - uncond / 100) / se) if se > 0 else None
    return res
