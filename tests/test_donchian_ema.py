import numpy as np
import pandas as pd

from app import donchian_ema as de


def _df(close):
    close = np.asarray(close, float)
    idx = pd.date_range("2020-01-01", periods=len(close), freq="D", tz="UTC")
    return pd.DataFrame({"open": close, "high": close * 1.005, "low": close * 0.995,
                         "close": close, "volume": 1.0}, index=idx)


def _trend_then_crash(n_up=300, n_down=150, seed=0):
    rng = np.random.default_rng(seed)
    up = 100 * np.exp(np.cumsum(rng.normal(0.004, 0.01, n_up)))
    down = up[-1] * np.exp(np.cumsum(rng.normal(-0.006, 0.01, n_down)))
    return _df(np.concatenate([up, down]))


def test_donchian_channel_is_shifted():
    df = _df(np.arange(1, 101, dtype=float))
    up, lo = de.donchian(df, 20, 10)
    # il livello alla barra t non include l'high della barra t
    assert up.iloc[30] == df["high"].iloc[10:30].max()
    assert lo.iloc[30] == df["low"].iloc[20:30].min()


def test_no_lookahead_on_future_data():
    df = _trend_then_crash()
    p_full = de.positions(df)
    p_cut = de.positions(df.iloc[:250])
    pd.testing.assert_series_equal(p_full.iloc[:250], p_cut)


def test_flat_in_downtrend_and_long_in_uptrend():
    df = _trend_then_crash()
    p = de.positions(df)
    assert p.iloc[:300].max() == 1.0          # entra nel trend rialzista
    assert p.iloc[-1] == 0.0                  # fuori dopo il crollo


def test_execution_lag_and_costs():
    df = _trend_then_crash()
    p = de.positions(df)
    r0 = de.run(df, cost_bps=0, slippage_bps=0)
    r1 = de.run(df, cost_bps=5, slippage_bps=5)
    first = p[p == 1].index[0]
    i = df.index.get_loc(first)
    assert r0.iloc[: i + 2].abs().sum() == 0   # nessun rendimento prima di t+2
    assert r1.sum() < r0.sum()                  # i costi riducono il rendimento


def test_walk_forward_structure_and_insufficient_history():
    df = _trend_then_crash(500, 300)
    res = de.walk_forward(df)
    assert {"best_params", "train", "test", "buy_hold_test", "grid_train"} <= set(res)
    assert res["best_params"]["exit_n"] < res["best_params"]["entry_n"]
    assert "error" in de.walk_forward(df.iloc[:60])


def test_rolling_walk_forward_structure_and_oos_coverage():
    df = _trend_then_crash(900, 600)
    res = de.rolling_walk_forward(df, train_bars=500, test_bars=250)
    assert res["n_windows"] == 4                      # (1500-500)//250
    assert len(res["param_choices"]) == res["n_windows"]
    # le finestre OOS sono contigue e non si sovrappongono
    froms = [w["test_from"] for w in res["windows"]]
    tos = [w["test_to"] for w in res["windows"]]
    assert froms == sorted(froms) and all(t < f for t, f in zip(tos[:-1], froms[1:]))
    assert 0 <= res["oos"]["exposure_pct"] <= 100
    assert res["oos"]["n_bars"] == 1000
    assert "error" in de.rolling_walk_forward(df.iloc[:300])


def test_rolling_params_use_only_past_data():
    # modificare i prezzi DOPO la prima finestra di test non cambia i parametri scelti per la prima finestra
    df = _trend_then_crash(900, 600)
    base = de.rolling_walk_forward(df, train_bars=500, test_bars=250)
    df2 = df.copy()
    df2.iloc[900:] = df2.iloc[900:] * 1.5            # dopo la fine della prima finestra di test (500+250=750 -> barre >=750)
    chg = de.rolling_walk_forward(df2, train_bars=500, test_bars=250)
    assert base["param_choices"][0] == chg["param_choices"][0]
    assert base["param_choices"][1] == chg["param_choices"][1]


def test_exposure_trades_and_scaled_benchmark():
    df = _trend_then_crash(500, 300)
    res = de.walk_forward(df)
    assert 0 <= res["test"]["exposure_pct"] <= 100
    assert res["trades_test"] <= res["trades_total"]
    # B&H scalato: stessa esposizione media => volatilità minore del B&H pieno
    assert res["buy_hold_test_scaled"]["max_drawdown_pct"] >= res["buy_hold_test"]["max_drawdown_pct"]
    eff = de.effective_positions(df, **{k: res["best_params"][k] for k in ("entry_n", "exit_n", "ema_n")})
    assert set(eff.unique()) <= {0.0, 1.0}
