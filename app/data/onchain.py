"""Metriche on-chain e di sentiment da fonti gratuite, più adapter con chiave (Glassnode).

Tutte le funzioni restituiscono FetchResult con un DataFrame indicizzato per data UTC (colonne = metriche).
Metriche gratuite verificate: MVRV, indirizzi attivi, capitalizzazione, numero transazioni e flussi
da/verso exchange (CoinMetrics community), TVL (DefiLlama), Fear & Greed (Alternative.me)."""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from .adapters import FetchResult, _error, http_get

CM_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
# nome interno -> metrica CoinMetrics (piano community, senza chiave)
CM_METRICS = {"mvrv": "CapMVRVCur", "active_addresses": "AdrActCnt", "market_cap": "CapMrktCurUSD",
              "tx_count": "TxCnt", "ex_inflow_usd": "FlowInExUSD", "ex_outflow_usd": "FlowOutExUSD"}


def derive(df: pd.DataFrame) -> pd.DataFrame:
    """Indicatori derivati. NVT: qui è un PROXY (cap. / flussi exchange medi a 90g) perché il volume
    on-chain rettificato non è nel piano gratuito; il vero NVT/SOPR arriva da Glassnode con chiave."""
    d = df.copy()
    if {"ex_inflow_usd", "ex_outflow_usd"} <= set(d):
        d["ex_netflow_usd"] = d["ex_inflow_usd"] - d["ex_outflow_usd"]     # >0 = afflusso = pressione di vendita
        d["ex_netflow_7d"] = d["ex_netflow_usd"].rolling(7).sum()
        vol = (d["ex_inflow_usd"] + d["ex_outflow_usd"]).rolling(90, min_periods=30).mean()
        if "market_cap" in d:
            d["nvt_proxy"] = d["market_cap"] / vol.replace(0, np.nan)
    if "active_addresses" in d:
        d["active_addr_z90"] = (d["active_addresses"] - d["active_addresses"].rolling(90).mean()) / d["active_addresses"].rolling(90).std()
    return d


class CoinMetrics:
    name = "CoinMetrics community"

    def metrics(self, asset: str, since: str | None = None) -> FetchResult:
        try:
            frames = []
            for key, metric in CM_METRICS.items():   # una richiesta per metrica: una non disponibile non blocca le altre
                params = {"assets": asset, "metrics": metric, "frequency": "1d", "page_size": 10000}
                if since:
                    params["start_time"] = since
                try:
                    rows = http_get(CM_URL, params).json().get("data", [])
                except Exception:
                    continue
                if rows:
                    s = pd.Series({pd.Timestamp(r["time"]): float(r[metric]) for r in rows if r.get(metric) is not None}, name=key)
                    frames.append(s)
            if not frames:
                return _error(self.name, "nessuna metrica disponibile")
            df = pd.concat(frames, axis=1).sort_index()
            df.index = pd.to_datetime(df.index, utc=True).normalize()
            return FetchResult(derive(df), self.name, "~1 giorno", meta={"asset": asset})
        except Exception as e:
            return _error(self.name, e)


class DefiLlama:
    name = "DefiLlama"

    def chain_tvl(self, chain: str = "Ethereum") -> FetchResult:
        try:
            rows = http_get(f"https://api.llama.fi/v2/historicalChainTvl/{chain}").json()
            s = pd.Series({pd.Timestamp(r["date"], unit="s", tz="UTC"): float(r["tvl"]) for r in rows}, name="tvl")
            return FetchResult(s.to_frame(), self.name, "~1 giorno")
        except Exception as e:
            return _error(self.name, e)


class FearGreed:
    name = "Alternative.me"

    def index(self) -> FetchResult:
        try:
            rows = http_get("https://api.alternative.me/fng/", {"limit": 0}).json()["data"]
            s = pd.Series({pd.Timestamp(int(r["timestamp"]), unit="s", tz="UTC"): float(r["value"]) for r in rows}, name="fear_greed").sort_index()
            return FetchResult(s.to_frame(), self.name, "~1 giorno")
        except Exception as e:
            return _error(self.name, e)


class Glassnode:
    """Metriche a chiave: SOPR e NVT (e MVRV Glassnode). La chiave arriva da GLASSNODE_API_KEY oppure,
    nell'ambiente cloud, viene iniettata dal proxy nell'header X-Api-Key (si invia un segnaposto).
    Ogni metrica è richiesta da sola: quelle non incluse nel piano della chiave sono saltate e dichiarate."""
    name = "Glassnode"
    PATHS = {"sopr": "indicators/sopr", "nvt": "indicators/nvt", "mvrv_gn": "market/mvrv"}
    ASSETS = {"btc": "BTC", "eth": "ETH"}       # il piano gratuito copre di fatto solo BTC ed ETH

    def __init__(self, key: str | None = None):
        self.key = key or os.environ.get("GLASSNODE_API_KEY") or "proxy-injected"

    def metrics(self, asset: str = "btc", since: int | None = None) -> FetchResult:
        sym = self.ASSETS.get(asset.lower())
        if not sym:
            return _error(self.name, f"{asset}: non coperto dal piano gratuito")
        cols, errors = {}, []
        for k, path in self.PATHS.items():
            try:
                params = {"a": sym, "i": "24h"}
                if since:
                    params["s"] = since
                rows = http_get(f"https://api.glassnode.com/v1/metrics/{path}", params, headers={"X-Api-Key": self.key}).json()
                cols[k] = pd.Series({pd.Timestamp(r["t"], unit="s", tz="UTC"): r["v"] for r in rows if r.get("v") is not None})
            except Exception as e:
                msg = str(e)
                errors.append(f"{k}: " + ("accesso negato o chiave non valida (401/403)" if any(c in msg for c in ("401", "403")) else msg[:80]))
        if not cols:
            return _error(self.name, "; ".join(errors) or "nessuna metrica")
        df = pd.DataFrame(cols)
        df.index = df.index.normalize()
        return FetchResult(df, self.name, "~1 giorno", message="; ".join(errors))


COINMETRICS, DEFILLAMA, FEARGREED = CoinMetrics(), DefiLlama(), FearGreed()
