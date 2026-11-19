"""Test di supporti e resistenze: individuazione causale e piani di uscita."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import levels as L  # noqa: E402
from app.analysis import indicators as ind  # noqa: E402


def oscillating(n=600, lo=100, hi=120, period=12.0, seed=1):
    t = np.arange(n)
    c = (lo + hi) / 2 + (hi - lo) / 2 * np.sin(t / period) + np.random.default_rng(seed).normal(0, 0.3, n)
    idx = pd.date_range("2022-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"open": np.r_[c[0], c[:-1]], "high": c + 0.8, "low": c - 0.8, "close": c, "volume": 1.0}, index=idx)


def test_finds_range_boundaries():
    df = oscillating(); f = L.LevelFinder(df, ind.atr(df))
    z = f.important(500)
    prices = sorted(round(x.price) for x in z)
    assert any(abs(p - 99) <= 2 for p in prices) and any(abs(p - 121) <= 2 for p in prices)


def test_levels_are_causal():
    df = oscillating(); atr = ind.atr(df); i = 500
    full = [(round(z.price, 4), z.touches) for z in L.LevelFinder(df, atr).important(i)]
    cut = [(round(z.price, 4), z.touches) for z in L.LevelFinder(df.iloc[:i + 1], atr.iloc[:i + 1]).important(i)]
    assert full == cut


def test_pivot_is_usable_only_after_confirmation():
    df = oscillating(); f = L.LevelFinder(df, ind.atr(df))
    w = f.p["pivot_w"]
    for idx, _p, conf, _r, _k in f.piv[:20]:
        assert conf == idx + w
    t = int(f.piv[10][2]) - 1                                                    # un giorno prima della conferma
    assert all(z.last_idx <= t - w for z in f.at(t))


def Z(price, touches=4, strength=4.0, w=0.4):
    return L.Zone(low=price - w, high=price + w, price=price, strength=strength, touches=touches, last_idx=0)


def test_long_plan_stop_below_support_target_before_resistance():
    zones = [Z(95), Z(88), Z(112), Z(130)]
    p = L.plan_exit(1, 100.0, 2.0, zones)
    assert p["ok"] and p["stop"] < 95 - 0.3 * 2 + 1e-9 and p["tp"] < 112 and p["tp"] > 100
    assert p["rr"] >= 1.5 and p["stop_from_levels"] and p["tp_from_levels"]


def test_short_plan_is_mirror_image():
    zones = [Z(105), Z(112), Z(88), Z(70)]
    p = L.plan_exit(-1, 100.0, 2.0, zones)
    assert p["ok"] and p["stop"] > 105 and p["tp"] > 88 and p["tp"] < 100 and p["rr"] >= 1.5


def test_too_close_support_is_skipped_for_next_one():
    p = L.plan_exit(1, 100.0, 2.0, [Z(99.5), Z(94), Z(112)])                    # 99.5 darebbe uno stop sotto 0.8 ATR
    assert p["ok"] and p["stop"] < 94


def test_rejects_when_reward_is_insufficient():
    p = L.plan_exit(1, 100.0, 2.0, [Z(95), Z(102)])                              # resistenza troppo vicina per R:R >= 1.5
    assert not p["ok"] and "rischio/rendimento" in p["why"]


def test_free_space_target_and_atr_fallback_are_declared():
    p = L.plan_exit(1, 100.0, 2.0, [Z(92)])                                      # nessuna resistenza sopra
    assert p["ok"] and "spazio libero" in p["basis_tp"]
    q = L.plan_exit(1, 100.0, 2.0, [])                                           # nessun supporto sotto
    assert q["basis_stop"].startswith("ATR") and not q["stop_from_levels"]


def test_no_target_mode_for_trailing():
    p = L.plan_exit(1, 100.0, 2.0, [Z(95)], need_tp=False)
    assert p["ok"] and p["tp"] is None and p["rr"] is None
