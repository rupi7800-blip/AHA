"""Benchmark: la strategia predefinita (T3) contro buy & hold e contro una regola semplice, sullo stesso periodo fuori campione,
con il funding VERO dei perpetual (data.binance.vision).

Confronti (portafoglio equipesato sulle monete disponibili, posizione 100% del capitale durante un trade, liquidità altrimenti):
- Buy & hold equipesato.
- Strategia SOLO LONG (equivalente spot, niente funding).
- Strategia long+short su perpetual SENZA funding (irrealistico) e CON funding.
- Regola semplice senza segnali: sopra EMA200 sempre long, sotto sempre short, con lo stesso stop mobile e gli stessi costi
  (serve a capire se i voti degli indicatori aggiungono qualcosa).
Periodo: dal punto di split di ciascuna moneta fino al periodo riservato (mai toccato).
Uso: AHA_DB=/percorso/db.sqlite python research/benchmark.py"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import pipeline as pl, signals as sg, storage  # noqa: E402
from app.analysis import indicators as ind  # noqa: E402
from app.data.funding import daily_funding  # noqa: E402


def load(cid):
    df = storage.load_bars(cid, "1d").iloc[:-1]
    oc = {c: storage.load_series(f"onchain:{cid}:{c}") for c in pl.ONCHAIN_COLS}
    return df, {k: v for k, v in oc.items() if len(v)}, storage.load_series("fear_greed:global")


def daily_returns(tr: sg.Trader, trades: list[dict], lo: int, hi: int, fund: np.ndarray | None, side_filter=None) -> np.ndarray:
    """Rendimento giornaliero (posizione 100%) per le barre [lo, hi): marcatura giornaliera, costi all'uscita, funding per giorno."""
    r = np.zeros(hi - lo)
    o, c = tr.o, tr.c
    for t in trades:
        if side_filter and t["side"] != side_filter:
            continue
        s, e, j = t["side"], t["e"], t["e"] + t["bars"] - 1
        for k in range(e, j + 1):
            if not (lo <= k < hi):
                continue
            if k == e:
                x = t["exit_px"] if j == e else c[e]
                ret = s * (x / o[e] - 1)
            elif k == j:
                ret = s * (t["exit_px"] / c[k - 1] - 1)
            else:
                ret = s * (c[k] / c[k - 1] - 1)
            if fund is not None:
                ret -= s * fund[k]
            if k == j:
                ret -= t["cost"]
            r[k - lo] = ret
    return r


def stats(r: pd.Series) -> dict:
    r = r.dropna()
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    yrs = len(r) / 365
    return {"total_pct": float((eq.iloc[-1] - 1) * 100), "cagr_pct": float((eq.iloc[-1] ** (1 / yrs) - 1) * 100) if yrs > 0 else None,
            "vol_pct": float(r.std() * np.sqrt(365) * 100), "sharpe": float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else None,
            "max_dd_pct": float(dd * 100), "days": int(len(r))}


def main():
    storage.init(storage.DB_PATH)
    ids = [i["id"] for i in pl.UNIVERSE["instruments"]]
    cols = {k: {} for k in ("bh", "long_spot", "ls_nofund", "ls_fund", "simple_ls_fund", "simple_long_spot")}
    expo = {k: [] for k in ("long_spot", "ls_fund", "simple_ls_fund")}
    cover = {}
    for cid in ids:
        df, oc, fg = load(cid)
        n = len(df)
        n_pre = int((df.index < pd.Timestamp(sg.HOLDOUT_START, tz="UTC")).sum())
        if n_pre < 400:
            continue
        params, _ = sg.tune(df.iloc[:n_pre], dict(sg.DEFAULTS))
        h = int(params["horizon"]); split = int(n_pre * sg.TRAIN_FRAC)
        votes, score = sg.compute_votes(df, oc, fg, params)
        atr = ind.atr(df)
        tr = sg.Trader(df, atr, params)
        fs = daily_funding(inst_symbol := next(i["symbol"] for i in pl.UNIVERSE["instruments"] if i["id"] == cid))
        fund = fs.reindex(df.index.normalize()).fillna(0.0).values if len(fs) else None
        cover[cid] = {"funding_days": int(len(fs)), "from": str(fs.index[0].date()) if len(fs) else None}
        trades, _ = sg.simulate(tr, score, params["min_votes"])
        oos = [t for t in trades if split <= t["e"] - 1 < n_pre - h]
        simple, _ = sg.simulate(tr, pd.Series(3.0 * np.sign(df["close"] - ind.ema(df["close"], 200)).fillna(0).values, index=df.index), params["min_votes"])
        simple = [t for t in simple if split <= t["e"] - 1 < n_pre - h]
        dates = df.index[split:n_pre]
        mk = lambda r: pd.Series(r, index=dates)
        c = df["close"].values
        cols["bh"][cid] = mk(c[split:n_pre] / c[split - 1:n_pre - 1] - 1)
        cols["long_spot"][cid] = mk(daily_returns(tr, oos, split, n_pre, None, side_filter=1))
        cols["ls_nofund"][cid] = mk(daily_returns(tr, oos, split, n_pre, None))
        cols["ls_fund"][cid] = mk(daily_returns(tr, oos, split, n_pre, fund))
        cols["simple_ls_fund"][cid] = mk(daily_returns(tr, simple, split, n_pre, fund))
        cols["simple_long_spot"][cid] = mk(daily_returns(tr, simple, split, n_pre, None, side_filter=1))
        for k, trl in (("long_spot", [t for t in oos if t["side"] == 1]), ("ls_fund", oos), ("simple_ls_fund", simple)):
            expo[k].append(sum(t["bars"] for t in trl) / (n_pre - split))
        print("fatto", cid, file=sys.stderr)
    names = {"bh": "Buy & hold (equipesato)", "long_spot": "Strategia solo long (spot, niente funding)",
             "ls_nofund": "Strategia long+short, funding IGNORATO", "ls_fund": "Strategia long+short su perpetual, CON funding",
             "simple_long_spot": "Regola semplice solo long (sopra EMA200, stop mobile, spot)",
             "simple_ls_fund": "Regola semplice long+short con funding (senza voti)"}
    out = {}
    print("# Benchmark fuori campione (pool di %d monete, portafoglio equipesato)\n" % len(cover))
    print("| Portafoglio | rend. totale % | annuo % | volatilità % | Sharpe | max drawdown % | giorni |")
    print("|---|---|---|---|---|---|---|")
    for k, nm in names.items():
        P = pd.DataFrame(cols[k]).mean(axis=1, skipna=True)
        st = stats(P); out[k] = st
        print(f"| {nm} | {st['total_pct']:+.0f} | {st['cagr_pct']:+.1f} | {st['vol_pct']:.0f} | {st['sharpe']:+.2f} | {st['max_dd_pct']:.0f} | {st['days']} |")
    print("\nEsposizione media (quota dei giorni in posizione): " + ", ".join(f"{k} {np.mean(v) * 100:.0f}%" for k, v in expo.items()))
    # per moneta: strategia con funding vs buy & hold
    wins = sum(1 for cid in cols["bh"] if (1 + cols["ls_fund"][cid]).prod() > (1 + cols["bh"][cid]).prod())
    print(f"\nMonete in cui la strategia long+short con funding batte il buy & hold: {wins} su {len(cols['bh'])}")
    fsum = {cid: float(pd.Series(cols["bh"][cid]).size) for cid in cols["bh"]}
    json.dump({"portfolio": out, "funding_coverage": cover, "exposure": {k: float(np.mean(v)) for k, v in expo.items()}},
              open(Path(__file__).parent / "benchmark.json", "w"), indent=1)


if __name__ == "__main__":
    main()
