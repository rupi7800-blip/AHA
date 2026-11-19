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

## Segnali e metodo di valutazione

`app/signals.py` combina EMA, MACD, (RSI e Bollinger con peso 0 di default), MVRV, flussi exchange, SOPR, NVT e Fear & Greed.
La strategia predefinita è **trend-following con stop mobile**: voti di trend + on-chain, solo nel verso dell'EMA200, stop iniziale
oltre il supporto/resistenza importante più vicino, poi stop che segue gli ultimi minimi/massimi confermati, uscita a 60 giorni.
Il take profit mostrato è solo il prossimo livello importante (presa parziale), non fa parte della strategia.

**Come si valuta** (`app/validation.py`):
- il successo non si misura con la % di operazioni in positivo ma con l'**aspettativa netta per operazione** (in % e in multipli
  di rischio R), il rapporto guadagno medio / perdita media, il profit factor;
- il **"caso"** è entrare in momenti casuali con le stesse regole di uscita: il calendario dei trade viene spostato di un numero
  casuale di giorni, uguale per tutte le monete (si conservano numero di trade, quota long/short e correlazione tra monete);
- **long e short** sono valutati separatamente;
- l'affidabilità (alta/media/bassa/non valutabile) è del **pool di monete**, non della singola: bootstrap a blocchi di mese,
  correzione di Holm, costi doppi, senza i 5 trade migliori, senza una moneta alla volta;
- simulazione prudente: ingresso all'apertura della barra dopo il segnale, gap oltre lo stop = uscita all'apertura, stop prima del
  target nella stessa barra, 20 bps per lato + 10 bps extra sugli stop. Il funding dei perpetual non è modellato;
- i dati dal 2026-09-10 in poi sono **riservati** (data fissa, `signals.HOLDOUT_START`): non entrano né nella scelta dei parametri né
  nelle statistiche, e non sono esposti dall'API. Con un orizzonte di 60 giorni contengono comunque troppe poche operazioni per
  validare (le ho contate una sola volta, non i risultati: 1 nel pool), quindi la prova fuori campione vera è il **registro in
  avanti** (`/api/aha/track`): un trade alla volta per moneta, esiti calcolati con le stesse regole, confronto con il caso;
- la correzione di Holm dell'app copre solo i due versi; quella del laboratorio copre tutte le varianti dei due giri.

**Uscite da supporti e resistenze** (`app/levels.py`): pivot confermati con ritardo, zone raggruppate in ATR, forza da tocchi,
recenza e ampiezza della reazione; stop oltre la zona con margine, target prima della resistenza con R/R minimo 1,5.

## Risultati della ricerca (cartella `research/`)

Pool di 20 monete, scelta sul periodo in campione, verifica fuori campione, costi realistici:
- Indicatori classici (8 varianti, giro 1) con uscite ATR fisse o da livelli: aspettativa tra -0,05R e +0,05R sui long e tra -0,01R
  e +0,05R sugli short, nessuna distinguibile dal caso. Fanno eccezione V5 short (solo ritorno alla media: -0,24R, in perdita) e
  V3 short (+1,27R ma 9 operazioni fuori campione: rumore).
- Supporti e resistenze come livelli di rimbalzo (`level_quality_*.md`): le zone importanti rimbalzano come punti qualunque, anche
  con molti tocchi. Servono come criterio logico di invalidazione, non come previsione.
- Trend-following con stop mobile (giro 2, esplorativo): la variante scelta in campione (T3, quella predefinita) dà long +0,40R e
  short +0,13R fuori campione, ma il **caso con le stesse uscite** dà +0,34R e +0,04R: il vantaggio viene dalla struttura di uscita e
  (probabilmente, non è stato separato con un test) dalla deriva rialzista dei sopravvissuti, non dal tempismo degli ingressi
  (p=0,37 e 0,31). Affidabilità: **bassa**. Con costi doppi +0,35R e +0,09R; **senza i 5 trade migliori +0,01R e +0,01R**: il
  risultato dipende da pochi trade molto grandi (tipico del trend-following: vince poco spesso ma molto).
- Un grado "media" compare solo per T5 long (3 voti minimi, +0,78R, p=0,04 non corretto, Holm 1,00; senza i 5 migliori -0,06R): da non fidarsi.
- Non è stato fatto: funding dei perpetual (su 60 giorni vale circa 0,15-0,3R: può azzerare il vantaggio sui long con leva; non pesa sullo spot),
  confronto con buy & hold, congelamento della configurazione con l'hash del commit prima del test in avanti.
- Limiti: le 20 monete sono i sopravvissuti; le soglie on-chain (MVRV 1 e 3,5) vengono da letteratura costruita sull'intera storia di BTC;
  il periodo fuori campione è stato visto nel primo giro del laboratorio (il secondo è esplorativo).

## Interfaccia (passo 3)

`/` è la nuova interfaccia AHA, pensata per smartphone (installabile come PWA): scheda del segnale con ingresso, stop loss,
take profit, orizzonte, affidabilità e percentuale storica fuori campione; grafici Plotly (candele, EMA, Bollinger, supporti/resistenze, RSI,
MACD, MVRV, netflow exchange, SOPR, NVT, Fear & Greed) e registro in avanti, con i parametri **modificabili**: cambiando un valore, segnale,
grafici e percentuali si ricalcolano (`GET /api/aha/chart/{id}?...`). `/classic` è la vecchia interfaccia GFIP.

## Aggiornamento dei dati

Solo manuale: tasto **Aggiorna dati** nell'interfaccia (o `POST /api/refresh`). Misurato su tutte e 20 le monete,
da zero: 328 s tra download e analisi, su 4 CPU. Lo scheduler periodico è opt-in (`AHA_SCHEDULER=1`).

## Integrazione con CryptoRadar

`GET /api/aha/export[?only_active=true]` restituisce `{generatedAt, items: Signal[]}` con gli stessi nomi/tipi di
CryptoRadar (camelCase, date ISO, id CoinGecko in `coins`, prezzi USD) più `side`, `targetPrice` (può essere `null` con lo stop
mobile), `stopPrice`, `confidence` (0,8 alta / 0,55 media; `null` per bassa o non valutabile), `expectancyR` e `nPool`
(aspettativa e numero di operazioni del pool), `reliability`, `basisStop`, `basisTarget`, `riskReward`, `horizonDays`.
Lato CryptoRadar mancano (da fare nel suo repository): tabella `aha_signals`, job `refreshAhaSignals()` in
`src/lib/ingest/aha.ts`, rotta `/api/signals`, variabile `AHA_BASE_URL`.

## Esecuzione (predisposizione)

`app/execution.py`: interfaccia `ExecutionAdapter`, esecutore simulato `PaperExecutor` e `plan_order`, che calcola la
dimensione dal rischio (% del capitale perso allo stop) e applica controlli di sicurezza: interruttore generale (spento
di default), affidabilità minima ("alta" di default), operazioni fuori campione minime, posizioni aperte, età del segnale,
coerenza di stop e target. `LiveExecutor` NON è implementato e non invia ordini.
API: `/api/aha/execution/config`, `/preview/{id}`, `/paper/{id}` (solo simulazione), `/positions`.

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
3. In sospeso: SOPR/NVT reali (chiave Glassnode salvata nell'ambiente, ma l'API risponde 401), derivati, pesi dei voti.
4. Fatto: grafici interattivi per smartphone con parametri modificabili.
5. Fatto lato AHA: export per CryptoRadar e interfaccia di esecuzione (solo simulata). Da fare: lato CryptoRadar e, in futuro, esecutore reale.
