# Benchmark fuori campione (pool di 20 monete, portafoglio equipesato)

| Portafoglio | rend. totale % | annuo % | volatilità % | Sharpe | max drawdown % | giorni |
|---|---|---|---|---|---|---|
| Buy & hold (equipesato) | +35 | +8.6 | 62 | +0.44 | -75 | 1325 |
| Strategia solo long (spot, niente funding) | +35 | +8.7 | 28 | +0.44 | -42 | 1325 |
| Strategia long+short, funding IGNORATO | +36 | +8.9 | 33 | +0.42 | -48 | 1325 |
| Strategia long+short su perpetual, CON funding | +23 | +5.8 | 33 | +0.34 | -48 | 1325 |
| Regola semplice solo long (sopra EMA200, stop mobile, spot) | +47 | +11.2 | 34 | +0.49 | -40 | 1325 |
| Regola semplice long+short con funding (senza voti) | +25 | +6.5 | 42 | +0.36 | -48 | 1325 |

Esposizione media (quota dei giorni in posizione): long_spot 26%, ls_fund 55%, simple_ls_fund 90%

Monete in cui la strategia long+short con funding batte il buy & hold: 8 su 20
