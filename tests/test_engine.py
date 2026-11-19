"""Test del motore. I dati qui sono SINTETICI e servono solo a verificare il codice
(assenza di look-ahead, calibrazione, robustezza): non vengono mai mostrati nell'app."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import pipeline as pl, storage
from app.analysis import indicators as ind
from app.backtest import backtest_rule
from app.predict.model import walk_forward
from app.scanner import RULES


def synth(n=3000, seed=0, drift=0.0002, ar=0.0):
    rng = np.random.default_rng(seed)
    r = np.zeros(n); h = np.full(n, 1e-4)
    for t in range(1, n):
        h[t] = 2e-6 + 0.08 * r[t - 1] ** 2 + 0.9 * h[t - 1]
        r[t] = drift + ar * r[t - 1] + np.sqrt(h[t]) * rng.standard_normal()
    c = 100 * np.exp(np.cumsum(r))
    idx = pd.bdate_range("2014-01-01", periods=n, tz="UTC")
    hi = c * (1 + np.abs(rng.normal(0, 0.005, n))); lo = c * (1 - np.abs(rng.normal(0, 0.005, n)))
    op = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": op, "high": np.maximum.reduce([hi, c, op]), "low": np.minimum.reduce([lo, c, op]),
                         "close": c, "volume": rng.lognormal(10, 0.3, n)}, index=idx)


def test_features_are_causal():
    df = synth()
    T = 2000
    f_cut = ind.feature_frame(df.iloc[:T])
    df2 = df.copy()
    df2.iloc[T:, :4] *= 1.5  # futuro alterato
    f_full = ind.feature_frame(df2).iloc[:T]
    cols = [c for c in f_cut.columns]
    a, b = f_cut[cols].values, f_full[cols].values
    same = np.isclose(a, b, equal_nan=True, rtol=1e-9, atol=1e-12)
    assert same.all(), f"look-ahead in {[c for c, s in zip(cols, same.all(0)) if not s]}"


def test_signals_are_causal():
    df = synth(seed=3)
    T = 2200
    f_cut = ind.feature_frame(df.iloc[:T])
    df2 = df.copy(); df2.iloc[T:, :4] *= 0.6
    f_full = ind.feature_frame(df2)
    for r in RULES:
        s1 = r.fn(f_cut).values
        s2 = r.fn(f_full).values[:T]
        assert (s1 == s2).all(), r.id


def test_prediction_unchanged_by_future():
    df = synth(seed=5)
    T = 2400
    f = ind.feature_frame(df.iloc[:T])
    w1 = walk_forward(f, df["close"].iloc[:T], 5)
    df2 = df.copy(); df2.iloc[T:, :4] *= 2
    f2 = ind.feature_frame(df2).iloc[:T]
    w2 = walk_forward(f2, df2["close"].iloc[:T], 5)
    for m in ("base_rate", "logit", "analog"):
        assert np.allclose(w1["models"][m]["current"], w2["models"][m]["current"])


def test_no_skill_on_random_walk_is_not_shown_as_probability():
    """Su un random walk senza memoria il modello non deve dichiarare vantaggio (salvo raro caso)."""
    shown = 0
    for seed in range(4):
        df = synth(seed=100 + seed, drift=0.0)
        f = ind.feature_frame(df)
        w = walk_forward(f, df["close"], 5)
        shown += int(w["shown_as_probability"])
        assert abs(sum(w["probabilities"].values()) - 1) < 1e-6
    assert shown <= 1


def test_skill_detected_with_real_predictability():
    """Con autocorrelazione forte (AR 0.25) il modello deve trovare skill fuori campione."""
    df = synth(seed=7, ar=0.25, n=3500)
    f = ind.feature_frame(df)
    w = walk_forward(f, df["close"], 3)
    assert w["models"]["logit"]["brier_skill"] > 0


def test_backtest_runs_and_costs_reduce_returns():
    df = synth(seed=9)
    f = ind.feature_frame(df)
    a = backtest_rule(df, f, "momentum_strong", 0, 0)
    b = backtest_rule(df, f, "momentum_strong", 10, 10)
    if a["all"]["n"]:
        assert b["all"]["mean_net_pct"] < a["all"]["mean_net_pct"]


def test_full_analysis_pipeline(tmp_path):
    storage.init(tmp_path / "t.sqlite")
    inst = {"id": "TEST", "name": "Test sintetico", "market": "crypto", "source": "binance", "symbol": "TEST"}
    storage.save_bars("TEST", "1d", synth(seed=11))
    storage.log_fetch("TEST", "1d", "test", "ok", "-", "")
    cfg = pl.settings()
    a = pl.analyze_instrument(inst, cfg, str(tmp_path / "t.sqlite"))
    import json
    json.dumps(a, allow_nan=False)  # JSON standard valido
    assert "predictions" in a and "1 settimana" in a["predictions"]
    assert a["meta"]["freshness"].startswith("obsoleto")  # i dati sintetici finiscono nel passato
    pl.log_predictions(a)
    assert len(storage.predictions("TEST")) > 0


def test_plans_are_consistent():
    from app.plans import build_plans, simulate_plan
    df = synth(seed=21)
    f = ind.feature_frame(df)
    sig = (f["mom63"] > 1).fillna(False).values
    tr = simulate_plan(df, f["atr"], sig, 1, 21, 1.5, 3.0)
    assert tr and all(t["exit"] in ("stop", "obiettivo", "scadenza") for t in tr)
    # una perdita da stop non può superare ~1.5 ATR + costi salvo gap di apertura
    sigs = [{"rule": "momentum_strong", "name": "x", "family": "quantitativa", "direction": 1, "intensity": 50,
             "horizon_bars": 21, "why": "w", "invalidation": "i"},
            {"rule": "squeeze", "name": "s", "family": "tecnica", "direction": 0, "intensity": 50,
             "horizon_bars": 21, "why": "w", "invalidation": "i"}]
    out = build_plans({"id": "T", "source": "binance"}, df, f, sigs)
    p = out["plans"][0]
    assert p["stop"] < p["entry"] < p["target"] and abs(p["rr"] - 2) < 1e-9
    assert len(out["plans"]) == 3  # 1 diretto + 2 condizionati di attesa
    assert build_plans({"id": "UST10Y", "source": "fred", "kind": "yield"}, df, f, sigs)["tradable"] is False
