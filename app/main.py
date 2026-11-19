"""API web e server dei file statici."""
from __future__ import annotations

import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import pipeline as pl
from . import backtester, execution, signals, storage, tracking, validation
from .analysis import indicators as ind
from .backtest import backtest_rule
from .scanner import OPPORTUNITY_TYPES, RULES, RULES_BY_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
STATIC = Path(__file__).resolve().parent.parent / "static"
@asynccontextmanager
async def _lifespan(app):
    storage.init()
    # aggiornamento SOLO manuale (tasto "Aggiorna dati"); lo scheduler periodico è opt-in con AHA_SCHEDULER=1
    if os.environ.get("AHA_SCHEDULER") == "1" and not os.environ.get("AHA_NO_SCHEDULER"):
        pl.start_scheduler()
    yield


app = FastAPI(title="AHA - Alpha Horizon Crypto Analytics", version=pl.ENGINE_VERSION, lifespan=_lifespan)
# compressione: le risposte JSON (serie dei grafici) e Plotly si riducono di 4-8 volte,
# decisivo da smartphone su rete mobile
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)


def _analysis(iid):
    a = storage.kv_get(f"analysis:{iid}")
    if not a:
        return None
    return {k: v for k, v in a.items() if not k.startswith("_") or k == "_features_last"}


@app.get("/api/status")
def status():
    st = dict(pl.STATE)
    if not st.get("last_run"):
        st["last_run"] = storage.kv_get("last_run")
    return {**st, "version": pl.ENGINE_VERSION, "n_instruments": len(pl.instruments()), "db": str(storage.DB_PATH)}


@app.post("/api/refresh")
def refresh(force: bool = False):
    import threading
    if pl.STATE["running"]:
        return {"started": False, "message": "aggiornamento già in corso"}
    threading.Thread(target=pl.run_cycle, kwargs={"force": force}, daemon=True).start()
    return {"started": True}


@app.get("/api/universe")
def universe():
    s = pl.settings()
    return {"markets": pl.UNIVERSE["markets"], "enabled_markets": s["enabled_markets"],
            "instruments": [dict(i, enabled=(i["market"] in s["enabled_markets"] and i["id"] not in s["disabled_instruments"]))
                            for i in pl.instruments(True)],
            "rules": [{"id": r.id, "name": r.name, "family": r.family} for r in RULES]
                     + [{"id": "model_edge", "name": "Probabilità del modello sopra il tasso storico", "family": "predittiva"}],
            "opportunity_types": OPPORTUNITY_TYPES}


@app.get("/api/settings")
def get_settings():
    s = pl.settings()
    return s


@app.post("/api/settings")
def post_settings(body: dict = Body(...)):
    pl.save_settings(body)
    return get_settings()


@app.post("/api/instruments/custom")
def add_custom(body: dict = Body(...)):
    sym = (body.get("symbol") or "").strip()
    src = body.get("source", "binance")
    if not sym or src not in ("binance",):
        raise HTTPException(400, "simbolo o fonte non validi")
    iid = re.sub(r"[^A-Z0-9_]", "_", (body.get("id") or sym).upper())[:20]
    s = pl.settings()
    if any(i["id"] == iid for i in pl.instruments(True)):
        raise HTTPException(400, "identificativo già presente")
    s["custom_instruments"].append({"id": iid, "name": body.get("name") or sym, "market": body.get("market", "crypto"),
                                    "source": src, "symbol": sym, "custom": True})
    pl.save_settings({"custom_instruments": s["custom_instruments"]})
    return {"ok": True, "id": iid}


@app.delete("/api/instruments/custom/{iid}")
def del_custom(iid: str):
    s = pl.settings()
    pl.save_settings({"custom_instruments": [i for i in s["custom_instruments"] if i["id"] != iid]})
    return {"ok": True}


@app.get("/api/overview")
def overview():
    rows = []
    for inst in pl.instruments():
        a = storage.kv_get(f"analysis:{inst['id']}")
        info = storage.fetch_info(inst["id"])
        if not a:
            rows.append({"id": inst["id"], "name": inst["name"], "market": inst["market"], "status": "in attesa di analisi",
                         "fetch": info})
            continue
        m = a["meta"]
        rows.append({"id": inst["id"], "name": m["name"], "market": m["market"], "last": m.get("last_close"),
                     "chg": m.get("chg"), "level": m.get("level_series"), "freshness": m.get("freshness"),
                     "source": m.get("source"), "delay": m.get("delay"), "last_bar": m.get("last_bar"),
                     "vol_regime": (a.get("statistical") or {}).get("vol_regime"),
                     "n_signals": len(a.get("signals", [])), "error": a.get("error"),
                     "aha": {k: (a.get("aha") or {}).get(k) for k in ("action", "side", "entry", "stop_loss", "take_profit", "horizon_days")}
                            | {"grade": ((a.get("aha") or {}).get("reliability") or {}).get("grade"),
                               "expectancy_r": ((a.get("aha") or {}).get("reliability") or {}).get("expectancy_r")},
                     "lat_lon": None})
    return {"instruments": rows, "markets": pl.UNIVERSE["markets"], "global": storage.kv_get("global") or {},
            "alerts": storage.list_alerts(15), "status": pl.STATE}


@app.get("/api/scanner")
def scanner(kind: str | None = None, horizon: str | None = None, min_score: float = 0):
    s = pl.settings()
    rules_filter = set(OPPORTUNITY_TYPES.get(kind, [])) if kind else None
    out = []
    for inst in pl.instruments():
        a = storage.kv_get(f"analysis:{inst['id']}")
        if not a or not a.get("opportunity"):
            continue
        sigs = a["signals"]
        if rules_filter:
            sigs = [x for x in sigs if x["rule"] in rules_filter]
            if not sigs:
                continue
        o = a["opportunity"]
        if o["reliability"] < s["min_reliability"]:
            continue
        if o.get("risk_atr_pct") and o["risk_atr_pct"] > s["risk_max_atr_pct"]:
            continue
        if o["score"] < min_score:
            continue
        out.append({"id": inst["id"], "name": a["meta"]["name"], "market": a["meta"]["market"],
                    "price": a["meta"].get("last_close"), "last_bar": a["meta"]["last_bar"],
                    "freshness": a["meta"]["freshness"], **o, "signals": sigs})
    out.sort(key=lambda x: -x["score"])
    return {"results": out, "types": list(OPPORTUNITY_TYPES)}


RADAR_FIELDS = [("rsi14", "RSI 14"), ("z20", "Z-score 20g"), ("mom63", "Momentum 3m"), ("mom252", "Momentum 12m"),
                ("bb_width_pct", "Ampiezza Bollinger (pct)"), ("vol_ratio", "Vol 20g / 100g"), ("adx", "ADX"),
                ("vol_z", "Volume z"), ("cmf20", "CMF 20g")]


@app.get("/api/radar")
def radar():
    rows = []
    for inst in pl.instruments():
        a = storage.kv_get(f"analysis:{inst['id']}")
        if not a or "_features_last" not in a:
            continue
        f = a["_features_last"]
        cx = a.get("complexity", {})
        row = {"id": inst["id"], "name": a["meta"]["name"], "market": a["meta"]["market"],
               "values": {k: f.get(k) for k, _ in RADAR_FIELDS}}
        row["values"]["hurst"] = (cx.get("hurst_dfa") or {}).get("value")
        row["values"]["vol_pct"] = ((a.get("statistical") or {}).get("vol_regime") or {}).get("percentile")
        rows.append(row)
    fields = RADAR_FIELDS + [("hurst", "Hurst DFA"), ("vol_pct", "Percentile volatilità")]
    return {"fields": [{"key": k, "label": l} for k, l in fields], "rows": rows}


@app.get("/api/asset/{iid}")
def asset(iid: str):
    a = _analysis(iid)
    if not a:
        inst = pl.instrument(iid)
        if not inst:
            raise HTTPException(404, "strumento sconosciuto")
        return {"meta": {"id": iid, "name": inst["name"], "fetch": storage.fetch_info(iid)}, "pending": True}
    g = storage.kv_get("global") or {}
    a["centrality"] = ((g.get("network") or {}).get("centrality") or {}).get(a["meta"]["name"])
    a["spillover"] = (g.get("spillover") or {}).get(a["meta"]["name"])
    a["prediction_log"] = storage.predictions(iid)[:60]
    if isinstance(a.get("aha"), dict):
        a["aha"] = _public(a["aha"])
    return a


@app.get("/api/alerts")
def alerts():
    return {"alerts": storage.list_alerts(300)}


@app.post("/api/alerts/{aid}/read")
def alert_read(aid: int):
    storage.mark_alert_read(aid)
    return {"ok": True}


@app.get("/api/backtest/{iid}")
def backtest(iid: str, rule: str = "momentum_strong", horizon: int | None = None):
    if rule not in RULES_BY_ID:
        raise HTTPException(400, "regola non valida")
    df = storage.load_bars(iid, "1d")
    if len(df) < 300:
        raise HTTPException(400, "storia insufficiente per il backtest")
    inst = pl.instrument(iid) or {}
    if inst.get("source") == "binance":
        df = df.iloc[:-1]
    s = pl.settings()
    feat = ind.feature_frame(df)
    return pl.clean(backtest_rule(df, feat, rule, s["cost_bps"], s["slippage_bps"], horizon))


@app.get("/api/backtest_all/{rule}")
def backtest_all(rule: str):
    s = pl.settings(); out = []
    for inst in pl.instruments():
        df = storage.load_bars(inst["id"], "1d")
        if len(df) < 300 or inst.get("frequency") == "monthly":
            continue
        if inst["source"] == "binance":
            df = df.iloc[:-1]
        r = backtest_rule(df, ind.feature_frame(df), rule, s["cost_bps"], s["slippage_bps"])
        out.append({"id": inst["id"], "name": inst["name"], **{k: r[k] for k in ("all", "first_half", "second_half", "unconditional_mean_pct")},
                    "edge": r.get("edge_vs_unconditional_pct"), "t_stat": r.get("t_stat")})
    return pl.clean({"rule": rule, "results": out})


@app.get("/api/segnali")
def segnali():
    out = []
    for inst in pl.instruments():
        a = storage.kv_get(f"analysis:{inst['id']}")
        if not a or not a.get("simple") or not a["simple"].get("plans"):
            continue
        sp = a["simple"]
        for p in sp["plans"]:
            out.append({"id": inst["id"], "name": a["meta"]["name"], "market": a["meta"]["market"],
                        "freshness": a["meta"]["freshness"], "source": a["meta"]["source"], "asof": sp["asof"],
                        "conflict": sp.get("conflict", False), **{k: v for k, v in p.items() if k != "name"}, "signal_name": p["name"]})
    order = {"verde": 0, "giallo": 1, "grigio": 2, "rosso": 3}
    out.sort(key=lambda x: (order.get(x["quality"], 9), x["type"] != "diretto", -x["intensity"]))
    # diagnostica: quante analisi sono già alla versione attuale e quanti strumenti hanno segnali
    insts = pl.instruments(); n_done = n_old = n_none = n_nosig = n_untradable = 0
    for inst in insts:
        a = storage.kv_get(f"analysis:{inst['id']}")
        if not a:
            n_none += 1
        elif "simple" not in a or not str(a.get("_key", "")).startswith(pl.ENGINE_VERSION + "|"):
            n_old += 1
        else:
            n_done += 1
            if not a["simple"].get("tradable", True):
                n_untradable += 1
            elif not a["simple"].get("plans"):
                n_nosig += 1
    return pl.clean({"signals": out, "version": pl.ENGINE_VERSION, "status": pl.STATE,
                     "diag": {"instruments": len(insts), "analyzed_current": n_done, "analyzed_old": n_old,
                              "not_analyzed": n_none, "no_signal": n_nosig, "not_tradable": n_untradable}})


@app.get("/api/calibration")
def calibration():
    return pl.clean(pl.live_calibration())


@app.middleware("http")
async def _cache_headers(request, call_next):
    resp = await call_next(request)
    path = request.url.path
    if path.startswith("/static/vendor/") or path.startswith("/static/icons/"):
        # librerie e icone non cambiano tra una versione e l'altra: il browser le tiene un mese
        resp.headers["Cache-Control"] = "public, max-age=2592000"
    elif path in ("/", "/classic", "/sw.js", "/manifest.webmanifest") or path.startswith("/static/") or path.startswith("/api/"):
        # interfaccia e dati: il browser chiede sempre al server (evita di restare su una versione vecchia)
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/")
def index():
    html = (STATIC / "aha.html").read_text(encoding="utf-8").replace("{{V}}", pl.ENGINE_VERSION)
    return HTMLResponse(html)


@app.get("/classic")
def classic():
    """Interfaccia ereditata da GFIP (schede ancora in parte da adattare al solo mondo crypto)."""
    html = (STATIC / "index.html").read_text(encoding="utf-8").replace("{{V}}", pl.ENGINE_VERSION)
    return HTMLResponse(html)


# ---------------------------------------------------------------- app installabile (PWA)
@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    # servito dalla radice perché possa gestire tutto il sito; la versione nel file forza
    # l'aggiornamento della cache dell'app quando cambia la versione del programma
    js = (STATIC / "sw.js").read_text(encoding="utf-8").replace("{{V}}", pl.ENGINE_VERSION)
    return HTMLResponse(js, media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})


@app.get("/api/connect")
def connect_info(request: Request):
    """Indirizzi con cui raggiungere il server dal telefono sulla stessa rete Wi-Fi."""
    import socket
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))   # nessun pacchetto inviato: serve solo a scegliere l'interfaccia
        ips.add(s.getsockname()[0]); s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    ips = sorted(i for i in ips if not i.startswith("127."))
    port = request.url.port or 8000
    return {"lan_urls": [f"http://{i}:{port}" for i in ips], "hostname": socket.gethostname(),
            "secure": request.url.scheme == "https" or request.url.hostname in ("localhost", "127.0.0.1")}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ------------------------------------------------------------------ segnali AHA
_PARAM_TYPES = {k: type(v) for k, v in signals.DEFAULTS.items()}


def _aha_inputs(iid: str):
    inst = pl.instrument(iid)
    if not inst:
        raise HTTPException(404, "strumento sconosciuto")
    df = storage.load_bars(iid, "1d")
    if len(df) < 2:
        raise HTTPException(404, "dati non ancora scaricati")
    df = df.iloc[:-1]                      # la barra in formazione non entra nei segnali
    oc = {c: storage.load_series(f"onchain:{iid}:{c}") for c in pl.ONCHAIN_COLS}
    return df, {k: v for k, v in oc.items() if len(v)}, storage.load_series("fear_greed:global")


_POSITIVE = {"ema_fast", "ema_slow", "rsi_n", "macd_f", "macd_s", "macd_sig", "bb_n", "bb_k", "horizon", "atr_stop", "atr_tp", "min_votes", "min_rr",
             "near_atr", "flow_z", "min_strength"}
_FLAGS = {"exit_sr", "trail", "regime", "near_level"}
_NONNEG = {"stop_buf", "tp_buf", "cost_bps", "slippage_bps", "stop_slip_bps", "w_trend", "w_mr", "w_chain"}


def _public(sig: dict) -> dict:
    """Toglie le chiavi interne (_trades, _outcomes) dal segnale e dal blocco della regola semplice."""
    out = {k: v for k, v in sig.items() if not k.startswith("_")}
    if isinstance(out.get("simple"), dict):
        out["simple"] = {k: v for k, v in out["simple"].items() if not k.startswith("_")}
    return out


def _attach_both(sig: dict, default_params: bool) -> dict:
    validation.attach(sig, storage.kv_get("aha_validation"), default_params=default_params)
    if isinstance(sig.get("simple"), dict):
        validation.attach(sig["simple"], storage.kv_get("aha_validation_simple"), default_params=default_params)
    return sig


def _overrides(request: Request, skip=("days",)) -> dict:
    import math
    over = {}
    for k, v in request.query_params.items():
        if k in skip:
            continue
        if k not in signals.DEFAULTS:
            raise HTTPException(400, f"parametro sconosciuto: {k}")
        try:
            x = float(v)
        except ValueError:
            raise HTTPException(400, f"valore non numerico per {k}")
        if not math.isfinite(x):
            raise HTTPException(400, f"{k}: valore non finito")
        if (k in _POSITIVE and x <= 0) or (k in _NONNEG and x < 0) or (k in _FLAGS and x not in (0, 1)):
            raise HTTPException(400, f"{k}: valore fuori intervallo")
        if k == "horizon" and not (1 <= x <= 250):
            raise HTTPException(400, "horizon: tra 1 e 250 giorni")
        if k in ("ema_fast", "ema_slow", "rsi_n", "macd_f", "macd_s", "macd_sig", "bb_n") and x > 500:
            raise HTTPException(400, f"{k}: massimo 500")
        over[k] = int(x) if _PARAM_TYPES[k] is int else x
    return over


@app.get("/api/aha/signal/{iid}")
def aha_signal(iid: str, request: Request):
    """Segnale ricalcolato al volo. I parametri in query (es. ?ema_fast=12&ema_slow=26&horizon=21&atr_stop=1.5)
    sostituiscono la ricerca automatica: servono ai grafici interattivi."""
    df, oc, fg = _aha_inputs(iid)
    over = _overrides(request)
    sig = _attach_both(signals.build_signal(df, oc, fg, overrides=over), default_params=not over)
    return pl.clean(_public(sig))


@app.get("/api/aha/chart/{iid}")
def aha_chart(iid: str, request: Request, days: int = 365):
    """Tutte le serie per i grafici, calcolate con i parametri scelti (automatici o modificati in query)
    insieme al segnale e alla sua prova storica."""
    days = max(60, min(days, 1500))
    df, oc, fg = _aha_inputs(iid)
    over = _overrides(request)
    sig = _public(_attach_both(signals.build_signal(df, oc, fg, overrides=over), default_params=not over))
    if "params" not in sig:
        raise HTTPException(422, sig.get("why", "dati insufficienti"))
    p = sig["params"]
    c = df["close"]
    ema_f, ema_s = ind.ema(c, int(p["ema_fast"])), ind.ema(c, int(p["ema_slow"]))
    _m, bb_up, bb_lo, _w = ind.bollinger(c, int(p["bb_n"]), float(p["bb_k"]))
    macd, macd_sig, macd_h = ind.macd(c, int(p["macd_f"]), int(p["macd_s"]), int(p["macd_sig"]))
    rsi = ind.rsi(c, int(p["rsi_n"]))
    tail = df.index[-days:]

    def take(s):
        return None if s is None else [None if not np.isfinite(x) else round(float(x), 6) for x in s.reindex(tail).values]

    ex = signals._align(oc.get("ex_netflow_usd"), df.index, lag=0)
    series = {
        "t": [str(x.date()) for x in tail],
        "o": take(df["open"]), "h": take(df["high"]), "l": take(df["low"]), "c": take(c),
        "ema_fast": take(ema_f), "ema_slow": take(ema_s), "bb_up": take(bb_up), "bb_lo": take(bb_lo),
        "rsi": take(rsi), "macd": take(macd), "macd_signal": take(macd_sig), "macd_hist": take(macd_h),
        "mvrv": take(signals._align(oc.get("mvrv"), df.index, lag=0)),
        "netflow_7d": take(ex.rolling(7, min_periods=5).sum()) if ex is not None else None,
        "sopr": take(signals._align(oc.get("sopr"), df.index, lag=0)),
        "nvt": take(signals._align(oc.get("nvt"), df.index, lag=0)),
        "fear_greed": take(signals._align(fg, df.index, lag=0)) if len(fg) else None,
    }
    series = {k: v for k, v in series.items() if v is not None and any(x is not None for x in v)}
    series["t"] = [str(x.date()) for x in tail]
    return pl.clean({"series": series, "signal": sig})


@app.get("/api/aha/signals")
def aha_signals(only_active: bool = False):
    """Elenco dei segnali correnti (dall'ultima analisi), con affidabilità e percentuale storica fuori campione."""
    rows = []
    for inst in pl.instruments():
        a = storage.kv_get(f"analysis:{inst['id']}") or {}
        s = a.get("aha")
        if not s or (only_active and not s.get("direction")):
            continue
        rows.append({"id": inst["id"], "name": inst["name"], **{k: s.get(k) for k in (
            "action", "side", "entry", "stop_loss", "take_profit", "basis_stop", "basis_tp", "horizon_days", "risk_reward",
            "score", "asof", "positioning", "skipped_reason")},
            "reliability": s.get("reliability"), "reliability_by_side": s.get("reliability_by_side"),
            "onchain_used": s.get("onchain_used"),
            "simple": {k: (s.get("simple") or {}).get(k) for k in ("action", "side", "entry", "stop_loss", "basis_stop", "reliability", "reliability_by_side")}})
    order = {"alta": 0, "media": 1, "bassa": 2}
    rows.sort(key=lambda r: (not r["side"], order.get((r["reliability"] or {}).get("grade"), 3)))
    return {"signals": rows}


# ------------------------------------------------------------------ esportazione per CryptoRadar
@app.get("/api/aha/export")
def aha_export(only_active: bool = False):
    """Segnali nel formato letto da CryptoRadar: { generatedAt, items: Signal[] } con nomi camelCase, date ISO,
    id delle monete come CoinGecko e prezzi in USD. `confidence` è il punteggio di affidabilità fuori campione (0-1);
    l'aspettativa del pool in R è in `expectancyR`."""
    items = []
    for inst in pl.instruments():
        a = storage.kv_get(f"analysis:{inst['id']}") or {}
        base = a.get("aha") or {}
        for strat, s, label in (("aha", base, "AHA"), ("simple", base.get("simple") or {}, "Regola semplice")):
            if not s or not s.get("price"):
                continue
            side = s.get("side")
            if only_active and not side:
                continue
            rel = s.get("reliability") or {}
            score = {"alta": 80, "media": 55}.get(rel.get("grade"))       # per "bassa" o "non valutabile" non si dichiara nessuna fiducia
            items.append({
                "id": f"aha-{inst['id'].lower()}-{s['asof']}" if strat == "aha" else f"aha-{strat}-{inst['id'].lower()}-{s['asof']}",
                "title": f"{inst['id']}: {s['action']}" if strat == "aha" else f"{inst['id']} · {label}: {s['action']}",
                "strategy": strat, "excerpt": rel.get("why"), "url": None,
                "publishedAt": (a.get("meta") or {}).get("analyzed_at") or s["asof"],
                "coins": [inst.get("cg") or inst["id"].lower()], "symbol": inst["id"],
                "side": side or "neutral", "price": s["price"],
                "targetPrice": s.get("take_profit"), "stopPrice": s.get("stop_loss"),
                "confidence": None if score is None else score / 100,
                "expectancyR": rel.get("expectancy_r"), "nPool": rel.get("n"), "reliability": rel.get("grade"),
                "basisStop": s.get("basis_stop"), "basisTarget": s.get("basis_tp"), "riskReward": s.get("risk_reward"),
                "horizonDays": s.get("horizon_days"),
                "flags": sorted({"on-chain" if k in ("mvrv", "exchange_flow", "sopr", "nvt") else "sentiment" for k in base.get("onchain_used", [])}) if strat == "aha" else ["trend"],
                "lang": "it", "status": "attivo" if side else "in attesa"})
    return {"generatedAt": storage.utcnow(), "items": items}


# ------------------------------------------------------------------ esecuzione (solo anteprima e simulazione)
_PAPER = execution.PaperExecutor()


def _exec_cfg() -> execution.ExecutionConfig:
    saved = storage.kv_get("exec_config") or {}
    cfg = execution.ExecutionConfig()
    for k, v in saved.items():
        if hasattr(cfg, k):
            setattr(cfg, k, tuple(v) if k == "allowed_grades" else v)
    return cfg


@app.get("/api/aha/execution/config")
def exec_get():
    from dataclasses import asdict
    return {**asdict(_exec_cfg()), "note": "Solo simulazione (paper): nessun ordine viene inviato a un exchange. Spento di default."}


@app.post("/api/aha/execution/config")
def exec_set(body: dict = Body(...)):
    cur = _exec_cfg(); from dataclasses import asdict
    new = {}
    for k, v in body.items():
        if not hasattr(cur, k):
            raise HTTPException(400, f"impostazione sconosciuta: {k}")
        if k == "mode" and v != "paper":
            raise HTTPException(400, "è implementata solo la modalità paper")
        if k == "allowed_grades" and not (isinstance(v, list) and all(x in ("alta", "media", "bassa") for x in v)):
            raise HTTPException(400, "allowed_grades: lista di alta/media/bassa")
        if k in ("risk_pct", "equity", "max_notional_pct") and not (isinstance(v, (int, float)) and v > 0):
            raise HTTPException(400, f"{k}: numero positivo")
        new[k] = v
    storage.kv_set("exec_config", {**asdict(cur), **new, "mode": "paper"})
    return exec_get()


def _plan(iid: str):
    a = storage.kv_get(f"analysis:{iid}")
    inst = pl.instrument(iid)
    if not inst or not a or not a.get("aha"):
        raise HTTPException(404, "analisi non disponibile")
    return inst, execution.plan_order(a["aha"], inst["symbol"], _exec_cfg(), open_positions=len(_PAPER.positions()),
                                      now_asof=str(__import__("pandas").Timestamp.now("UTC").date()))


@app.get("/api/aha/execution/preview/{iid}")
def exec_preview(iid: str):
    """Mostra l'ordine che verrebbe generato e i controlli di sicurezza: NON invia nulla."""
    from dataclasses import asdict
    inst, plan = _plan(iid)
    return pl.clean({"ok": plan["ok"], "checks": plan["checks"], "order": asdict(plan["order"]) if plan["order"] else None})


@app.post("/api/aha/execution/paper/{iid}")
def exec_paper(iid: str):
    inst, plan = _plan(iid)
    if not plan["ok"]:
        raise HTTPException(409, "controlli di sicurezza non superati: " + "; ".join(c["check"] for c in plan["checks"] if not c["ok"]))
    return pl.clean(_PAPER.place(plan["order"]))


@app.get("/api/aha/execution/positions")
def exec_positions():
    return pl.clean({"mode": "paper", "positions": _PAPER.positions(), "closed": _PAPER.closed})


@app.get("/api/aha/track")
def aha_track():
    """Registro in avanti dei segnali (paper): voci aperte e chiuse con l'esito calcolato con le stesse regole del backtest."""
    log = tracking.load()
    ch = tracking.chance(log, lambda c: storage.load_bars(c, "1d").iloc[:-1]) if log else {}
    return pl.clean({"by_strategy": tracking.summary_by_strategy(log), "chance": ch, "entries": log[-100:][::-1],
                     "note": "Verifica in avanti: un trade alla volta per moneta, registrato prima di conoscerne l'esito. "
                             "Servono almeno 30 operazioni chiuse per trarre conclusioni; confrontare sempre con il caso. Le voci aperte nello stesso giorno su monete diverse "
                             "sono correlate (un solo mercato): conta il numero di giorni distinti, non di voci."})


# ------------------------------------------------------------------ backtest su tutta la storia
_BT_PARAMS = ("strategy", "start", "end", "side", "extended", "funding", "tune", "include_reserved")


def _cached_series(iid: str, key: str, hours: float, fetch):
    """Serie in cache (SQLite). Si riscarica se più vecchia di `hours`; dopo un fallimento si riprova non prima di un'ora
    (la serie precedente, se c'è, resta valida) e un'eccezione del download non diventa un errore 500."""
    import pandas as pd
    info = storage.fetch_info(iid, key)
    ser = storage.load_series(f"{key}:{iid}")
    if info:
        age_h = (pd.Timestamp.now("UTC") - pd.Timestamp(info["fetched_at"])).total_seconds() / 3600
        if (info["status"] == "ok" and len(ser) and age_h < hours) or (info["status"] != "ok" and age_h < 1):
            return ser if len(ser) else None
    try:
        res = fetch()
    except Exception as e:
        storage.log_fetch(iid, key, "-", "errore", "-", str(e)[:200])
        return ser if len(ser) else None
    if res is not None and len(res):
        storage.save_series(f"{key}:{iid}", res)
        storage.log_fetch(iid, key, "-", "ok", "-", "")
        return res
    storage.log_fetch(iid, key, "-", "errore", "-", "serie non disponibile")
    return ser if len(ser) else None


_BT_CACHE: dict = {}


@app.get("/api/aha/backtest/{iid}")
def aha_backtest(iid: str, request: Request):
    """Backtest su tutta la storia disponibile. Query: strategy=aha|simple, start/end=YYYY-MM-DD, side=all|long|short,
    extended=1 (chiusure dalla nascita della moneta, CoinMetrics), funding=1 (funding vero dei perpetual),
    tune=1 (parametri scelti sul primo 60% della finestra), include_reserved=1 (include il periodo riservato dal 2026-09-10),
    più gli stessi parametri della strategia di /api/aha/signal."""
    import re
    q = request.query_params
    strategy, side = q.get("strategy", "aha"), q.get("side", "all")
    if strategy not in ("aha", "simple") or side not in ("all", "long", "short"):
        raise HTTPException(400, "strategy: aha|simple; side: all|long|short")
    import datetime as _dt
    for k in ("start", "end"):
        if q.get(k):
            try:
                _dt.datetime.strptime(q[k], "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, f"{k}: data non valida (formato YYYY-MM-DD)")
    flag = lambda k, d="0": q.get(k, d) in ("1", "true")
    inst = pl.instrument(iid)
    if not inst:
        raise HTTPException(404, "strumento sconosciuto")
    over = _overrides(request, skip=_BT_PARAMS)
    df, oc, fg = _aha_inputs(iid)
    import pandas as pd
    from .data import funding as fmod, history as hmod
    if flag("extended"):
        cm = _cached_series(iid, "price_cm", 24, lambda: (lambda r: r.df["close"] if r.status == "ok" else None)(hmod.coinmetrics_close(inst.get("cm") or iid.lower())))
        df = hmod.extend_bars(df, cm)
    first_real = str((df.index[~df["synthetic"].values][0] if "synthetic" in df else df.index[0]).tz_localize(None).to_period("M"))
    fund = _cached_series(iid, "funding", 24 * 7, lambda: fmod.daily_funding(inst["symbol"], start=max("2019-09", first_real))) if flag("funding") else None
    key = (iid, str(df.index[-1]), str(sorted(q.items())))
    res = _BT_CACHE.get(key)
    if res is None:
        res = backtester.run(df, oc, fg, strategy=strategy, overrides=over, start=q.get("start"), end=q.get("end"), side=side,
                             tune_params=flag("tune", "1"), funding=fund, include_reserved=flag("include_reserved"))
        if not res.get("error"):
            if len(_BT_CACHE) >= 32:
                _BT_CACHE.pop(next(iter(_BT_CACHE)))
            _BT_CACHE[key] = res
    if res.get("error"):
        raise HTTPException(422, res["error"])
    res = dict(res)
    res["extended_requested"] = flag("extended")
    res["extended_available"] = bool("synthetic" in df and df["synthetic"].any())
    return pl.clean(res)
