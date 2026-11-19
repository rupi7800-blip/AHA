"""Backtest su tutta la storia disponibile di una moneta, con le stesse regole di simulazione dei segnali.

Strategie: "aha" (voti + regime, parametri scelti sul primo 60% della finestra) e "simple" (regola semplice EMA200 + stop mobile).
Il capitale è investito al 100% durante un trade (nessuna leva) e in liquidità altrimenti; confronto con buy & hold sulla
stessa finestra. Opzioni: storico esteso dalla nascita (chiusure CoinMetrics, barre ricostruite e dichiarate), funding dei
perpetual, periodo riservato incluso o escluso, filtro per verso."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import signals as sg
from .analysis import indicators as ind


def daily_returns(tr: sg.Trader, trades: list[dict], lo: int, hi: int, fund: np.ndarray | None = None, side_filter: int | None = None) -> np.ndarray:
    """Rendimento giornaliero del capitale per le barre [lo, hi): posizione 100% fissata all'ingresso (numero di unità costante),
    marcatura giornaliera, funding per giorno e costi all'uscita. La somma composta dei giorni di un trade coincide con il suo
    risultato (long: uscita/ingresso - 1; short: 1 - uscita/ingresso), costi esclusi."""
    r = np.zeros(max(0, hi - lo))
    c = tr.c
    for t in trades:
        if side_filter and t["side"] != side_filter:
            continue
        s, e, j, entry = t["side"], t["e"], t["e"] + t["bars"] - 1, t["entry"]
        prev = 1.0
        for k in range(e, j + 1):
            p = t["exit_px"] if k == j else c[k]
            v = 1.0 + s * (p / entry - 1.0)                       # valore del capitale investito nel trade, rispetto all'ingresso
            if fund is not None:
                v -= s * fund[k] * (p / entry)                    # funding sul controvalore del giorno
            if k == j:
                v -= t["cost"]
            v = max(v, 0.0)
            if lo <= k < hi:
                r[k - lo] = (v / prev - 1.0) if prev > 0 else 0.0
            prev = v
    return r


def curve_stats(r: pd.Series) -> dict:
    r = r.dropna()
    if len(r) < 2:
        return {}
    eq = (1 + r).cumprod()
    yrs = len(r) / 365
    return {"total_pct": float((eq.iloc[-1] - 1) * 100), "cagr_pct": float((eq.iloc[-1] ** (1 / yrs) - 1) * 100) if yrs > 0.5 else None,
            "vol_pct": float(r.std() * np.sqrt(365) * 100), "sharpe": float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else None,
            "max_dd_pct": float((eq / eq.cummax() - 1).min() * 100), "days": int(len(r))}


def _yearly(trades: list[dict]) -> list[dict]:
    out = []
    for y in sorted({t["date"][:4] for t in trades}):
        xs = [t for t in trades if t["date"][:4] == y]
        net = np.array([t["net"] for t in xs]); r = np.array([t["r"] for t in xs])
        out.append({"year": y, "n": len(xs), "long": sum(t["side"] > 0 for t in xs), "short": sum(t["side"] < 0 for t in xs),
                    "win_rate": float((net > 0).mean()), "expectancy_pct": float(net.mean() * 100), "expectancy_r": float(r.mean()),
                    "sum_pct": float(net.sum() * 100)})
    return out


def run(df: pd.DataFrame, onchain: dict[str, pd.Series] | None = None, fear_greed: pd.Series | None = None, *,
        strategy: str = "aha", overrides: dict | None = None, start: str | None = None, end: str | None = None, side: str = "all",
        tune_params: bool = True, funding: pd.Series | None = None, include_reserved: bool = False) -> dict:
    """df: barre giornaliere CHIUSE (colonna opzionale `synthetic`). Restituisce statistiche, curve e operazioni."""
    onchain = onchain or {}
    if strategy not in ("aha", "simple"):
        return {"error": "strategia sconosciuta"}
    base = dict(sg.DEFAULTS, **{k: v for k, v in (overrides or {}).items() if k in sg.DEFAULTS})
    cut = df if include_reserved else df[df.index < pd.Timestamp(sg.HOLDOUT_START, tz="UTC")]
    synthetic = cut["synthetic"].values if "synthetic" in cut else np.zeros(len(cut), bool)
    df_in = cut.drop(columns=["synthetic"], errors="ignore")
    n = len(df_in)
    if n < 450:
        return {"error": f"servono almeno 450 barre, ce ne sono {n}"}
    idx = df_in.index
    i0 = 0 if start is None else int(idx.searchsorted(pd.Timestamp(start, tz="UTC")))
    i1 = n if end is None else int(idx.searchsorted(pd.Timestamp(end, tz="UTC"), side="right"))
    i0 = max(i0, 200)                                    # la EMA200 ha bisogno di 200 barre di avvio
    if i1 - i0 < 250:
        return {"error": "finestra troppo corta: servono almeno 250 giorni dopo l'avvio degli indicatori"}
    h = int(base["horizon"])
    split = i0 + int((i1 - i0) * sg.TRAIN_FRAC)          # primo 60% della finestra: scelta dei parametri (aha); poi "fuori campione"
    params, tuning = (sg.tune(df_in.iloc[:split], base, train_frac=1.0) if (strategy == "aha" and tune_params) else (base, {}))
    for k in (overrides or {}):
        if k in base:
            params[k] = base[k]
    atr = ind.atr(df_in)
    tr = sg.Trader(df_in, atr, params)
    score = sg.compute_votes(df_in, onchain, fear_greed, params)[1] if strategy == "aha" else sg.simple_score(df_in)
    trades, skipped = sg.simulate(tr, score, params["min_votes"], start=i0)
    want = {"all": (1, -1), "long": (1,), "short": (-1,)}[side]
    trades = [t for t in trades if t["e"] - 1 < i1 - h and t["side"] in want]
    seg = {"in_sample": [t for t in trades if t["e"] - 1 < split - h], "out_of_sample": [t for t in trades if t["e"] - 1 >= split]}
    first_real = int(np.argmax(~synthetic)) if (~synthetic).any() else n
    syn_tr = [t for t in trades if t["e"] - 1 < first_real]
    real_tr = [t for t in trades if t["e"] - 1 >= first_real]
    fund, fund_cov = None, None
    if funding is not None and len(funding):
        f_in = funding.reindex(idx[i0:i1].normalize())
        covered = int(f_in.notna().sum())
        if covered:                                           # il funding esiste dal 2019-09 e solo per i mesi già pubblicati
            fund = funding.reindex(idx.normalize()).fillna(0.0).values
            fund_cov = {"from": str(funding.index[0].date()), "to": str(funding.index[-1].date()), "days_with_data_in_window": covered,
                        "days_without_data_in_window": int(len(f_in) - covered)}
    sf = {"all": None, "long": 1, "short": -1}[side]
    dates = idx[i0:i1]
    ret = pd.Series(daily_returns(tr, trades, i0, i1, fund, sf), index=dates)
    ret_nf = pd.Series(daily_returns(tr, trades, i0, i1, None, sf), index=dates)
    c = df_in["close"].values
    bh = pd.Series(c[i0:i1] / c[i0 - 1:i1 - 1] - 1, index=dates)
    eq = lambda r: ((1 + r).cumprod() - 1) * 100
    step = max(1, len(dates) // 2500)                    # curve alleggerite per lo smartphone (l'ultimo punto c'è sempre)
    sel = sorted(set(range(0, len(dates), step)) | {len(dates) - 1})
    junction = None
    if synthetic[i0:i1].any() and (~synthetic[i0:i1]).any():
        k = int(np.argmax(~synthetic))
        junction = float(c[k] / c[k - 1] - 1) * 100 if k > 0 else None     # salto tra ultima chiusura ricostruita e prima reale
    out = {
        "strategy": strategy, "side": side, "params": {k: params[k] for k in sg.DEFAULTS}, "tuning": {k: v["chosen"] for k, v in tuning.items()},
        "window": {"start": str(dates[0].date()), "end": str(dates[-1].date()), "days": len(dates), "split": str(idx[split].date()),
                   "first_real_bar": str(idx[first_real].date()) if first_real < n else None,
                   "reserved_included": include_reserved, "junction_gap_pct": junction, "synthetic_bars_in_window": int(synthetic[i0:i1].sum())},
        "stats": sg.by_side(trades), "in_sample": sg.by_side(seg["in_sample"]), "out_of_sample": sg.by_side(seg["out_of_sample"]),
        "synthetic_period": sg.by_side(syn_tr), "real_period": sg.by_side(real_tr),
        "chance": sg.random_timing(tr, split, max(split + 1, i1 - h)),
        "portfolio": {"strategy": curve_stats(ret), "strategy_no_funding": curve_stats(ret_nf) if fund is not None else None,
                      "buy_hold": curve_stats(bh), "exposure_pct": float(sum(t["bars"] for t in trades) / max(1, len(dates)) * 100)},
        "curves": {"t": [str(dates[k].date()) for k in sel], "strategy": [round(float(eq(ret).values[k]), 3) for k in sel],
                   "buy_hold": [round(float(eq(bh).values[k]), 3) for k in sel],
                   "drawdown": [round(float((((1 + ret).cumprod() / (1 + ret).cumprod().cummax() - 1) * 100).values[k]), 3) for k in sel]},
        "yearly": _yearly(trades), "skipped": skipped, "funding_used": fund is not None, "funding_coverage": fund_cov,
        "trades": [{"date": t["date"], "side": "long" if t["side"] > 0 else "short", "entry": round(t["entry"], 6), "exit": round(t["exit_px"], 6),
                    "bars": t["bars"], "exit_reason": t["exit"], "net_pct": round(t["net"] * 100, 2), "r": round(t["r"], 2),
                    "synthetic": bool(t["e"] - 1 < first_real)} for t in trades[-200:][::-1]],
        "n_trades": len(trades),
        "notes": ["Capitale investito al 100% durante un trade, senza leva; liquidità negli altri giorni.",
                  "Costi: commissione + slippage per lato e slippage extra sugli stop (parametri modificabili)."
                  + (f" Funding dei perpetual incluso nel portafoglio (dati dal {fund_cov['from']} al {fund_cov['to']}; "
                     f"{fund_cov['days_without_data_in_window']} giorni della finestra senza dato valgono 0); le statistiche per operazione NON lo includono."
                     if fund is not None else " Funding non incluso."),
                  "Parametri scelti con i dati fino al punto di split (60% della finestra); 'fuori campione' è il restante 40%. Se cambi i parametri "
                  "guardando il fuori campione, quel periodo diventa in campione." if strategy == "aha" and tune_params else "Parametri fissi: se li scegli guardando i risultati, sono in campione.",
                  "Una singola moneta: pochi trade, risultati instabili. Il pool di monete è la valutazione affidabile."]
                 + (["Il tratto precedente alla quotazione su Binance usa barre ricostruite dalle sole chiusure (stop valutati sulle chiusure): risultati approssimati."]
                    if out_has(synthetic[i0:i1]) else []),
    }
    return out


def out_has(a) -> bool:
    return bool(np.asarray(a).any())
