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


def _flat(n=80, hi_pad=1.0, lo_pad=1.0):
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    c = np.full(n, 100.0)
    return pd.DataFrame({"open": c, "high": c + hi_pad, "low": c - lo_pad, "close": c, "volume": 1.0}, index=idx)


def _trader(df, **p):
    params = dict(sg.DEFAULTS, exit_sr=0, trail=0, horizon=10, cost_bps=0, slippage_bps=0, stop_slip_bps=0, atr_stop=2.0, atr_tp=3.0, **p)
    atr = pd.Series(2.0, index=df.index)
    return sg.Trader(df, atr, params)


def test_trader_target_and_stop_levels():
    df = _flat(); df.iloc[12, df.columns.get_loc("high")] = 110.0          # tocca il target del long (106)
    tr = _trader(df)
    o = tr.outcome(10, 1)
    assert o["exit"] == "target" and o["net"] == pytest.approx(0.06) and o["r"] == pytest.approx(1.5)
    s = tr.outcome(10, -1)                                                  # stesso movimento per uno short = stop a 104
    assert s["exit"] == "stop" and s["net"] == pytest.approx(-0.04) and s["r"] == pytest.approx(-1.0)


def test_gap_through_stop_fills_at_open_worse_than_minus_one_r():
    df = _flat()
    df.iloc[13, df.columns.get_loc("open")] = 90.0                           # apertura sotto lo stop (96)
    df.iloc[13, df.columns.get_loc("low")] = 89.0
    o = _trader(df).outcome(10, 1)
    assert o["exit"] == "stop" and o["net"] == pytest.approx(-0.10) and o["r"] < -1.5


def test_costs_and_stop_slippage_reduce_returns():
    df = _flat(); df.iloc[12, df.columns.get_loc("high")] = 110.0
    free = _trader(df).outcome(10, 1)["net"]
    tr = _trader(df); tr.p.update(cost_bps=10, slippage_bps=10)
    assert tr.outcome(10, 1)["net"] == pytest.approx(free - 0.004)           # 2 x (10+10) bps
    d2 = _flat(); d2.iloc[12, d2.columns.get_loc("low")] = 90.0
    a = _trader(d2).outcome(10, 1)["net"]
    t2 = _trader(d2); t2.p.update(stop_slip_bps=10)
    assert t2.outcome(10, 1)["net"] == pytest.approx(a - 0.001)


def test_signal_is_decided_on_close_and_entered_next_open():
    df = _flat(); df.iloc[11, df.columns.get_loc("open")] = 101.0
    o = _trader(df).outcome(10, 1)
    assert o["e"] == 11                                                      # ingresso all'apertura della barra successiva


def test_simulate_one_trade_at_a_time():
    df = _flat(300)
    tr = _trader(df)
    score = pd.Series(3.0, index=df.index)
    trades, _ = sg.simulate(tr, score, 2)
    ends = [t["e"] + t["bars"] for t in trades]
    assert all(trades[k + 1]["e"] >= ends[k] for k in range(len(trades) - 1))


def test_stats_report_payoff_and_expectancy():
    tr = [{"net": 0.02, "r": 1.0, "exit": "target", "side": 1}] * 3 + [{"net": -0.03, "r": -1.0, "exit": "stop", "side": 1}] * 7
    s = sg.stats(tr)
    assert s["win_rate"] == pytest.approx(0.3) and s["payoff"] == pytest.approx(0.02 / 0.03)
    assert s["expectancy_pct"] == pytest.approx((3 * 2 - 7 * 3) / 10)        # vincere il 30% con payoff < 1 è in perdita
    assert s["profit_factor"] < 1


def test_build_signal_structure_and_segments():
    df = synth(1500, seed=2)
    r = sg.build_signal(df)
    assert r["action"] in ("ACQUISTA (long)", "VENDI / SHORT", "ATTENDI")
    h = r["history"]
    assert {"in_sample", "out_of_sample", "random_out_of_sample", "split_date", "holdout_start"} <= set(h)
    assert "holdout" not in h                                          # il periodo riservato non è mai esposto
    assert set(h["out_of_sample"]) == {"all", "long", "short"}
    assert h["split_date"] < h["holdout_start"]
    assert "_trades" in r and "_outcomes" in r
    if r["direction"] and r["side"]:
        d = r["direction"]
        assert (r["entry"] - r["stop_loss"]) * d > 0


def test_holdout_never_influences_tuning_or_oos():
    df = synth(1500, seed=3)
    hold = str(df.index[1300].date())
    r1 = sg.build_signal(df, holdout_start=hold)
    df2 = df.copy()
    df2.iloc[1305:, df2.columns.get_loc("close")] *= 3                          # stravolge solo il periodo riservato
    df2["open"] = df2["close"].shift(1).fillna(df2["close"]); df2["high"] = df2[["open", "close"]].max(axis=1) * 1.01; df2["low"] = df2[["open", "close"]].min(axis=1) * 0.99
    df2.iloc[:1300] = df.iloc[:1300]
    r2 = sg.build_signal(df2, holdout_start=hold)
    assert r1["tuning"] == r2["tuning"]
    assert r1["history"]["out_of_sample"] == r2["history"]["out_of_sample"]         # nessun prezzo riservato nelle statistiche
    assert r1["history"]["in_sample"] == r2["history"]["in_sample"]
    assert r1["_trades"] == r2["_trades"] and r1["_outcomes"] == r2["_outcomes"]
    assert all(x[0] < hold for x in r1["_trades"])
    assert all(x["date"] < hold for x in r1["history"]["recent_trades"]) and r1["history"]["recent_trades"] == r2["history"]["recent_trades"]


def test_oos_trades_are_selected_by_entry_bar_symmetrically_with_chance():
    df = synth(1500, seed=4)
    r = sg.build_signal(df, holdout_start=str(df.index[1300].date()))
    dates = r["_outcomes"]["dates"]
    h = int(r["params"]["horizon"])
    assert dates[-1] == str(df.index[1300 - h - 1].date())                           # ultimo ingresso valutabile = n_pre - horizon - 1
    assert all(t[0] <= dates[-1] for t in r["_trades"])                              # i trade del segnale: stesso insieme di ingressi del caso


def test_trailing_stop_only_moves_in_favourable_direction():
    from app.levels import LevelFinder, trailing_stop
    n = 300
    idx = pd.date_range("2022-01-01", periods=n, freq="D", tz="UTC")
    t = np.arange(n)
    c = 100 + 0.2 * t + 3 * np.sin(t / 4.0)                                      # trend rialzista con oscillazioni
    df = pd.DataFrame({"open": np.r_[c[0], c[:-1]], "high": c + 1, "low": c - 1, "close": c, "volume": 1.0}, index=idx)
    atr = pd.Series(2.0, index=idx)
    f = LevelFinder(df, atr)
    stop, last = 90.0, 90.0
    for j in range(120, 220):
        stop = trailing_stop(f, 1, 120, j, stop, 2.0, 0.3)
        assert stop >= last
        last = stop
    assert stop > 90.0                                                           # in un trend rialzista lo stop sale


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


def test_sopr_and_nvt_votes():
    df = synth(600, seed=6)
    sopr = pd.Series(1.01, index=df.index); sopr.iloc[400:420] = 0.95        # capitolazione
    nvt = pd.Series(np.linspace(40, 60, 600), index=df.index); nvt.iloc[450:] = 200   # NVT estremo
    v = sg.onchain_votes(df, {"sopr": sopr, "nvt": nvt}, None, sg.DEFAULTS)
    assert v["sopr"].iloc[415] == 1 and v["sopr"].iloc[380] == 0
    assert v["nvt"].iloc[500] == -1


def test_glassnode_partial_and_unsupported(monkeypatch):
    from app.data import onchain
    def fake_get(url, params=None, retries=3, headers=None):
        assert headers["X-Api-Key"]
        if "sopr" in url:
            raise RuntimeError("401 Client Error")
        return type("R", (), {"json": lambda self: [{"t": 1700000000, "v": 1.2}]})()
    monkeypatch.setattr(onchain, "http_get", fake_get)
    r = onchain.Glassnode(key="test").metrics("btc")
    assert r.status == "ok" and "nvt" in r.df and "sopr" not in r.df and "sopr:" in r.message
    assert onchain.Glassnode(key="test").metrics("sol").status == "errore"
    off = onchain.Glassnode(key="")
    assert not off.enabled and "a pagamento" in off.metrics("btc").message


def test_api_chart_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    storage.init(tmp_path / "t2.sqlite")
    storage.replace_bars("ETH", "1d", synth(900, seed=8))
    c = TestClient(app)
    d = c.get("/api/aha/chart/ETH?days=120&ema_fast=9&ema_slow=40").json()
    s = d["series"]
    assert len(s["t"]) == 120 and len(s["c"]) == 120 and len(s["ema_fast"]) == 120
    assert {"rsi", "macd", "bb_up", "bb_lo"} <= set(s) and "mvrv" not in s       # senza on-chain niente pannelli on-chain
    assert d["signal"]["params"]["ema_fast"] == 9
    assert c.get("/api/").status_code in (404, 405, 200)
    assert c.get("/").status_code == 200 and "AHA" in c.get("/").text
    assert c.get("/classic").status_code == 200


def test_simple_rule_block_and_pool_validation_key():
    from app import validation as V, tracking
    df = synth(1500, seed=9)
    r = sg.build_signal(df, holdout_start=str(df.index[1300].date()))
    s = r["simple"]
    assert {"history", "direction", "action", "name", "rule", "_trades", "_outcomes"} <= set(s)
    # la regola semplice: verso = segno di (chiusura - EMA200); nessun voto di indicatori
    from app.analysis import indicators as ind
    expect = 1 if df["close"].iloc[-1] > ind.ema(df["close"], 200).iloc[-1] else -1
    assert s["direction"] == expect or s["action"] == "ATTENDI"
    assert V.run({"X": {"aha": r}}, key="simple")["n_coins"] == 1
    # il registro in avanti tiene le due strategie separate
    log = [{"coin": "X", "strategy": "simple", "status": "chiuso", "r": 1.0, "net": 0.05, "side": "long"},
           {"coin": "X", "strategy": "aha", "status": "aperto", "side": "long"}]
    by = tracking.summary_by_strategy(log)
    assert by["simple"]["n_closed"] == 1 and by["aha"]["n_open"] == 1
