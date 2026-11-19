"""Test della validazione sul pool di monete."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import validation as V  # noqa: E402


def make_coin(n_days=500, edge=0.0, seed=0, n_trades=60, risk_pct=5.0):
    """Moneta sintetica: esiti per barra con media `edge` R; i trade del segnale sono a barre scelte a caso."""
    rng = np.random.default_rng(seed)
    dates = [str(d.date()) for d in __import__("pandas").date_range("2024-01-01", periods=n_days)]
    R = rng.normal(0.0, 1.0, n_days)
    out = {"dates": dates, "long": [[float(r * risk_pct / 100), float(r)] for r in R], "short": [[0.0, 0.0] for _ in R]}
    pos = rng.choice(n_days, n_trades, replace=False)
    trades = []
    for k in sorted(pos):
        r = R[k] + edge
        trades.append([dates[k], 1, round(r * risk_pct / 100 + 0.004, 5), 0.004, round(float(r), 4), risk_pct])
    return {"trades": trades, "out": out, "split": dates[0], "hold": "2999-01-01", "ins": {}}


def pool(edge, n_coins=8, **kw):
    return {f"C{i}": make_coin(edge=edge, seed=i, **kw) for i in range(n_coins)}


def test_no_edge_is_indistinguishable_from_chance():
    r = V.pooled_side(pool(0.0), 1)
    assert abs(r["expectancy_r"]) < 0.2 and r["p_vs_chance"] > 0.05


def test_planted_edge_is_detected_against_chance():
    r = V.pooled_side(pool(0.8), 1)
    assert r["expectancy_r"] > 0.5 and r["p_vs_chance"] < 0.01
    assert r["ci95_r"][0] > 0
    assert r["robust"]["double_costs_r"] > 0


def test_chance_is_calendar_shift_not_independent_draws():
    # senza edge la media del caso coincide con la media degli esiti; il p-value si calcola come (k+1)/(N+1)
    r = V.pooled_side(pool(0.0), 1)
    assert r["n_null"] == V.N_NULL and r["p_vs_chance"] >= 1 / (V.N_NULL + 1)


def test_holm_adjustment():
    adj = V.holm({"a": 0.01, "b": 0.04, "c": None})
    assert adj["a"] == pytest.approx(0.02) and adj["b"] == pytest.approx(0.04) and adj["c"] is None


def test_grade_rules():
    base = {"n": 120, "months": 15, "expectancy_r": 0.4, "p_vs_chance": 0.01, "profit_factor": 1.5, "ci95_r": [0.1, 0.7],
            "robust": {"double_costs_r": 0.3, "without_top5_r": 0.2, "worst_leave_one_coin_out_r": 0.2}, "in_sample_expectancy_r": 0.3}
    assert V.grade(base, 0.02)[0] == "alta"
    assert V.grade(dict(base, n=20), 0.02)[0] == "non valutabile"
    assert V.grade(dict(base, expectancy_r=-0.1), 0.02)[0] == "bassa"
    assert V.grade(dict(base, p_vs_chance=0.4), 0.4)[0] == "bassa"
    assert V.grade(dict(base, ci95_r=[-0.1, 0.7]), 0.02)[0] == "media"          # IC che include lo zero: non "alta"
    assert V.grade(dict(base, n=60, months=8), 0.02)[0] == "media"


def test_attach_uses_pool_grade_per_side_and_blocks_custom_params():
    val = {"n_coins": 20, "sides": {"long": {"grade": "media", "n": 80}, "short": {"grade": "bassa", "n": 70}}}
    s = V.attach({"side": "short"}, val)
    assert s["reliability"]["grade"] == "bassa" and set(s["reliability_by_side"]) == {"long", "short"}
    assert V.attach({"side": "long"}, val, default_params=False)["reliability"]["grade"] == "non applicabile"
    assert V.attach({"side": None}, val)["reliability"]["grade"] == "nessun segnale"


def _series(n=400, seed=3):
    import numpy as np, pandas as pd
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, n)))
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"open": np.r_[c[0], c[:-1]], "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1.0}, index=idx)


def test_forward_log_one_trade_at_a_time_and_same_rules(tmp_path):
    from app import signals as sg, storage, tracking
    from app.analysis import indicators as ind
    storage.init(tmp_path / "tr.sqlite")
    df = _series(); i = 200
    params = dict(sg.DEFAULTS, trail=0, exit_sr=0, horizon=10)
    mk = lambda d: {"aha": {"side": "long", "asof": str(df.index[d].date()), "entry": float(df["close"].iloc[d]), "stop_loss": 0.0,
                            "take_profit": 0.0, "params": params}}
    assert tracking.record({"X": mk(i)}) == 1 and tracking.record({"X": mk(i)}) == 0           # nessun duplicato
    assert tracking.record({"X": mk(i + 1)}) == 0                                                # stessa moneta ancora aperta: niente voci sovrapposte
    log = tracking.evaluate(lambda coin: df)
    expect = sg.Trader(df, ind.atr(df), params).outcome(i, 1)
    assert log[0]["status"] == "chiuso" and log[0]["r"] == pytest.approx(expect["r"])           # stesse regole del backtest
    assert tracking.record({"X": mk(i + 20)}) == 1                                               # chiusa: si può registrare la successiva


def test_forward_log_closes_early_on_stop_and_stays_open_otherwise(tmp_path):
    import numpy as np, pandas as pd
    from app import signals as sg, storage, tracking
    from app.analysis import indicators as ind
    storage.init(tmp_path / "tr2.sqlite")
    df = _series(); n = len(df); i = n - 6                                      # solo 5 barre dopo il segnale, orizzonte 30
    df = df.copy()
    params = dict(sg.DEFAULTS, trail=0, exit_sr=0, horizon=30, cost_bps=0, slippage_bps=0, stop_slip_bps=0)
    tr = sg.Trader(df, ind.atr(df), params)
    assert tr.outcome(i, 1, partial=True) in ({"open": True},) or tr.outcome(i, 1, partial=True)["exit"] in ("stop", "target")
    assert tr.outcome(i, 1) is None                                              # la versione completa aspetta l'orizzonte
    df.iloc[i + 2, df.columns.get_loc("low")] = df["close"].iloc[i] * 0.5       # crollo: stop colpito al terzo giorno
    o = sg.Trader(df, ind.atr(df), params).outcome(i, 1, partial=True)
    assert o["exit"] == "stop" and o["bars"] == 2 and o["r"] < 0


def test_forward_chance_and_summary(tmp_path):
    from app import signals as sg, storage, tracking
    storage.init(tmp_path / "tr3.sqlite")
    df = _series()
    params = dict(sg.DEFAULTS, trail=0, exit_sr=0, horizon=10)
    log = [{"coin": "X", "side": "long", "asof": str(df.index[100].date()), "params": params, "status": "chiuso", "r": 0.5, "net": 0.02, "exit": "target", "bars": 5}]
    ch = tracking.chance(log, lambda c: df)
    assert ch["long"]["n"] > 100 and ch["short"]["n"] > 100
    s = tracking.summary(log)
    assert s["all"]["expectancy_r"] == pytest.approx(0.5) and s["n_open"] == 0


def test_daily_funding_sums_three_settlements_per_day(monkeypatch):
    import pandas as pd
    from app.data import funding
    t0 = int(pd.Timestamp("2026-08-01", tz="UTC").timestamp() * 1000)
    rows = [{"calc_time": t0 + k * 8 * 3600 * 1000 + 1, "funding_interval_hours": 8, "last_funding_rate": 0.0001 * (k + 1)} for k in range(6)]
    monkeypatch.setattr(funding, "_month", lambda s, m: pd.DataFrame(rows) if m == "2026-08" else None)
    s = funding.daily_funding("BTCUSDT", start="2026-08")
    assert list(s.round(6)) == [0.0006, 0.0015]                  # 3 regolamenti al giorno sommati; mesi assenti saltati
    assert s.index[0] == pd.Timestamp("2026-08-01", tz="UTC")
