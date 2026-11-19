"""Funding dei perpetual USDT-M di Binance, dai file pubblici mensili di data.binance.vision.

Il funding si paga ogni 8 ore: con tasso positivo chi è LONG paga e chi è SHORT incassa (con tasso negativo il contrario).
Questa è la voce di costo che un backtest sui soli prezzi spot ignora. Serie restituita: somma giornaliera dei tassi (UTC)."""
from __future__ import annotations

import io
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

BASE = "https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{ym}.zip"


def _month(symbol: str, ym: str) -> pd.DataFrame | None:
    try:
        r = requests.get(BASE.format(s=symbol, ym=ym), timeout=(6, 30))
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
        df = pd.read_csv(z.open(z.namelist()[0]))
    except Exception:                      # risposta non valida (es. pagina del proxy): mese saltato
        return None
    return df if len(df) else None


def daily_funding(symbol: str, start: str = "2019-09", workers: int = 6) -> pd.Series:
    """Somma giornaliera dei tassi di funding. Mesi mancanti (non ancora pubblicati o simbolo non ancora quotato) sono saltati."""
    months = [str(p) for p in pd.period_range(start, pd.Timestamp.now("UTC").tz_localize(None).to_period("M") - 1, freq="M")]
    with ThreadPoolExecutor(workers) as ex:
        parts = [d for d in ex.map(lambda m: _month(symbol, m), months) if d is not None]
    if not parts:
        return pd.Series(dtype=float, name="funding")
    d = pd.concat(parts)
    d.index = pd.to_datetime(d["calc_time"], unit="ms", utc=True)
    s = d["last_funding_rate"].astype(float).groupby(d.index.normalize()).sum()
    s.name = "funding"
    return s.sort_index()
