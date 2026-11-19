"""Storico esteso: prezzi di chiusura giornalieri dalla NASCITA delle monete (CoinMetrics community, gratuito).

Binance parte dalla quotazione (BTC 2017, DOGE 2019, ...), CoinMetrics dal primo prezzo (BTC 2010, ETH 2015, XRP 2014, LTC 2013...).
Per il tratto precedente alla quotazione esistono solo chiusure: le barre vengono ricostruite con open = chiusura precedente,
high/low = estremi tra open e close (nessuna ombra intragiornaliera). Gli stop in quel tratto si valutano quindi sulle chiusure:
i risultati sono APPROSSIMATI e il backtest li separa da quelli su dati reali (colonna `synthetic`)."""
from __future__ import annotations

import pandas as pd

from .adapters import FetchResult, _error, http_get

CM_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"


def coinmetrics_close(asset: str) -> FetchResult:
    """Chiusure giornaliere dalla prima data disponibile (una richiesta da 10000 righe copre 27 anni; si segue comunque `next_page_url`)."""
    try:
        rows, url, params = [], CM_URL, {"assets": asset, "metrics": "PriceUSD", "frequency": "1d", "start_time": "2009-01-01",
                                         "page_size": 10000, "paging_from": "start"}
        for _ in range(10):
            j = http_get(url, params).json()
            rows += [(pd.Timestamp(r["time"]), float(r["PriceUSD"])) for r in j.get("data", []) if r.get("PriceUSD") is not None]
            url, params = j.get("next_page_url"), None
            if not url:
                break
        if not rows:
            return _error("CoinMetrics community", "nessun prezzo")
        s = pd.Series(dict(rows), name="close").sort_index()
        s.index = pd.to_datetime(s.index, utc=True).normalize()
        return FetchResult(s.to_frame(), "CoinMetrics community", "~1 giorno")
    except Exception as e:
        return _error("CoinMetrics community", e)


def extend_bars(real: pd.DataFrame, cm_close: pd.Series | None) -> pd.DataFrame:
    """Barre reali (Binance) precedute dalle barre ricostruite dalle sole chiusure CoinMetrics. Colonna `synthetic`: True per le ricostruite."""
    real = real.copy()
    real["synthetic"] = False
    if cm_close is None or len(cm_close) == 0 or len(real) == 0:
        return real
    first = real.index[0]
    pre = cm_close[cm_close.index < first.normalize()].dropna()
    if len(pre) < 2:
        return real
    o = pre.shift(1).fillna(pre.iloc[0])
    syn = pd.DataFrame({"open": o, "high": pd.concat([o, pre], axis=1).max(axis=1), "low": pd.concat([o, pre], axis=1).min(axis=1),
                        "close": pre, "volume": float("nan")}, index=pre.index)
    syn["synthetic"] = True
    return pd.concat([syn, real]).sort_index()
