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


def test_forward_log_records_once_and_evaluates_with_backtest_rules(tmp_path):
    import numpy as np, pandas as pd
    from app import signals as sg, storage, tracking
    from app.analysis import indicators as ind
    storage.init(tmp_path / "tr.sqlite")
    rng = np.random.default_rng(3)
    n = 400
    c = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, n)))
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    df = pd.DataFrame({"open": np.r_[c[0], c[:-1]], "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1.0}, index=idx)
    i = 200
    params = dict(sg.DEFAULTS, trail=0, exit_sr=0, horizon=10)
    a = {"aha": {"side": "long", "asof": str(idx[i].date()), "entry": float(c[i]), "stop_loss": 0.0, "take_profit": 0.0, "params": params}}
    assert tracking.record({"X": a}) == 1 and tracking.record({"X": a}) == 0            # nessun duplicato
    log = tracking.evaluate(lambda coin: df)
    expect = sg.Trader(df, ind.atr(df), params).outcome(i, 1)
    assert log[0]["status"] == "chiuso" and log[0]["r"] == pytest.approx(expect["r"])    # stesse regole del backtest
    # voce troppo recente: resta aperta
    a2 = {"aha": dict(a["aha"], asof=str(idx[-3].date()))}
    tracking.record({"X": a2})
    log = tracking.evaluate(lambda coin: df)
    assert [e["status"] for e in log] == ["chiuso", "aperto"]
    assert tracking.summary(log)["n_open"] == 1 and tracking.summary(log)["long"]["n"] == 1


def test_null_calendar_shift_is_common_across_coins_with_different_windows():
    import pandas as pd
    # due monete con finestre diverse: lo spostamento in giorni è unico, non posizionale per moneta
    a = make_coin(n_days=300, seed=1); b = make_coin(n_days=200, seed=2)
    b["out"]["dates"] = [str(d.date()) for d in pd.date_range("2024-04-01", periods=200)]
    b["trades"] = [[b["out"]["dates"][k], 1, 0.01, 0.004, 0.2, 5.0] for k in range(0, 200, 10)]
    r = V.pooled_side({"A": a, "B": b}, 1)
    assert r["n"] > 0 and r["n_null"] == V.N_NULL and 0 < r["p_vs_chance"] <= 1
