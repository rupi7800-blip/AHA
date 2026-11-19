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
from . import storage
from .analysis import indicators as ind
from .backtest import backtest_rule
from .scanner import OPPORTUNITY_TYPES, RULES, RULES_BY_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
STATIC = Path(__file__).resolve().parent.parent / "static"
@asynccontextmanager
async def _lifespan(app):
    storage.init()
    if not os.environ.get("AHA_NO_SCHEDULER"):
        pl.start_scheduler()
    yield


app = FastAPI(title="Global Financial Intelligence Platform", version=pl.ENGINE_VERSION, lifespan=_lifespan)
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
    elif path in ("/", "/sw.js", "/manifest.webmanifest") or path.startswith("/static/") or path.startswith("/api/"):
        # interfaccia e dati: il browser chiede sempre al server (evita di restare su una versione vecchia)
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/")
def index():
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
