"""Test del backtest su tutta la storia e dello storico esteso."""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["AHA_NO_SCHEDULER"] = "1"
from app import backtester as bt, signals as sg, storage  # noqa: E402
from app.analysis import indicators as ind  # noqa: E402
from app.data import history  # noqa: E402


def synth(n=1800, seed=1, drift=0.0006, vol=0.025, start="2018-01-01"):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    idx = pd.date_range(start, periods=n, freq="D", tz="UTC")
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.01, "low": np.minimum(o, c) * 0.99, "close": c, "volume": 1.0}, index=idx)


def test_run_structure_and_windows():
    df = synth()
    for strat in ("aha", "simple"):
        r = bt.run(df, strategy=strat, include_reserved=True)
        assert not r.get("error")
        assert {"stats", "in_sample", "out_of_sample", "chance", "portfolio", "curves", "yearly", "trades", "window"} <= set(r)
        assert r["window"]["split"] > r["window"]["start"]
        assert len(r["curves"]["t"]) == len(r["curves"]["strategy"]) == len(r["curves"]["buy_hold"])
        assert r["n_trades"] == r["stats"]["all"]["n"]


def test_side_filter_and_errors():
    df = synth()
    both = bt.run(df, strategy="simple", include_reserved=True)
    lo = bt.run(df, strategy="simple", side="long", include_reserved=True)
    sh = bt.run(df, strategy="simple", side="short", include_reserved=True)
    assert lo["n_trades"] + sh["n_trades"] == both["n_trades"]
    assert all(t["side"] == "long" for t in lo["trades"]) and all(t["side"] == "short" for t in sh["trades"])
    assert "error" in bt.run(df.iloc[:300], strategy="simple", include_reserved=True)
    assert "error" in bt.run(df, strategy="simple", start="2022-01-01", end="2022-03-01", include_reserved=True)
    assert "error" in bt.run(df, strategy="boh")


def test_backtest_is_causal_future_changes_do_not_alter_past_trades():
    df = synth(2000, seed=5)
    cut = "2022-06-01"
    a = bt.run(df, strategy="simple", end=cut, include_reserved=True)
    df2 = df.copy()
    k = df2.index.searchsorted(pd.Timestamp("2022-07-01", tz="UTC"))
    df2.iloc[k:, df2.columns.get_loc("close")] *= 4                                     # stravolge solo il futuro
    df2["open"] = df2["close"].shift(1).fillna(df2["close"]); df2["high"] = df2[["open", "close"]].max(axis=1) * 1.01; df2["low"] = df2[["open", "close"]].min(axis=1) * 0.99
    df2.iloc[:k] = df.iloc[:k]
    b = bt.run(df2, strategy="simple", end=cut, include_reserved=True)
    assert [t for t in a["trades"]] == [t for t in b["trades"]]


def test_reserved_period_excluded_by_default():
    df = synth(1800, seed=2, start="2022-01-01")                                       # arriva oltre il 2026-09-10
    r = bt.run(df, strategy="simple")
    assert r["window"]["end"] < sg.HOLDOUT_START and not r["window"]["reserved_included"]
    r2 = bt.run(df, strategy="simple", include_reserved=True)
    assert r2["window"]["end"] > r["window"]["end"]


def test_daily_returns_compound_to_trade_return_for_longs():
    df = synth(900, seed=3)
    params = dict(sg.DEFAULTS, exit_sr=0, trail=0, horizon=10, cost_bps=0, slippage_bps=0, stop_slip_bps=0)
    tr = sg.Trader(df, ind.atr(df), params)
    score = pd.Series(3.0, index=df.index)
    trades, _ = sg.simulate(tr, score, 2)
    longs = [t for t in trades if t["side"] > 0][:5]
    for t in longs:
        r = bt.daily_returns(tr, [t], 0, len(df))
        assert np.prod(1 + r) - 1 == pytest.approx(t["exit_px"] / t["entry"] - 1, rel=1e-9)


def test_funding_reduces_long_returns_and_helps_shorts():
    df = synth(900, seed=3)
    params = dict(sg.DEFAULTS, exit_sr=0, trail=0, horizon=10, cost_bps=0, slippage_bps=0, stop_slip_bps=0)
    tr = sg.Trader(df, ind.atr(df), params)
    trades, _ = sg.simulate(tr, pd.Series(3.0, index=df.index), 2)
    fund = np.full(len(df), 0.0003)                                                    # tasso positivo: i long pagano
    t = trades[0]
    base = bt.daily_returns(tr, [t], 0, len(df)).sum()
    with_f = bt.daily_returns(tr, [t], 0, len(df), fund).sum()
    assert with_f < base
    t_short = dict(t, side=-1)
    assert bt.daily_returns(tr, [t_short], 0, len(df), fund).sum() > bt.daily_returns(tr, [t_short], 0, len(df)).sum()


def test_extend_bars_flags_synthetic_period_and_keeps_real_bars():
    real = synth(500, start="2020-01-01")
    cm = pd.Series(np.linspace(1, 50, 900), index=pd.date_range("2017-08-01", periods=900, freq="D", tz="UTC"))
    ext = history.extend_bars(real, cm)
    syn = ext[ext["synthetic"]]
    assert syn.index.max() < real.index.min() and len(ext) == len(syn) + len(real)
    assert (ext[~ext["synthetic"]]["close"].values == real["close"].values).all()
    assert (syn["high"] >= syn[["open", "close"]].max(axis=1)).all() and (syn["low"] <= syn[["open", "close"]].min(axis=1)).all()
    r = bt.run(ext, strategy="simple", include_reserved=True)
    assert r["window"]["synthetic_bars_in_window"] > 0 and any("ricostruite" in n for n in r["notes"])
    assert r["window"]["first_real_bar"] == str(real.index[0].date())


def test_api_backtest_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    storage.init(tmp_path / "bt.sqlite")
    storage.replace_bars("BTC", "1d", synth(1500, seed=7, start="2021-01-01"))
    c = TestClient(app)
    r = c.get("/api/aha/backtest/BTC?strategy=simple&side=long&include_reserved=1")
    assert r.status_code == 200 and r.json()["strategy"] == "simple" and r.json()["side"] == "long"
    assert c.get("/api/aha/backtest/BTC?strategy=aha&tune=0&horizon=30").json()["params"]["horizon"] == 30
    assert c.get("/api/aha/backtest/BTC?strategy=boh").status_code == 400
    assert c.get("/api/aha/backtest/BTC?start=2020/01/01").status_code == 400
    assert c.get("/api/aha/backtest/BTC?atr_stop=0").status_code == 400
    assert c.get("/api/aha/backtest/BTC?start=2026-01-01&end=2026-02-01&include_reserved=1").status_code == 422
    assert c.get("/api/aha/backtest/NOPE").status_code == 404
    assert '"_trades"' not in r.text and '"_outcomes"' not in r.text
