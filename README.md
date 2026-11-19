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
- Il livello on-chain è pronto ma **non ancora collegato** ai segnali né ai grafici (passi successivi).
- Il fix alla lentezza di GFIP è nei passi successivi (vedi sotto).

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

1. (questo passo) Base crypto-only + livello dati.
2. Integrazione on-chain nei segnali, EMA/RSI/MACD/Bollinger con ricerca dei parametri in walk-forward.
3. Segnali completi: long/short, stop loss, take profit, orizzonte, affidabilità e % storica di esito positivo.
4. Grafici interattivi per smartphone con parametri modificabili.
5. API per CryptoRadar e interfaccia `ExecutionAdapter` per gli exchange (automazione futura).
