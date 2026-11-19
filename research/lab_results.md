# Risultati del laboratorio (pool di 20 monete)

Scelta sul periodo in campione; i valori fuori campione NON sono stati usati per scegliere. R = multiplo del rischio, netto dei costi.

| Variante | verso | IN CAMPIONE n | exp R | FUORI CAMPIONE n | exp R | exp % | win % | payoff | PF | IC95% R | p caso | p Holm | grado |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| V0 uscite ATR fisse (2/3) | long | 998 | +0.19 | 699 | -0.04 | -0.94 | 43 | +1.10 | +0.83 | [-0.20, +0.11] | +0.59 | +1.00 | bassa |
| V0 uscite ATR fisse (2/3) | short | 569 | +0.03 | 416 | +0.01 | +0.35 | 47 | +1.19 | +1.07 | [-0.17, +0.19] | +0.24 | +1.00 | bassa |
| V1 uscite da supporti/resistenze | long | 795 | +0.18 | 578 | -0.00 | -0.59 | 43 | +1.17 | +0.89 | [-0.17, +0.16] | +0.43 | +1.00 | bassa |
| V1 uscite da supporti/resistenze | short | 467 | +0.03 | 305 | +0.04 | +0.59 | 47 | +1.27 | +1.12 | [-0.19, +0.27] | +0.14 | +1.00 | bassa |
| V2 V1 + regime (EMA200) | long | 401 | +0.19 | 328 | -0.01 | -0.83 | 40 | +1.27 | +0.86 | [-0.22, +0.21] | +0.61 | +1.00 | bassa |
| V2 V1 + regime (EMA200) | short | 274 | +0.09 | 220 | +0.04 | +1.05 | 48 | +1.31 | +1.22 | [-0.23, +0.33] | +0.35 | +1.00 | bassa |
| V3 V1 + 3 voti minimi | long | 180 | +0.14 | 113 | +0.01 | -0.73 | 44 | +1.09 | +0.86 | [-0.21, +0.25] | +0.42 | +1.00 | bassa |
| V3 V1 + 3 voti minimi | short | 32 | -0.29 | 9 | +1.27 | +11.44 | 78 | +4.37 | +15.29 | [+0.09, +2.32] | +0.00 | +0.02 | non valutabile |
| V4 V1 solo trend (senza ritorno alla media) | long | 859 | +0.20 | 615 | +0.00 | -0.50 | 41 | +1.32 | +0.91 | [-0.19, +0.18] | +0.44 | +1.00 | bassa |
| V4 V1 solo trend (senza ritorno alla media) | short | 451 | +0.06 | 305 | +0.04 | +0.44 | 47 | +1.24 | +1.09 | [-0.15, +0.23] | +0.12 | +1.00 | bassa |
| V5 V1 solo ritorno alla media (senza trend) | long | 385 | -0.05 | 244 | -0.02 | -0.49 | 48 | +0.99 | +0.90 | [-0.23, +0.17] | +0.48 | +1.00 | bassa |
| V5 V1 solo ritorno alla media (senza trend) | short | 350 | -0.38 | 138 | -0.26 | -1.47 | 36 | +1.31 | +0.74 | [-0.52, +0.03] | +0.90 | +1.00 | bassa |
| V6 V1 + ingresso vicino a livello | long | 499 | +0.17 | 373 | -0.01 | -0.78 | 42 | +1.19 | +0.86 | [-0.20, +0.21] | +0.44 | +1.00 | bassa |
| V6 V1 + ingresso vicino a livello | short | 296 | +0.07 | 197 | +0.03 | +0.77 | 45 | +1.44 | +1.18 | [-0.24, +0.32] | +0.18 | +1.00 | bassa |
| V7 V1 senza on-chain/sentiment | long | 752 | +0.23 | 507 | +0.01 | -0.72 | 42 | +1.21 | +0.88 | [-0.20, +0.22] | +0.41 | +1.00 | bassa |
| V7 V1 senza on-chain/sentiment | short | 656 | +0.04 | 459 | +0.02 | +0.32 | 46 | +1.28 | +1.07 | [-0.19, +0.26] | +0.27 | +1.00 | bassa |

Variante con la migliore aspettativa IN CAMPIONE (unico criterio di scelta): **V4 V1 solo trend (senza ritorno alla media)**

Operazioni scartate dal piano di uscita (motivo: numero):
- V0 uscite ATR fisse (2/3): {}
- V1 uscite da supporti/resistenze: {'rischio/rendimento insufficiente in spazio libero': 852, 'nessun livello a favore con rischio/rendimento >= 1.5': 2485}
- V2 V1 + regime (EMA200): {'rischio/rendimento insufficiente in spazio libero': 711, 'nessun livello a favore con rischio/rendimento >= 1.5': 2030}
- V3 V1 + 3 voti minimi: {'rischio/rendimento insufficiente in spazio libero': 50, 'nessun livello a favore con rischio/rendimento >= 1.5': 205}
- V4 V1 solo trend (senza ritorno alla media): {'rischio/rendimento insufficiente in spazio libero': 1095, 'nessun livello a favore con rischio/rendimento >= 1.5': 2676}
- V5 V1 solo ritorno alla media (senza trend): {'rischio/rendimento insufficiente in spazio libero': 25, 'nessun livello a favore con rischio/rendimento >= 1.5': 300}
- V6 V1 + ingresso vicino a livello: {'nessun livello a favore con rischio/rendimento >= 1.5': 1239, 'rischio/rendimento insufficiente in spazio libero': 197}
- V7 V1 senza on-chain/sentiment: {'rischio/rendimento insufficiente in spazio libero': 1347, 'nessun livello a favore con rischio/rendimento >= 1.5': 3503}
