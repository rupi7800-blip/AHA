"""Livello dati AHA: fonti gratuite del mondo crypto.

Ogni adapter restituisce un FetchResult (mai eccezioni verso l'esterno): lo stato "ok" o "errore"
e il messaggio permettono all'interfaccia di dire all'utente quando un dato manca."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import pandas as pd
import requests

TIMEOUT = (6, 20)
INTRADAY_HISTORY_DAYS = 120
BINANCE_HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]
KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_vol",
              "trades", "taker_base", "taker_quote", "ignore"]


@dataclass
class FetchResult:
    df: pd.DataFrame
    source: str
    delay: str = "-"
    status: str = "ok"
    message: str = ""
    meta: dict = field(default_factory=dict)


_session = requests.Session()
_session.headers["User-Agent"] = "AHA-crypto-analytics/0.1"


def http_get(url: str, params: dict | None = None, retries: int = 3, headers: dict | None = None):
    """GET con ripetizione sugli errori temporanei (429, 5xx, rete) e attesa crescente."""
    last = None
    for i in range(retries):
        try:
            r = _session.get(url, params=params, headers=headers, timeout=TIMEOUT)
            if r.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"HTTP {r.status_code}")
                time.sleep(0.5 * 2 ** i)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(0.5 * 2 ** i)
    raise last


def _error(source: str, msg: str) -> FetchResult:
    return FetchResult(pd.DataFrame(), source, "-", "errore", str(msg)[:300])


class Binance:
    name = "Binance"

    def bars(self, symbol: str, interval: str = "1d", since: pd.Timestamp | None = None) -> FetchResult:
        """Candele OHLCV. Con `since` scarica solo il tratto finale; senza, tutta la storia (a pagine da 1000)."""
        start = int(pd.Timestamp(since).timestamp() * 1000) if since is not None else 0
        if since is None and interval != "1d":
            # le barre intraday servono solo per gli ultimi mesi: evita decine di pagine di storico
            start = int((pd.Timestamp.now("UTC") - pd.Timedelta(days=INTRADAY_HISTORY_DAYS)).timestamp() * 1000)
        rows: list = []
        host_err = None
        for host in BINANCE_HOSTS:
            try:
                cur, rows = start, []
                while True:
                    r = http_get(f"{host}/api/v3/klines",
                                 {"symbol": symbol, "interval": interval, "startTime": cur, "limit": 1000})
                    batch = r.json()
                    if not batch:
                        break
                    rows += batch
                    if len(batch) < 1000:
                        break
                    cur = batch[-1][0] + 1
                host_err = None
                break
            except Exception as e:  # prova il mirror ufficiale
                host_err = e
        if host_err is not None:
            return _error(self.name, host_err)
        if not rows:
            return _error(self.name, "nessuna candela restituita")
        d = pd.DataFrame(rows, columns=KLINE_COLS)
        d.index = pd.to_datetime(d["open_time"], unit="ms", utc=True)
        for c in ("open", "high", "low", "close", "volume", "taker_base"):
            d[c] = d[c].astype(float)
        taker = (d["taker_base"] / d["volume"].replace(0, float("nan"))).rename("taker_buy_ratio")
        out = d[["open", "high", "low", "close", "volume"]]
        out = out[~out.index.duplicated(keep="last")]
        return FetchResult(out, self.name, "tempo reale", meta={"taker_buy_ratio": taker})


ADAPTERS = {"binance": Binance()}
