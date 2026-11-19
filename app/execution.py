"""Esecuzione degli ordini: INTERFACCIA per il futuro collegamento a un exchange.

Oggi esiste solo l'esecutore simulato (`PaperExecutor`): non invia nulla a nessun exchange. Un esecutore reale
(`LiveExecutor`) non è implementato di proposito: richiederà chiavi API con permessi limitati (solo trading, mai
prelievi), conferma esplicita dell'utente e una fase di prova in simulazione.

Il passaggio chiave è `plan_order`: trasforma un segnale AHA in un ordine con dimensione calcolata dal rischio
(quota di capitale persa se scatta lo stop) e lo sottopone a controlli di sicurezza. Se un controllo fallisce
l'ordine NON viene generato e il motivo è riportato."""
from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field


@dataclass
class ExecutionConfig:
    enabled: bool = False                 # interruttore generale (kill switch): spento = nessun ordine
    mode: str = "paper"                   # solo "paper" è implementato
    equity: float = 10_000.0              # capitale di riferimento (USD)
    risk_pct: float = 1.0                 # % del capitale rischiata per operazione (distanza dallo stop)
    max_notional_pct: float = 25.0        # tetto al valore di una posizione, % del capitale
    max_open: int = 3                     # posizioni aperte contemporaneamente
    allowed_grades: tuple = ("alta",)     # affidabilità minima richiesta (di default solo "alta")
    min_oos_trades: int = 30              # operazioni storiche fuori campione minime
    max_signal_age_days: int = 2          # un segnale vecchio non si esegue


@dataclass
class Order:
    symbol: str                           # es. BTCUSDT
    side: str                             # "buy" | "sell"
    qty: float
    entry: float
    stop_loss: float
    take_profit: float
    horizon_days: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created: float = field(default_factory=time.time)
    meta: dict = field(default_factory=dict)

    @property
    def notional(self) -> float:
        return self.qty * self.entry

    @property
    def risk_usd(self) -> float:
        return self.qty * abs(self.entry - self.stop_loss)


class ExecutionAdapter(ABC):
    """Contratto che ogni esecutore deve rispettare."""
    name = "abstract"

    @abstractmethod
    def place(self, order: Order) -> dict: ...

    @abstractmethod
    def positions(self) -> list[dict]: ...

    @abstractmethod
    def close(self, order_id: str, price: float) -> dict: ...


class PaperExecutor(ExecutionAdapter):
    """Esecutore simulato: tiene le posizioni in memoria, non contatta nessun exchange."""
    name = "paper"

    def __init__(self):
        self._open: dict[str, Order] = {}
        self.closed: list[dict] = []

    def place(self, order: Order) -> dict:
        self._open[order.id] = order
        return {"id": order.id, "status": "simulato", "mode": "paper", "order": asdict(order)}

    def positions(self) -> list[dict]:
        return [asdict(o) for o in self._open.values()]

    def close(self, order_id: str, price: float) -> dict:
        o = self._open.pop(order_id, None)
        if o is None:
            raise KeyError(order_id)
        d = 1 if o.side == "buy" else -1
        pnl = d * (price - o.entry) * o.qty
        rec = {"id": o.id, "symbol": o.symbol, "pnl_usd": pnl, "exit": price}
        self.closed.append(rec)
        return rec


class LiveExecutor(ExecutionAdapter):
    """Segnaposto: NON implementato. Non invia ordini reali."""
    name = "live"

    def _no(self, *a, **k):
        raise NotImplementedError("Esecuzione reale non implementata: richiede chiavi API a permessi limitati, "
                                  "conferma esplicita dell'utente e una fase di prova in simulazione.")
    place = positions = close = _no


def plan_order(signal: dict, symbol: str, cfg: ExecutionConfig, open_positions: int = 0, now_asof: str | None = None) -> dict:
    """Restituisce {"ok": bool, "order": Order|None, "checks": [...]}. Ogni controllo è riportato con l'esito."""
    checks = []

    def chk(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        return ok

    chk("interruttore generale", cfg.enabled, "attivo" if cfg.enabled else "spento: nessun ordine verrà generato")
    chk("modalità", cfg.mode == "paper", f"{cfg.mode}" + ("" if cfg.mode == "paper" else " non implementata (solo paper)"))
    side = signal.get("side")
    chk("segnale attivo", side in ("long", "short"), "direzione: " + str(side))
    rel = signal.get("reliability") or {}
    chk("affidabilità", rel.get("grade") in cfg.allowed_grades, f"{rel.get('grade')} (richiesta: {', '.join(cfg.allowed_grades)})")
    n_oos = rel.get("n", 0)
    chk("operazioni del pool fuori campione", n_oos >= cfg.min_oos_trades, f"{n_oos} (minimo {cfg.min_oos_trades})")
    chk("posizioni aperte", open_positions < cfg.max_open, f"{open_positions} su {cfg.max_open}")
    if now_asof and signal.get("asof"):
        import pandas as pd
        age = (pd.Timestamp(now_asof) - pd.Timestamp(signal["asof"])).days
        chk("età del segnale", age <= cfg.max_signal_age_days, f"{age} giorni (massimo {cfg.max_signal_age_days})")
    ok_levels = False
    if side in ("long", "short"):
        e, s, t = signal["entry"], signal["stop_loss"], signal["take_profit"]
        d = 1 if side == "long" else -1
        ok_levels = (e - s) * d > 0 and (t - e) * d > 0
    chk("livelli coerenti", ok_levels, "stop e target dalla parte giusta dell'ingresso" if ok_levels else "livelli non validi")
    if not all(c["ok"] for c in checks):
        return {"ok": False, "order": None, "checks": checks}

    risk_usd = cfg.equity * cfg.risk_pct / 100
    qty = risk_usd / abs(signal["entry"] - signal["stop_loss"])
    cap = cfg.equity * cfg.max_notional_pct / 100
    capped = qty * signal["entry"] > cap
    if capped:
        qty = cap / signal["entry"]
    chk("dimensione", True, f"rischio {risk_usd:,.0f} USD" + (f", ridotta al tetto del {cfg.max_notional_pct:g}% del capitale" if capped else ""))
    order = Order(symbol=symbol, side="buy" if side == "long" else "sell", qty=qty, entry=signal["entry"],
                  stop_loss=signal["stop_loss"], take_profit=signal["take_profit"], horizon_days=signal["horizon_days"],
                  meta={"reliability": rel.get("grade"), "asof": signal.get("asof"), "source": "AHA"})
    return {"ok": True, "order": order, "checks": checks}
