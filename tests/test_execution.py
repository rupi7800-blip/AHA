"""Test di esecuzione simulata, controlli di sicurezza ed esportazione per CryptoRadar."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["AHA_NO_SCHEDULER"] = "1"
from app import execution as ex, storage  # noqa: E402


def sig(**kw):
    s = {"side": "long", "entry": 100.0, "stop_loss": 95.0, "take_profit": 110.0, "horizon_days": 14, "asof": "2026-11-18",
         "reliability": {"grade": "alta", "n": 60}}
    s.update(kw)
    return s


def cfg(**kw):
    return ex.ExecutionConfig(**{"enabled": True, **kw})


def test_disabled_by_default_blocks_everything():
    p = ex.plan_order(sig(), "BTCUSDT", ex.ExecutionConfig())
    assert not p["ok"] and p["order"] is None
    assert any(c["check"] == "interruttore generale" and not c["ok"] for c in p["checks"])


def test_position_size_from_risk():
    p = ex.plan_order(sig(), "BTCUSDT", cfg(equity=10_000, risk_pct=1, max_notional_pct=100))
    o = p["order"]
    assert p["ok"] and o.side == "buy" and o.qty == pytest.approx(20)          # 100 USD di rischio / 5 USD di stop
    assert o.risk_usd == pytest.approx(100)


def test_notional_cap_reduces_size():
    o = ex.plan_order(sig(), "BTCUSDT", cfg(equity=10_000, risk_pct=1, max_notional_pct=10))["order"]
    assert o.notional == pytest.approx(1000) and o.risk_usd < 100


def test_short_levels_and_side():
    p = ex.plan_order(sig(side="short", entry=100, stop_loss=105, take_profit=90), "BTCUSDT", cfg())
    assert p["ok"] and p["order"].side == "sell"
    bad = ex.plan_order(sig(side="short", entry=100, stop_loss=95, take_profit=90), "BTCUSDT", cfg())
    assert not bad["ok"] and any(c["check"] == "livelli coerenti" and not c["ok"] for c in bad["checks"])


@pytest.mark.parametrize("change,failed", [
    ({"reliability": {"grade": "media", "n": 60}}, "affidabilità"),
    ({"reliability": {"grade": "alta", "n": 10}}, "operazioni del pool fuori campione"),
    ({"side": None}, "segnale attivo"),
])
def test_safety_checks_block(change, failed):
    p = ex.plan_order(sig(**change), "BTCUSDT", cfg())
    assert not p["ok"] and any(c["check"] == failed and not c["ok"] for c in p["checks"])


def test_max_open_and_stale_signal():
    assert not ex.plan_order(sig(), "X", cfg(max_open=2), open_positions=2)["ok"]
    assert not ex.plan_order(sig(asof="2026-11-01"), "X", cfg(), now_asof="2026-11-18")["ok"]


def test_paper_executor_and_live_is_not_implemented():
    p = ex.PaperExecutor()
    o = ex.plan_order(sig(), "BTCUSDT", cfg())["order"]
    r = p.place(o)
    assert r["status"] == "simulato" and len(p.positions()) == 1
    assert p.close(o.id, 110)["pnl_usd"] == pytest.approx(o.qty * 10)
    with pytest.raises(NotImplementedError):
        ex.LiveExecutor().place(o)


def test_api_export_and_execution(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    storage.init(tmp_path / "e.sqlite")
    a = {"meta": {"analyzed_at": "2026-11-19T10:00:00+00:00"},
         "aha": sig(price=100.0, action="ACQUISTA (long)", onchain_used=["mvrv", "fear_greed"])}
    storage.kv_set("analysis:BTC", a)
    c = TestClient(app)
    items = c.get("/api/aha/export").json()["items"]
    assert len(items) == 1
    it = items[0]
    assert it["coins"] == ["bitcoin"] and it["side"] == "long" and it["targetPrice"] == 110 and it["stopPrice"] == 95
    assert it["confidence"] == pytest.approx(0.8) and it["reliability"] == "alta" and it["flags"] == ["on-chain", "sentiment"] and it["status"] == "attivo"
    # spento di default: l'anteprima non genera ordini e l'esecuzione simulata è rifiutata
    assert c.get("/api/aha/execution/preview/BTC").json()["order"] is None
    assert c.post("/api/aha/execution/paper/BTC").status_code == 409
    assert c.post("/api/aha/execution/config", json={"mode": "live"}).status_code == 400
    assert c.post("/api/aha/execution/config", json={"enabled": True, "max_signal_age_days": 100000}).status_code == 200
    assert c.get("/api/aha/execution/preview/BTC").json()["ok"] is True
    assert c.post("/api/aha/execution/paper/BTC").json()["status"] == "simulato"
    assert len(c.get("/api/aha/execution/positions").json()["positions"]) == 1
