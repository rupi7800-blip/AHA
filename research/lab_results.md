# Risultati del laboratorio (pool di 20 monete)

Scelta sul periodo in campione; i valori fuori campione NON sono stati usati per scegliere. R = multiplo del rischio, netto dei costi.

| Variante | verso | IN CAMPIONE n | exp R | FUORI CAMPIONE n | exp R | exp % | win % | payoff | PF | IC95% R | p caso | p Holm | grado |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| V0 uscite ATR fisse (2/3) | long | 992 | +0.18 | 680 | -0.05 | -1.01 | 43 | +1.09 | +0.82 | [-0.20, +0.11] | +0.62 | +1.00 | bassa |
| V0 uscite ATR fisse (2/3) | short | 563 | +0.04 | 413 | +0.02 | +0.40 | 48 | +1.19 | +1.08 | [-0.17, +0.20] | +0.15 | +1.00 | bassa |
| V1 uscite da supporti/resistenze | long | 789 | +0.17 | 560 | -0.02 | -0.89 | 42 | +1.13 | +0.84 | [-0.19, +0.15] | +0.59 | +1.00 | bassa |
| V1 uscite da supporti/resistenze | short | 459 | +0.03 | 304 | +0.04 | +0.60 | 47 | +1.27 | +1.13 | [-0.17, +0.26] | +0.13 | +1.00 | bassa |
| V2 V1 + regime (EMA200) | long | 397 | +0.18 | 318 | -0.03 | -1.05 | 40 | +1.25 | +0.82 | [-0.24, +0.20] | +0.65 | +1.00 | bassa |
| V2 V1 + regime (EMA200) | short | 271 | +0.10 | 220 | +0.04 | +1.05 | 48 | +1.31 | +1.22 | [-0.23, +0.33] | +0.31 | +1.00 | bassa |
| V3 V1 + 3 voti minimi | long | 178 | +0.15 | 108 | -0.02 | -1.10 | 44 | +1.04 | +0.80 | [-0.25, +0.23] | +0.45 | +1.00 | bassa |
| V3 V1 + 3 voti minimi | short | 32 | -0.29 | 9 | +1.27 | +11.44 | 78 | +4.37 | +15.29 | [+0.09, +2.32] | +0.00 | +0.03 | non valutabile |
| V4 V1 solo trend (senza ritorno alla media) | long | 851 | +0.19 | 597 | -0.02 | -0.82 | 40 | +1.27 | +0.85 | [-0.21, +0.16] | +0.54 | +1.00 | bassa |
| V4 V1 solo trend (senza ritorno alla media) | short | 445 | +0.06 | 304 | +0.05 | +0.45 | 47 | +1.23 | +1.10 | [-0.15, +0.23] | +0.10 | +1.00 | bassa |
| V5 V1 solo ritorno alla media (senza trend) | long | 381 | -0.04 | 244 | -0.02 | -0.49 | 48 | +0.99 | +0.90 | [-0.23, +0.17] | +0.55 | +1.00 | bassa |
| V5 V1 solo ritorno alla media (senza trend) | short | 347 | -0.37 | 132 | -0.24 | -1.04 | 37 | +1.37 | +0.81 | [-0.50, +0.09] | +0.76 | +1.00 | bassa |
| V6 V1 + ingresso vicino a livello | long | 497 | +0.16 | 364 | -0.02 | -0.98 | 41 | +1.17 | +0.82 | [-0.24, +0.19] | +0.57 | +1.00 | bassa |
| V6 V1 + ingresso vicino a livello | short | 294 | +0.07 | 196 | +0.03 | +0.80 | 45 | +1.43 | +1.19 | [-0.24, +0.34] | +0.15 | +1.00 | bassa |
| V7 V1 senza on-chain/sentiment | long | 749 | +0.23 | 490 | -0.01 | -1.02 | 41 | +1.17 | +0.83 | [-0.23, +0.21] | +0.54 | +1.00 | bassa |
| V7 V1 senza on-chain/sentiment | short | 646 | +0.04 | 455 | +0.03 | +0.36 | 46 | +1.28 | +1.08 | [-0.18, +0.26] | +0.20 | +1.00 | bassa |

Variante con la migliore aspettativa IN CAMPIONE (unico criterio di scelta): **V2 V1 + regime (EMA200)**

Operazioni scartate dal piano di uscita (motivo: numero):
- V0 uscite ATR fisse (2/3): {}
- V1 uscite da supporti/resistenze: {'rischio/rendimento insufficiente in spazio libero': 852, 'nessun livello a favore con rischio/rendimento >= 1.5': 2485}
- V2 V1 + regime (EMA200): {'rischio/rendimento insufficiente in spazio libero': 711, 'nessun livello a favore con rischio/rendimento >= 1.5': 2030}
- V3 V1 + 3 voti minimi: {'rischio/rendimento insufficiente in spazio libero': 50, 'nessun livello a favore con rischio/rendimento >= 1.5': 205}
- V4 V1 solo trend (senza ritorno alla media): {'rischio/rendimento insufficiente in spazio libero': 1095, 'nessun livello a favore con rischio/rendimento >= 1.5': 2676}
- V5 V1 solo ritorno alla media (senza trend): {'rischio/rendimento insufficiente in spazio libero': 25, 'nessun livello a favore con rischio/rendimento >= 1.5': 300}
- V6 V1 + ingresso vicino a livello: {'nessun livello a favore con rischio/rendimento >= 1.5': 1239, 'rischio/rendimento insufficiente in spazio libero': 197}
- V7 V1 senza on-chain/sentiment: {'rischio/rendimento insufficiente in spazio libero': 1347, 'nessun livello a favore con rischio/rendimento >= 1.5': 3503}
