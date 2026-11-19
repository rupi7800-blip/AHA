"""Esporta un'ISTANTANEA dei dati di AHA in file JSON ({rows, meta}) per una pagina di consultazione statica.

Uso:  AHA_DB=/percorso/db.sqlite python tools/export_snapshot.py <cartella_di_uscita>
Legge dal database (analisi, validazione, registro in avanti) e dalle API di AHA (grafici, backtest su storia estesa con funding),
più i risultati in research/. Nessun valore è inventato: ogni riga viene dal calcolo del motore."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient  # noqa: E402

from app import pipeline as pl, storage  # noqa: E402
from app.main import app  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "snapshot")
ROOT = Path(__file__).resolve().parent.parent


def rnd(x, d=4):
    return None if x is None else round(float(x), d)


def write(name, rows, meta):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps({"rows": rows, "meta": meta}, ensure_ascii=False, separators=(",", ":")))
    print(name, len(rows), "righe", file=sys.stderr)


def main():
    storage.init(storage.DB_PATH)
    c = TestClient(app)
    asof = storage.kv_get("last_run") or ""
    base_meta = [{"label": "Script", "value": "tools/export_snapshot.py (repository AHA)"},
                 {"label": "Ultimo aggiornamento dei dati", "value": str(asof)[:19].replace("T", " ") + " UTC"},
                 {"label": "Nota", "value": "Istantanea: i valori non si ricalcolano nella pagina"}]
    ids = [i["id"] for i in pl.UNIVERSE["instruments"]]
    names = {i["id"]: i["name"] for i in pl.UNIVERSE["instruments"]}
    coins, series, levels, bt, curves, yearly = [], [], [], [], [], []
    for cid in ids:
        d = c.get(f"/api/aha/chart/{cid}?days=365").json()
        s, sig = d["series"], d["signal"]
        row = {"coin": cid, "name": names[cid], "price": rnd(sig["price"], 6), "asof": sig["asof"]}
        for key, blk in (("aha", sig), ("simple", sig["simple"])):
            rel = blk.get("reliability") or {}
            row.update({f"{key}_action": blk.get("action"), f"{key}_side": blk.get("side") or "-", f"{key}_entry": rnd(blk.get("entry"), 6),
                        f"{key}_stop": rnd(blk.get("stop_loss"), 6), f"{key}_basis_stop": blk.get("basis_stop"),
                        f"{key}_level": rnd(blk.get("take_profit"), 6), f"{key}_basis_level": blk.get("basis_tp"),
                        f"{key}_risk_pct": rnd(blk.get("risk_pct"), 2), f"{key}_grade": rel.get("grade"),
                        f"{key}_why": rel.get("why"), f"{key}_votes": ", ".join(f"{v['indicator']}{'+' if v['vote'] > 0 else '-' if v['vote'] < 0 else '0'}" for v in sig["votes"]) if key == "aha" else None})
        row["mvrv_lo"], row["mvrv_hi"] = sig["params"]["mvrv_lo"], sig["params"]["mvrv_hi"]
        coins.append(row)
        for k, t in enumerate(s["t"]):
            series.append({"coin": cid, "t": t, "c": rnd(s["c"][k], 6), "ema_fast": rnd(s.get("ema_fast", [None] * 999)[k], 6),
                           "ema_slow": rnd(s.get("ema_slow", [None] * 999)[k], 6), "bb_up": rnd(s.get("bb_up", [None] * 999)[k], 6),
                           "bb_lo": rnd(s.get("bb_lo", [None] * 999)[k], 6), "macd_hist": rnd(s.get("macd_hist", [None] * 999)[k], 6),
                           "mvrv": rnd(s["mvrv"][k], 4) if "mvrv" in s else None, "netflow_7d": rnd(s["netflow_7d"][k], 0) if "netflow_7d" in s else None})
        for z in sig.get("levels", []):
            levels.append({"coin": cid, "price": rnd(z["price"], 6), "touches": z["touches"], "strength": z["strength"]})
        for strat in ("aha", "simple"):
            r = c.get(f"/api/aha/backtest/{cid}?strategy={strat}&extended=1&funding=1&tune=1").json()
            if "portfolio" not in r:
                print("backtest assente", cid, strat, r, file=sys.stderr); continue
            p, st, w = r["portfolio"], r["stats"]["all"], r["window"]
            bt.append({"coin": cid, "strategy": strat, "start": w["start"], "end": w["end"], "days": w["days"], "first_real_bar": w["first_real_bar"],
                       "synthetic_days": w["synthetic_bars_in_window"], "n_trades": r["n_trades"], "win_rate": rnd(st.get("win_rate")),
                       "payoff": rnd(st.get("payoff"), 2), "expectancy_pct": rnd(st.get("expectancy_pct"), 2), "expectancy_r": rnd(st.get("expectancy_r"), 2),
                       "profit_factor": rnd(st.get("profit_factor"), 2), "strat_total_pct": rnd(p["strategy"].get("total_pct"), 1),
                       "strat_cagr_pct": rnd(p["strategy"].get("cagr_pct"), 1), "strat_vol_pct": rnd(p["strategy"].get("vol_pct"), 1),
                       "strat_sharpe": rnd(p["strategy"].get("sharpe"), 2), "strat_maxdd_pct": rnd(p["strategy"].get("max_dd_pct"), 1),
                       "bh_total_pct": rnd(p["buy_hold"].get("total_pct"), 1), "bh_cagr_pct": rnd(p["buy_hold"].get("cagr_pct"), 1),
                       "bh_vol_pct": rnd(p["buy_hold"].get("vol_pct"), 1), "bh_sharpe": rnd(p["buy_hold"].get("sharpe"), 2),
                       "bh_maxdd_pct": rnd(p["buy_hold"].get("max_dd_pct"), 1), "exposure_pct": rnd(p["exposure_pct"], 1),
                       "funding_from": (r.get("funding_coverage") or {}).get("from")})
            cv = r["curves"]; step = max(1, len(cv["t"]) // 220)
            for k in sorted(set(range(0, len(cv["t"]), step)) | {len(cv["t"]) - 1}):
                curves.append({"coin": cid, "strategy": strat, "t": cv["t"][k], "strat": rnd(100 + cv["strategy"][k], 3), "bh": rnd(100 + cv["buy_hold"][k], 3)})
            for y in r["yearly"]:
                yearly.append({"coin": cid, "strategy": strat, "year": int(y["year"]), "n": y["n"], "win_rate": rnd(y["win_rate"], 3),
                               "expectancy_pct": rnd(y["expectancy_pct"], 2), "expectancy_r": rnd(y["expectancy_r"], 2), "sum_pct": rnd(y["sum_pct"], 1)})
        print("fatto", cid, file=sys.stderr)
    write("coins", coins, base_meta)
    write("series", series, base_meta + [{"label": "Finestra", "value": "ultimi 365 giorni di chiusure Binance; indicatori calcolati con i parametri scelti dal motore"}])
    write("levels", levels, base_meta + [{"label": "Livelli", "value": "supporti e resistenze importanti (>= 2 tocchi), app/levels.py"}])
    write("bt", bt, base_meta + [{"label": "Backtest", "value": "storico esteso dalla nascita (chiusure CoinMetrics prima della quotazione), funding incluso, capitale 100% investito, senza leva, periodo riservato escluso"}])
    write("curves", curves, base_meta + [{"label": "Curve", "value": "capitale con base 100, curve alleggerite a circa 220 punti"}])
    write("yearly", yearly, base_meta)
    pool = []
    for key, kv in (("aha", "aha_validation"), ("simple", "aha_validation_simple")):
        v = storage.kv_get(kv) or {}
        for side in ("long", "short"):
            o = (v.get("sides") or {}).get(side)
            if not o:
                continue
            rb = o.get("robust") or {}
            pool.append({"strategy": key, "side": side, "n": o["n"], "months": o.get("months"), "coins": o.get("coins"), "expectancy_r": rnd(o["expectancy_r"], 3),
                         "expectancy_pct": rnd(o["expectancy_pct"], 2), "win_rate": rnd(o["win_rate"], 3), "payoff": rnd(o.get("payoff"), 2),
                         "profit_factor": rnd(o.get("profit_factor"), 2), "chance_r": rnd(o.get("chance_mean_r"), 3), "p_vs_chance": rnd(o.get("p_vs_chance"), 3),
                         "ci_low": rnd(o["ci95_r"][0], 2), "ci_high": rnd(o["ci95_r"][1], 2), "double_costs_r": rnd(rb.get("double_costs_r"), 2),
                         "without_top5_r": rnd(rb.get("without_top5_r"), 2), "grade": o.get("grade")})
    write("pool", pool, base_meta + [{"label": "Pool", "value": "20 monete, solo periodo fuori campione, costi realistici, caso = calendario spostato con le stesse uscite (app/validation.py)"}])
    lab = []
    for rnd_name, f in (("1", "lab_results.json"), ("2", "lab_results_round2.json")):
        d = json.loads((ROOT / "research" / f).read_text())
        for n, r in d["results"].items():
            for side in ("long", "short"):
                o = r["validation"]["sides"][side]; i = r["in_sample"][side]
                lab.append({"round": rnd_name, "variant": n, "side": side, "in_n": i["n"], "in_r": rnd(i["expectancy_r"], 2), "oos_n": o["n"],
                            "oos_r": rnd(o.get("expectancy_r"), 2), "chance_r": rnd(o.get("chance_mean_r"), 2), "p": rnd(o.get("p_vs_chance"), 3),
                            "p_holm": rnd(d["holm"].get(f"{n}|{side}"), 2), "without_top5_r": rnd((o.get("robust") or {}).get("without_top5_r"), 2), "grade": o.get("grade")})
    write("lab", lab, base_meta + [{"label": "Laboratorio", "value": "13 varianti in 2 giri, research/strategy_lab.py; scelta solo in campione"}])
    bj = json.loads((ROOT / "research" / "benchmark.json").read_text())
    names_b = {"bh": "Buy & hold", "long_spot": "Strategia AHA, solo long (spot)", "ls_nofund": "Strategia AHA long+short, funding ignorato",
               "ls_fund": "Strategia AHA long+short, con funding", "simple_long_spot": "Regola semplice, solo long (spot)", "simple_ls_fund": "Regola semplice long+short, con funding"}
    bench = [{"name": names_b[k], "total_pct": rnd(v["total_pct"], 1), "cagr_pct": rnd(v["cagr_pct"], 1), "vol_pct": rnd(v["vol_pct"], 1), "sharpe": rnd(v["sharpe"], 2),
              "max_dd_pct": rnd(v["max_dd_pct"], 1)} for k, v in bj["portfolio"].items() if k in names_b]
    write("bench", bench, base_meta + [{"label": "Benchmark", "value": "20 monete equipesate, fuori campione, funding vero (research/benchmark.py)"}])
    log = storage.kv_get("aha_track") or []
    track = [{"asof": e["asof"], "coin": e["coin"], "strategy": e.get("strategy", "aha"), "side": e["side"], "status": e["status"],
              "r": rnd(e.get("r"), 2), "exit": e.get("exit")} for e in log]
    write("track", track, base_meta + [{"label": "Registro", "value": "segnali registrati prima di conoscerne l'esito (app/tracking.py)"}])


if __name__ == "__main__":
    main()
