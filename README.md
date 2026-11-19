# AHA - Alpha Horizon Crypto Analytics

Piattaforma di analisi crypto (on-chain + analisi tecnica) con segnali, derivata da GFIP e
specializzata sulle criptovalute. Web app leggera (FastAPI + JS vanilla, installabile come PWA da smartphone).

## Stato (passo 1)

- Motore GFIP riusato: indicatori, walk-forward con validazione fuori campione, scanner di segnali,
  piani entry/stop/target, backtest, calibrazione live, alert.
- Rimosso tutto ciò che non è crypto: Yahoo, FRED, CFTC COT, GDELT, azioni, valute, tassi.
- Universo: 20 coppie USDT su Binance (`config/universe.json`).
- Nuovo livello dati `app/data/`:
  - `adapters.py`: prezzi OHLCV Binance (mirror `data-api.binance.vision` se l'host principale è bloccato).
  - `onchain.py`: CoinMetrics community (MVRV, indirizzi attivi, flussi da/verso exchange, capitalizzazione),
    DefiLlama (TVL), Alternative.me (Fear & Greed) e Glassnode (con chiave `GLASSNODE_API_KEY`: SOPR, NVT).
- Velocità: walk-forward con finestra mobile; ciclo di 3 monete in circa 30 s (GFIP: oltre 15 minuti per una).

## Segnali (passo 2)

`app/signals.py` combina EMA, RSI, MACD, Bollinger, MVRV, flussi exchange e Fear & Greed:
- i parametri di ogni indicatore sono scelti su una griglia usando solo il primo 60% della storia;
- il segnale (long/short, entry, stop loss e take profit a multipli di ATR, orizzonte) è provato sull'intera storia
  con gli stessi stop/target e con i costi; **l'affidabilità si basa solo sul 40% finale (fuori campione)**,
  confrontato con l'ingresso casuale nella stessa direzione;
- le metriche on-chain mancanti per una moneta non votano e sono dichiarate (`onchain_missing`).

API: `GET /api/aha/signals` (elenco) e `GET /api/aha/signal/{id}?ema_fast=12&horizon=21&atr_stop=1.5...`
(ricalcolo con parametri modificati: base per i grafici interattivi).

## Avvio

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Test: `pip install -r requirements-dev.txt && pytest -q`

## Note

- SOPR e NVT reale non sono nel piano gratuito CoinMetrics: `nvt_proxy` è un proxy dichiarato come tale.
- I derivati Binance (`fapi`) sono bloccati da alcune regioni (HTTP 451): da gestire come fonte opzionale.

## Roadmap

1. Fatto: base crypto-only + livello dati + velocità.
2. Fatto: segnali con on-chain, ricerca parametri, affidabilità e % storica.
3. Segnali: SOPR/NVT reali (chiave Glassnode), derivati, bilanciamento dei pesi dei voti.
4. Grafici interattivi per smartphone con parametri modificabili.
5. API per CryptoRadar e interfaccia `ExecutionAdapter` per gli exchange (automazione futura).
