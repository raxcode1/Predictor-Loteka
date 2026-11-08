# Informe cuantitativo Ruleta Express

- Pipeline: `quant-1.0.0` · generado 2026-11-08T15:10:29+00:00 · semilla 20261108
- Dataset SHA-256: `d5367c6961a1c3cc580c60c39f7e04b7ae9cfb20dbb0ffe5a593f1dac144230e`
- Periodo: sorteos 219640–253154 (27/05/2026 → 07/11/2026), 33181 filas, 334 sorteos ausentes, 33159 transiciones consecutivas
- Hipótesis nula: resultados i.i.d. uniformes en 0..18 (P = 1/19)

## Decisión: **SIN VENTAJA** (modo paper, señales permitidas: False)

- ✔ `muestra_test_minima`: 6637 (umbral 1000)
- ✔ `modelo_no_uniforme`: markov1(a=10,w=all) (umbral ≠ uniform)
- ✔ `log_loss_test_vs_uniforme`: 1.7465567910961661e-13 (umbral p < 0.01)
- ✔ `estabilidad_walk_forward`: 5 (umbral >= 4 bloques)
- ✔ `calibracion_ece`: 0.011441484287927411 (umbral <= 0.02)
- ✘ `posterior_top1_sobre_break_even`: 0.9356328723871845 (umbral >= 0.95)
- ✘ `limite_inferior_credible`: 0.051463713978049594 (umbral > 0.0526)

## Particiones (cronológicas, bloqueadas)

- train: índices 0–19908 (19908), sorteos 219640–239881
- validation: índices 19908–26544 (6636), sorteos 239882–246517
- test: índices 26544–33181 (6637), sorteos 246518–253154

## Selección de modelo (sólo validation)

| modelo | log-loss validation |
|---|---|
| markov1(a=10,w=all) | 2.924485 |
| markov1(a=1,w=all) | 2.925268 |
| markov1(a=100,w=all) | 2.931782 |
| markov1(a=10,w=5000) | 2.938861 |
| markov1(a=100,w=5000) | 2.941080 |
| markov2(a=10,w=all) | 2.942443 |
| markov2(a=100,w=all) | 2.943094 |
| markov2(a=100,w=5000) | 2.944153 |
| markov3(a=100,w=all) | 2.944367 |
| uniform | 2.944439 |
| markov3(a=100,w=5000) | 2.944447 |
| markov2(a=10,w=5000) | 2.944454 |
| markov3(a=10,w=all) | 2.944507 |
| dirichlet(a=100,w=all) | 2.944522 |
| dirichlet(a=10,w=all) | 2.944566 |
| dirichlet(a=1,w=all) | 2.944571 |
| markov3(a=10,w=5000) | 2.944680 |
| dirichlet(a=100,w=2000) | 2.945677 |
| dirichlet(a=10,w=2000) | 2.948093 |
| dirichlet(a=1,w=2000) | 2.948710 |
| markov1(a=1,w=5000) | 2.951887 |
| markov3(a=1,w=5000) | 2.955981 |
| markov3(a=1,w=all) | 2.983515 |
| markov2(a=1,w=all) | 3.006652 |
| markov2(a=1,w=5000) | 3.031229 |

Seleccionado: **markov1(a=10,w=all)**, recalibración λ = 0.0000

## Test bloqueado (fuera de muestra)

- n = 6637; log-loss 2.924570 vs uniforme 2.944439
- Brier 0.945918 vs uniforme 0.947368
- top-1 0.0570 (base 0.0526), top-5 0.2896 (base 0.2632)
- ECE top-label 0.0114; ECE one-vs-rest 0.00271
- Diebold-Mariano: mejora media 0.019869 nats, z = 7.274, p = 0.0000
- Binomial top-1: p = 0.0620

### Walk-forward por bloques del test

| sorteos | n | log-loss | mejora vs 1/19 | top-1 |
|---|---|---|---|---|
| 246518–247844 | 1327 | 2.92602 | +0.01841 | 0.0580 |
| 247845–249171 | 1327 | 2.92499 | +0.01945 | 0.0528 |
| 249172–250499 | 1328 | 2.91912 | +0.02532 | 0.0595 |
| 250500–251826 | 1327 | 2.93079 | +0.01364 | 0.0490 |
| 251827–253154 | 1328 | 2.92194 | +0.02250 | 0.0655 |

## Bondad de ajuste e independencia (Holm α=0.01)

| test | estadístico | gl | p | p Holm | p BH | rechaza |
|---|---|---|---|---|---|---|
| chi2_frecuencias_uniformes | 41.0317 | 18 | 0.001507 | 0.004521 | 0.001938 | sí |
| chi2_independencia_lag1 | 1587.7150 | 324 | 2.035e-165 | 1.628e-164 | 9.158e-165 | sí |
| chi2_independencia_lag2 | 500.1491 | 324 | 1.104e-09 | 5.52e-09 | 1.987e-09 | sí |
| chi2_independencia_lag3 | 418.5220 | 324 | 0.000303 | 0.001212 | 0.0004545 | sí |
| chi2_homogeneidad_10_bloques | 116.2203 | 162 | 0.9974 | 0.9974 | 0.9974 | no |
| binomial_repeticiones_adyacentes | 390.0000 | - | 0 | 0 | 0 | sí |
| runs_alto_bajo | 9.1613 | - | 5.13e-20 | 3.078e-19 | 1.154e-19 | sí |
| ljung_box_10_lags | 133.7855 | 10 | 7.88e-24 | 5.516e-23 | 2.364e-23 | sí |
| ks_pit_aleatorizada | 0.0050 | - | 0.3776 | 0.7551 | 0.4248 | no |

Se rechaza la nula en: chi2_frecuencias_uniformes, chi2_independencia_lag1, chi2_independencia_lag2, chi2_independencia_lag3, binomial_repeticiones_adyacentes, runs_alto_bajo, ljung_box_10_lags. Un rechazo describe una desviación; no implica una ventaja explotable.

## Bayes

- α=0.5: log BF(frecuencias vs uniforme) = -55.703; log BF(Markov-1 vs iid) = 172.654
- α=1.0: log BF(frecuencias vs uniforme) = -48.103; log BF(Markov-1 vs iid) = 300.101
- α=10.0: log BF(frecuencias vs uniforme) = -26.154; log BF(Markov-1 vs iid) = 551.352
- top-1 test, prior uniforme Beta(1,1): media 0.0571, IC95 [0.0516, 0.0628], P(>1/19) = 0.944
- top-1 test, prior Jeffreys Beta(0.5,0.5): media 0.0570, IC95 [0.0516, 0.0627], P(>1/19) = 0.941
- top-1 test, prior escéptico Beta centrado en 0.0526 (n0=200): media 0.0568, IC95 [0.0515, 0.0624], P(>1/19) = 0.936

## Monte Carlo

- Nula, mejora de log-loss: p = 0.0005 (2000 sims)
- Nula, aciertos top-1: p = 0.0598
- Bankroll 1%/ronda bajo nula: ROI mediano -52.55%, drawdown máx. q95 96.12%, P(ruina 50%) 78.50%
- Bankroll 1%/ronda bajo posterior escéptico: ROI mediano -4.22%, P(beneficio) 45.10%

## PRNG

Veredicto: **NO_CONCLUYENTE** sobre el bloque contiguo {'first_draw': 226777, 'last_draw': 253154, 'length': 26378}.

- MT19937: NO_APLICABLE — requiere 624 salidas consecutivas de 32 bits; cada sorteo aporta 4.25 bits y el mapeo a 0..18 es desconocido y no lineal
- LCG (parámetros desconocidos): NO_APLICABLE — la recuperación por GCD requiere salidas completas del estado
- LCG (catálogo conocido): NO_CONCLUYENTE — consultas sin resolver por límite de tiempo/coste
  - hipótesis probadas 144, resultados {'sat': 0, 'unsat': 57, 'unknown': 87}, cota de azar Bonferroni 4.99e-19
- xorshift / PCG: NO_CONCLUYENTE — no implementado: requiere hipótesis concretas de parámetros y mapeo
- CSPRNG / fuente física: NO_APLICABLE — no recuperable por diseño; compatible con ausencia de señal estadística

> Un solver que satisface observaciones no prueba la identidad del generador, y un fallo no descarta otros PRNG.

## Limitaciones

- Sólo se observa el número publicado; el mapeo estado→número es desconocido.
- El break-even asume pago neto 18:1; con un pago real menor la exigencia es mayor.
- Los huecos de numeración se excluyen de las transiciones; no se imputan.
- Ningún resultado de este informe implica que el siguiente sorteo sea predecible.
