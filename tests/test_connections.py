"""Test dei collegamenti alle fonti con risposte HTTP simulate (nessuna rete)."""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["AHA_NO_SCHEDULER"] = "1"
from app.data import adapters, onchain  # noqa: E402


class Resp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def _klines(n, t0=1_700_000_000_000, day=86_400_000):
    return [[t0 + i * day, "100", "110", "90", str(100 + i), "10", 0, "0", 0, "4", "0", "0"] for i in range(n)]


def test_binance_pagination_and_taker_ratio(monkeypatch):
    pages = [_klines(1000), _klines(1000, t0=1_700_000_000_000 + 1000 * 86_400_000)[:5]]
    calls = []

    def fake_get(url, params=None, retries=3):
        calls.append(params["startTime"])
        return Resp(pages[len(calls) - 1])
    monkeypatch.setattr(adapters, "http_get", fake_get)
    r = adapters.ADAPTERS["binance"].bars("BTCUSDT", "1d")
    assert r.status == "ok" and len(r.df) == 1005 and len(calls) == 2
    assert float(r.meta["taker_buy_ratio"].iloc[0]) == 0.4
    assert r.df.index.is_monotonic_increasing and r.df.index.tz is not None


def test_binance_falls_back_to_mirror(monkeypatch):
    seen = []

    def fake_get(url, params=None, retries=3):
        seen.append(url)
        if "api.binance.com" in url:
            raise RuntimeError("bloccato")
        return Resp(_klines(3))
    monkeypatch.setattr(adapters, "http_get", fake_get)
    r = adapters.ADAPTERS["binance"].bars("ETHUSDT")
    assert r.status == "ok" and len(r.df) == 3
    assert any("data-api.binance.vision" in u for u in seen)


def test_binance_error_is_reported_not_raised(monkeypatch):
    def fake_get(url, params=None, retries=3):
        raise RuntimeError("rete assente")
    monkeypatch.setattr(adapters, "http_get", fake_get)
    r = adapters.ADAPTERS["binance"].bars("BTCUSDT")
    assert r.status == "errore" and "rete assente" in r.message and r.df.empty


def test_onchain_derived_metrics():
    idx = pd.date_range("2025-01-01", periods=120, freq="D", tz="UTC")
    df = pd.DataFrame({"ex_inflow_usd": 2e9, "ex_outflow_usd": 1e9, "market_cap": 1e12,
                       "active_addresses": np.arange(120) + 1000.0}, index=idx)
    d = onchain.derive(df)
    assert (d["ex_netflow_usd"] == 1e9).all()          # afflusso netto = pressione di vendita
    assert d["nvt_proxy"].iloc[-1] == 1e12 / 3e9
    assert np.isfinite(d["active_addr_z90"].iloc[-1])


def test_coinmetrics_skips_unavailable_metric(monkeypatch):
    def fake_get(url, params=None, retries=3):
        m = params["metrics"]
        if m == "FlowInExUSD":
            raise RuntimeError("403")
        return Resp({"data": [{"time": "2025-01-01T00:00:00.000000000Z", m: "1.5"}]})
    monkeypatch.setattr(onchain, "http_get", fake_get)
    r = onchain.COINMETRICS.metrics("btc")
    assert r.status == "ok" and "mvrv" in r.df and "ex_inflow_usd" not in r.df


def test_glassnode_without_key_is_explicit(monkeypatch):
    monkeypatch.delenv("GLASSNODE_API_KEY", raising=False)
    r = onchain.Glassnode().metrics()
    assert r.status == "errore" and "a pagamento" in r.message
