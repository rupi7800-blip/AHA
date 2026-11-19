"""Persistenza SQLite: barre di prezzo, metadati di aggiornamento, registro previsioni,
alert, impostazioni utente. Un solo file, nessun server di database."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import os
DB_PATH = Path(os.environ.get("AHA_DB") or Path(__file__).resolve().parent.parent / "data" / "aha.sqlite")
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
  inst TEXT, interval TEXT, ts TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
  PRIMARY KEY (inst, interval, ts));
CREATE TABLE IF NOT EXISTS fetch_log (
  inst TEXT, interval TEXT, source TEXT, fetched_at TEXT, status TEXT, delay TEXT, message TEXT,
  PRIMARY KEY (inst, interval));
CREATE TABLE IF NOT EXISTS predictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  inst TEXT, horizon TEXT, horizon_bars INTEGER, model TEXT,
  created_at TEXT, data_asof TEXT, close_asof REAL, threshold REAL,
  p_down REAL, p_flat REAL, p_up REAL, skill REAL, shown_as_probability INTEGER,
  resolved_at TEXT, realized_return REAL, realized_class TEXT,
  UNIQUE (inst, horizon, model, data_asof));
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, inst TEXT, kind TEXT, created_at TEXT, data_asof TEXT,
  severity REAL, payload TEXT, read INTEGER DEFAULT 0, UNIQUE (inst, kind, data_asof));
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS series (
  key TEXT, ts TEXT, value REAL, PRIMARY KEY (key, ts));
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


_KV_CACHE: dict = {}   # cache in memoria dei valori già letti (le analisi sono JSON grandi)


@contextmanager
def conn(write: bool = True):
    """Connessione SQLite. In modalità WAL le letture non bloccano e non vengono bloccate dalle
    scritture: l'interfaccia resta veloce anche mentre il ciclo di aggiornamento salva dati.
    Le scritture restano serializzate dal lock."""
    lk = _lock if write else _nolock
    with lk:
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA synchronous=NORMAL")
        try:
            yield c
            c.commit()
        finally:
            c.close()


class _NoLock:
    def __enter__(self): return self
    def __exit__(self, *a): return False


_nolock = _NoLock()


def init(path: Path | None = None) -> None:
    global DB_PATH
    if path is not None and Path(path) != DB_PATH:
        DB_PATH = Path(path)
        _KV_CACHE.clear()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)


# ---------- barre ----------
def save_bars(inst: str, interval: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    rows = _bar_rows(inst, interval, df)
    with conn() as c:
        c.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?)", rows)


def _bar_rows(inst, interval, df):
    return [
        (inst, interval, ts.isoformat(), float(r.open), float(r.high), float(r.low), float(r.close),
         None if pd.isna(r.volume) else float(r.volume))
        for ts, r in df.iterrows()
    ]


def replace_bars(inst: str, interval: str, df: pd.DataFrame) -> None:
    """Sostituisce l'intera storia (download completo): elimina anche barre che la fonte ha rimosso."""
    if df is None or df.empty:
        return
    rows = _bar_rows(inst, interval, df)
    with conn() as c:
        c.execute("DELETE FROM bars WHERE inst=? AND interval=?", (inst, interval))
        c.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?)", rows)


def _bars_query(where, args):
    with conn(write=False) as c:
        rows = c.execute(f"SELECT ts, open, high, low, close, volume FROM bars WHERE {where} ORDER BY ts", args).fetchall()
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    return df.set_index("ts")


def load_bars(inst: str, interval: str = "1d") -> pd.DataFrame:
    return _bars_query("inst=? AND interval=?", (inst, interval))


def load_bars_since(inst: str, interval: str, since) -> pd.DataFrame:
    return _bars_query("inst=? AND interval=? AND ts>=?", (inst, interval, pd.Timestamp(since).isoformat()))


def log_fetch(inst, interval, source, status, delay, message="") -> None:
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO fetch_log VALUES (?,?,?,?,?,?,?)",
                  (inst, interval, source, utcnow(), status, delay, message))


def fetch_info(inst, interval="1d") -> dict | None:
    with conn(write=False) as c:
        r = c.execute("SELECT * FROM fetch_log WHERE inst=? AND interval=?", (inst, interval)).fetchone()
    return dict(r) if r else None


# ---------- serie generiche (COT, tono news) ----------
def save_series(key: str, s: pd.Series) -> None:
    rows = [(key, pd.Timestamp(i).isoformat(), float(v)) for i, v in s.dropna().items()]
    with conn() as c:
        c.executemany("INSERT OR REPLACE INTO series VALUES (?,?,?)", rows)


def load_series(key: str) -> pd.Series:
    with conn(write=False) as c:
        rows = c.execute("SELECT ts, value FROM series WHERE key=? ORDER BY ts", (key,)).fetchall()
    if not rows:
        return pd.Series(dtype=float)
    return pd.Series([r["value"] for r in rows], index=pd.to_datetime([r["ts"] for r in rows], utc=True))


# ---------- key-value (impostazioni, cache analisi) ----------
def kv_get(k: str, default=None):
    """Lettura con cache in memoria: le pagine dell'interfaccia rileggono decine di analisi a ogni
    richiesta; senza cache ogni volta si rileggerebbero e decodificherebbero megabyte di JSON.
    ATTENZIONE: il valore restituito è condiviso, chi lo modifica deve prima copiarlo."""
    if k in _KV_CACHE:
        v = _KV_CACHE[k]
        return default if v is None else v
    with conn(write=False) as c:
        r = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    v = json.loads(r["v"]) if r else None
    _KV_CACHE[k] = v
    return default if v is None else v


def kv_set(k: str, v) -> None:
    txt = json.dumps(v, default=str)
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, txt))
    _KV_CACHE[k] = json.loads(txt)


# ---------- previsioni ----------
def log_prediction(p: dict) -> None:
    cols = ["inst", "horizon", "horizon_bars", "model", "created_at", "data_asof", "close_asof", "threshold",
            "p_down", "p_flat", "p_up", "skill", "shown_as_probability"]
    with conn() as c:
        c.execute(f"INSERT OR IGNORE INTO predictions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                  [p[k] for k in cols])


def open_predictions() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM predictions WHERE resolved_at IS NULL")]


def resolve_prediction(pid: int, ret: float, cls: str) -> None:
    with conn() as c:
        c.execute("UPDATE predictions SET resolved_at=?, realized_return=?, realized_class=? WHERE id=?",
                  (utcnow(), ret, cls, pid))


def predictions(inst: str | None = None, resolved_only=False) -> list[dict]:
    q, a = "SELECT * FROM predictions WHERE 1=1", []
    if inst:
        q += " AND inst=?"; a.append(inst)
    if resolved_only:
        q += " AND resolved_at IS NOT NULL"
    with conn(write=False) as c:
        return [dict(r) for r in c.execute(q + " ORDER BY created_at DESC", a)]


# ---------- alert ----------
def add_alert(inst, kind, data_asof, severity, payload) -> bool:
    with conn() as c:
        cur = c.execute("INSERT OR IGNORE INTO alerts (inst, kind, created_at, data_asof, severity, payload) "
                        "VALUES (?,?,?,?,?,?)", (inst, kind, utcnow(), data_asof, severity, json.dumps(payload)))
        return cur.rowcount > 0


def list_alerts(limit=200) -> list[dict]:
    with conn(write=False) as c:
        rows = c.execute("SELECT * FROM alerts ORDER BY created_at DESC, severity DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r); d["payload"] = json.loads(d["payload"]); out.append(d)
    return out


def mark_alert_read(aid: int) -> None:
    with conn() as c:
        c.execute("UPDATE alerts SET read=1 WHERE id=?", (aid,))
