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


def test_atr_variant_causal_and_exits():
    df = _trend_then_crash()
    p_full = de.positions_atr(df)
    p_cut = de.positions_atr(df.iloc[:250])
    pd.testing.assert_series_equal(p_full.iloc[:250], p_cut)
    assert p_full.iloc[:300].max() == 1.0 and p_full.iloc[-1] == 0.0


def test_vol_size_capped_and_causal():
    df = _trend_then_crash()
    s = de.vol_size(df, cap=1.0)
    assert s.max() <= 1.0 and s.min() >= 0.0
    pd.testing.assert_series_equal(s.iloc[:200], de.vol_size(df.iloc[:200]))


def test_build_pos_variants_and_unknown():
    df = _trend_then_crash()
    base = de.build_pos(df, "base", 40, 20)
    vt = de.build_pos(df, "voltarget", 40, 20)
    assert (vt <= base + 1e-12).all()                      # nessuna leva: taglia <= posizione base
    assert set(de.build_pos(df, "atr", 40, 3.0).unique()) <= {0.0, 1.0}
    try:
        de.build_pos(df, "boh", 1, 1)
    except ValueError:
        pass
    else:
        raise AssertionError("variante sconosciuta deve fallire")


def test_rolling_variants_and_fixed_params():
    df = _trend_then_crash(900, 600)
    for v in ("atr", "voltarget"):
        res = de.rolling_walk_forward(df, train_bars=500, test_bars=250, variant=v)
        assert res["variant"] == v and res["n_windows"] == 4 and res["oos"]["n_bars"] == 1000
    fixed = de.rolling_walk_forward(df, entries=(40,), exits=(20,), train_bars=500, test_bars=250)
    assert set(fixed["param_choices"]) == {(40, 20)}      # nessuna ottimizzazione: stessi parametri ovunque


def test_portfolio_aggregation():
    a = de.rolling_walk_forward(_trend_then_crash(900, 600, seed=1), train_bars=500, test_bars=250)
    b = de.rolling_walk_forward(_trend_then_crash(900, 600, seed=2), train_bars=500, test_bars=250)
    p = de.portfolio({"A": a, "B": b, "C": {"error": "storia insufficiente"}})
    assert p["n_assets"] == 2 and set(p["per_asset"]) == {"A", "B"}
    assert 0 <= p["assets_sharpe_above_bh"] <= 2
    assert "error" in de.portfolio({"C": {"error": "x"}})


def test_hourly_bars_per_year_changes_annualisation():
    df = _trend_then_crash(900, 600)
    d = de.rolling_walk_forward(df, train_bars=500, test_bars=250)
    h = de.rolling_walk_forward(df, train_bars=500, test_bars=250, bars_per_year=8760)
    # SE = sqrt(barre_anno / n): il rapporto tra le due annualizzazioni e' sqrt(8760/365)
    assert abs(h["sharpe_se_approx"] / d["sharpe_se_approx"] - (8760 / 365) ** 0.5) < 1e-9
    assert h["oos"]["sharpe"] != d["oos"]["sharpe"]


def test_ema_filter_can_be_disabled():
    df = _trend_then_crash()
    p = de.positions(df, 40, 20, ema_n=None)
    assert set(p.unique()) <= {0.0, 1.0}
    pd.testing.assert_series_equal(p.iloc[:250], de.positions(df.iloc[:250], 40, 20, ema_n=None))
    assert p.iloc[:300].max() == 1.0 and p.iloc[-1] == 0.0       # esce comunque sul minimo del canale
    assert set(de.positions_atr(df, 40, ema_n=None).unique()) <= {0.0, 1.0}


def test_robustness_costs_never_improve_sharpe():
    bars = {"A": _trend_then_crash(900, 600, seed=1), "B": _trend_then_crash(900, 600, seed=2)}
    rows = de.robustness(bars, params=((40, 20),), emas=(None, 60), costs_bps_per_side=(10, 40, 100),
                         train_bars=500, test_bars=250)
    assert len(rows) == 6
    for e in (None, 60):
        sh = [r["sharpe"] for r in rows if r["ema_n"] == e]       # stesso ordine dei costi crescenti
        assert sh == sorted(sh, reverse=True)


def _fake_bars(monkeypatch, series):
    from app import storage
    monkeypatch.setattr(storage, "load_bars", lambda sym, interval="1d": series[sym])


def test_current_signals_drop_incomplete_bar_and_states(monkeypatch):
    n = 400
    today = pd.Timestamp.now("UTC").normalize()
    idx = pd.date_range(end=today, periods=n, freq="D")           # l'ultima barra e' quella di OGGI (incompleta)
    up = 100 * np.exp(np.cumsum(np.full(n, 0.004)))
    dn = 100 * np.exp(np.cumsum(np.r_[np.full(n - 80, 0.004), np.full(80, -0.01)]))
    mk = lambda c: pd.DataFrame({"open": c, "high": c * 1.005, "low": c * 0.995, "close": c, "volume": 1.0}, index=idx)
    _fake_bars(monkeypatch, {"UP": mk(up), "DN": mk(dn), "NEW": mk(up[:20])})
    out = {r["symbol"]: r for r in de.current_signals(["UP", "DN", "NEW"])}
    assert out["UP"]["state"] in ("LONG", "INGRESSO (nuovo segnale)")
    assert out["UP"]["last_closed_bar"] == str((today - pd.Timedelta(days=1)).date())   # barra di oggi scartata
    assert out["DN"]["state"] in ("FLAT", "USCITA (nuovo segnale)")
    assert out["NEW"]["state"] == "DATI INSUFFICIENTI"
    assert out["UP"]["exit_level"] < out["UP"]["close"] < out["UP"]["entry_level"] * 1.01
