"""Test del motore dei segnali AHA (dati sintetici: verificano il codice, non il mercato)."""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["AHA_NO_SCHEDULER"] = "1"
from app import signals as sg, storage  # noqa: E402


def synth(n=1500, seed=1, drift=0.0005, vol=0.02):
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, vol, n)
    c = 100 * np.exp(np.cumsum(r))
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    o = np.r_[c[0], c[:-1]]
    hi = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.005, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.005, n)))
    return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": rng.lognormal(10, 0.3, n)}, index=idx)


def test_votes_are_causal():
    df = synth()
    cut = 1000
    full = {k: f(df, sg.DEFAULTS) for k, f in sg.VOTERS.items()}
    part = {k: f(df.iloc[:cut], sg.DEFAULTS) for k, f in sg.VOTERS.items()}
    for k in full:
        assert (full[k].iloc[:cut].values == part[k].values).all(), k


def test_onchain_votes_use_only_past_days():
    df = synth(400)
    mvrv = pd.Series(2.0, index=df.index)
    mvrv.iloc[300] = 0.5                      # fondo solo al giorno 300
    v = sg.onchain_votes(df, {"mvrv": mvrv}, None, sg.DEFAULTS)["mvrv"]
    assert v.iloc[300] == 0 and v.iloc[301] == 1     # ritardo di un giorno: nessun lookahead


def test_tuning_uses_only_training_segment():
    df = synth(1500, seed=3)
    p1, _ = sg.tune(df, sg.DEFAULTS)
    df2 = df.copy()
    split = int(len(df) * sg.TRAIN_FRAC)
    df2.iloc[split + 30:, df2.columns.get_loc("close")] *= 5   # stravolge solo il periodo fuori campione
    df2["open"] = df2["close"].shift(1).fillna(df2["close"]); df2["high"] = df2[["open", "close"]].max(axis=1); df2["low"] = df2[["open", "close"]].min(axis=1)
    p2, _ = sg.tune(df2, sg.DEFAULTS)
    assert {k: p1[k] for k in sg.DEFAULTS} == {k: p2[k] for k in sg.DEFAULTS}


def test_simulate_stop_and_target_levels():
    n = 60
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    c = np.full(n, 100.0)
    df = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1.0}, index=idx)
    df.iloc[12, df.columns.get_loc("high")] = 110.0         # tocca il target di un long
    atr = pd.Series(2.0, index=idx)                          # stop 96 (2xATR), target 106 (3xATR)
    score = pd.Series(0.0, index=idx); score.iloc[10] = 3
    p = dict(sg.DEFAULTS, horizon=10, cost_bps=0, slippage_bps=0)
    t = sg.simulate(df, score, p, atr)
    assert len(t) == 1 and t[0]["exit"] == "target" and t[0]["dir"] == 1
    assert t[0]["net"] == pytest.approx(0.06)
    # short sugli stessi dati: stesso movimento = stop
    score.iloc[10] = -3
    t = sg.simulate(df, score, p, atr)
    assert t[0]["exit"] == "stop" and t[0]["net"] == pytest.approx(-0.04)


def test_costs_reduce_returns():
    df = synth(1200, seed=5)
    s = pd.Series(0.0, index=df.index); s.iloc[::25] = 2
    atr = sg.ind.atr(df)
    a = sg.summarize(sg.simulate(df, s, dict(sg.DEFAULTS, cost_bps=0, slippage_bps=0), atr))
    b = sg.summarize(sg.simulate(df, s, dict(sg.DEFAULTS, cost_bps=20, slippage_bps=20), atr))
    assert b["mean_net_pct"] < a["mean_net_pct"]


def test_build_signal_structure_and_oos_separation():
    df = synth(1500, seed=2)
    r = sg.build_signal(df)
    assert r["action"] in ("ACQUISTA (long)", "VENDI / SHORT", "ATTENDI")
    h = r["history"]
    assert {"in_sample", "out_of_sample", "baseline_out_of_sample", "split_date"} <= set(h)
    assert r["reliability"]["grade"] in ("alta", "media", "bassa", "non valutabile")
    assert r["onchain_missing"] == ["mvrv", "exchange_flow", "fear_greed"]
    if r["direction"]:
        d = r["direction"]
        assert (r["take_profit"] - r["entry"]) * d > 0 and (r["entry"] - r["stop_loss"]) * d > 0


def test_user_overrides_win_over_tuning():
    df = synth(1500, seed=2)
    r = sg.build_signal(df, overrides={"ema_fast": 7, "ema_slow": 21, "horizon": 21})
    assert r["params"]["ema_fast"] == 7 and r["params"]["ema_slow"] == 21 and r["params"]["horizon"] == 21


def test_too_little_data():
    assert sg.build_signal(synth(100))["action"] == "DATI INSUFFICIENTI"


def test_api_signal_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    storage.init(tmp_path / "t.sqlite")
    df = synth(900, seed=4)
    storage.replace_bars("BTC", "1d", df)
    c = TestClient(app)
    r = c.get("/api/aha/signal/BTC?ema_fast=8&ema_slow=21&horizon=10")
    assert r.status_code == 200 and r.json()["params"]["horizon"] == 10
    assert c.get("/api/aha/signal/BTC?pippo=1").status_code == 400
    assert c.get("/api/aha/signal/NOPE").status_code == 404
    assert c.get("/api/aha/signals").status_code == 200
