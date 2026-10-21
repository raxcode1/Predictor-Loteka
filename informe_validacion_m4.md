# Informe de validación — Estrategia Cuantitativa M4 (Ruleta Express)

Fuente: `C:\Users\diose\OneDrive\Desktop\Ruleta Express\registros_supervision_probs_120 - registros_supervision_probs_120.csv`
Parámetros: modelo `M4`, ventana (5.0%, 7.0%], Δ máximo 4.7%, anclaje [1, 7, 8], aislamiento `any`.

## 1. Integridad del log de supervisión

| Métrica | Valor |
| --- | --- |
| Filas totales | 120 |
| Grupos de predicción únicos | 86 |
| Filas duplicadas | 43 |
| Grupos con resultados contradictorios | 6 |
| Filas descartadas por conflicto | 35 |
| Filas utilizables (observaciones independientes) | 80 |
| Rango de resultados | 0..18 |
| Orden cronológico por `no. sorteo` | False |
| Sorteos obsoletos | (248978,) |

- El identificador de sorteo [248978] reaparece después de sorteos posteriores: son predicciones obsoletas re-escritas en el log, no observaciones independientes.
- El orden de filas del log no es monótono en `no. sorteo`: el paso temporal debe derivarse del orden de fila, no del identificador de sorteo.
- 35 filas (29.2%) pertenecen a 6 grupos de predicción con resultados contradictorios: su etiqueta no es verificable y se descartan del cálculo principal.

## 2. Contraste de los hallazgos del PDF

| Afirmación | Esperado | Observado | Estado |
| --- | --- | --- | --- |
| P<5,0% ⇒ tasa de acierto ~0,0% (descartable) | 0.00% | 0.00% | CONFIRMADO |
| 5,0%–7,0% ⇒ 6,46% de acierto global (alta confianza) | 6.46% | 6.46% | CONFIRMADO |
| P>8,0% ⇒ ~2,3% de acierto (falso positivo por sobreajuste) | 2.30% | 2.19% | CONFIRMADO |
| 5,0%<P(M4)≤7,0% ⇒ 13,75% de acierto | 13.75% | 13.75% | CONFIRMADO |
| la probabilidad principal de la ronda debe emitirla M4 | 100.00% | 0.00% | NO CONFIRMADO |
| Δ = Pmax − Pmin ≤ 4,7% o la ronda se descarta | — | 11.84% | CONDICIONAL |
| último número en {1, 7, 8} ⇒ confianza «Crítico» y mayor stake | — | 22.22% | NO CONFIRMADO |
| 1/37 ≈ 2,70% es la probabilidad natural de un pleno | 2.70% | 5.26% | NO CONFIRMADO |
| la ventaja de M4 es general, no un artefacto de un número | — | 11.90% | NO CONFIRMADO |

- **zona de ruido**: 0/25 aciertos en la zona de ruido.
- **zona de convergencia global**: Replicado sobre 913 observaciones de la matriz completa.
- **trampa estadística**: 3/137 aciertos por encima del 8%.
- **tasa óptima de M4**: Replicado exactamente sobre el log bruto (11/80); en el log depurado la tasa es 17.02% (8/47).
- **aislamiento de modelo**: M4 alcanza la probabilidad máxima en 6 rondas, pero en ninguna de ellas P(M4) cae dentro de la ventana 5,0%–7,0%: la intersección literal de las dos condiciones es vacía y el motor estricto no emite alertas (tie=0, strict=0).
- **filtro de dispersión**: El filtro descarta 4 de 80 rondas de la ventana y con ello 2 aciertos: la tasa baja de 13.75% a 11.84%.
- **acelerador de anclaje**: 2/9 aciertos anclados frente a 6/38 sin ancla; Fisher p=0.639 (no significativo: no justifica escalar el stake).
- **ventaja base de la casa**: El log contiene 19 resultados distintos (0..18); la línea base equiprobable del espacio observado es 1/19 = 5.26%, no 2,70%. La ventaja real del sistema frente a su propio nulo es menor que la multiplicada por cinco del PDF.
- **concentración de aciertos**: El número 2 concentra 3/5 aciertos; sin él la tasa cae a 11.90% (5/42), p=0.068 frente a 1/19.

## 3. Zonas de densidad probabilística

Matriz completa (los diez submodelos):

| Zona | Observaciones | Aciertos | Tasa | IC 95% | p (vs 1/19) |
| --- | --- | --- | --- | --- | --- |
| <5% | 25 | 0 | 0.00% | [0.00%, 13.32%] | 1.0000 |
| 5-7% | 913 | 59 | 6.46% | [5.04%, 8.25%] | 0.0641 |
| 7-8% | 125 | 5 | 4.00% | [1.72%, 9.02%] | 0.7926 |
| >8% | 137 | 3 | 2.19% | [0.75%, 6.24%] | 0.9773 |

Modelo M4:

| Zona | Observaciones | Aciertos | Tasa | IC 95% | p (vs 1/19) |
| --- | --- | --- | --- | --- | --- |
| <5% | 8 | 0 | 0.00% | [0.00%, 32.44%] | 1.0000 |
| 5-7% | 80 | 11 | 13.75% | [7.85%, 22.97%] | 0.0031 |
| 7-8% | 21 | 2 | 9.52% | [2.65%, 28.91%] | 0.3039 |
| >8% | 11 | 0 | 0.00% | [0.00%, 25.88%] | 1.0000 |

## 4. Ventana operativa de M4

- **Log bruto**: 11/80 = 13.75% (IC 95% [7.85%, 22.97%]); p=0.0031 frente a 1/19 y p=1.05e-05 frente a 1/37.
- **Log depurado**: 8/47 = 17.02% (IC 95% [8.89%, 30.14%]); p=0.0029 frente a 1/19 y p=3.49e-05 frente a 1/37.

Aciertos por número predicho (log depurado):

| Número | Rondas | Aciertos | Tasa |
| --- | --- | --- | --- |
| 2 | 5 | 3 | 60.00% |
| 5 | 5 | 2 | 40.00% |
| 11 | 4 | 1 | 25.00% |
| 8 | 5 | 1 | 20.00% |
| 13 | 5 | 1 | 20.00% |
| 15 | 5 | 0 | 0.00% |
| 7 | 4 | 0 | 0.00% |
| 14 | 4 | 0 | 0.00% |
| 16 | 3 | 0 | 0.00% |
| 1 | 2 | 0 | 0.00% |
| 4 | 2 | 0 | 0.00% |
| 3 | 1 | 0 | 0.00% |
| 6 | 1 | 0 | 0.00% |
| 12 | 1 | 0 | 0.00% |

Sin el número dominante (2) la tasa cae a 11.90% (5/42), p=0.068 frente a 1/19.

## 5. Efecto del filtro de dispersión y del anclaje

- **Dispersión**: 4 rondas descartadas de 80; la tasa pasa de 13.75% a 11.84% (Δ -1.91%). el filtro de dispersión no aporta valor predictivo: elimina aciertos y reduce la tasa observada
- **Anclaje {1, 7, 8}**: 2/9 = 22.22% frente a 6/38 = 15.79% sin ancla; Fisher p=0.639. no hay evidencia estadística suficiente para escalar el stake: la diferencia no es significativa

Modos de aislamiento del modelo principal:

| Modo | Alertas | Aciertos | Tasa | p (vs 1/19) |
| --- | --- | --- | --- | --- |
| any | 76 | 9 | 11.84% | 0.0182 |
| tie | 0 | 0 | 0.00% | 1.0000 |
| strict | 0 | 0 | 0.00% | 1.0000 |

## 6. Matrices de confusión

Positivo = la alerta se dispara y acierta; negativo = la alerta se dispara y falla. Sobre el log completo la «exactitud» está dominada por los verdaderos negativos (rondas sin señal), por lo que la métrica relevante es la precisión.

| Universo | VP | FP | FN | VN | Precisión | Exhaustividad | Exactitud |
| --- | --- | --- | --- | --- | --- | --- | --- |
| log completo | 9 | 67 | 4 | 40 | 11.84% | 69.23% | 7.50% |
| ventana M4 (log completo) | 9 | 67 | 2 | 2 | 11.84% | 81.82% | 11.25% |
| log depurado | 6 | 37 | 4 | 33 | 13.95% | 60.00% | 7.50% |
| ventana M4 (log depurado) | 6 | 37 | 2 | 2 | 13.95% | 75.00% | 12.77% |

## 7. PnL simulado (apuestas planas de 1 unidad sobre las señales válidas)

| Modelo de pago | Apuestas | Aciertos | Equilibrio | PnL (u) | ROI | IC 95% ROI | DD máx. |
| --- | --- | --- | --- | --- | --- | --- | --- |
| european37:raw | 76 | 9 | 2.78% | +248.0 | +326.32% | [+89.47%, +610.53%] | 23.0 u |
| express19:raw | 76 | 9 | 5.26% | +95.0 | +125.00% | [+0.00%, +275.00%] | 23.0 u |
| european37:clean | 43 | 6 | 2.78% | +173.0 | +402.33% | [+67.44%, +820.93%] | 13.0 u |
| express19:clean | 43 | 6 | 5.26% | +71.0 | +165.12% | [-11.63%, +386.05%] | 13.0 u |

## 8. Validación cronológica fuera de muestra

| Tramo | Filas | Alertas | Aciertos | Tasa | p (vs 1/19) |
| --- | --- | --- | --- | --- | --- |
| 1 | 20 | 13 | 2 | 15.38% | 0.1472 |
| 2 | 20 | 9 | 0 | 0.00% | 1.0000 |
| 3 | 20 | 11 | 4 | 36.36% | 0.0019 |
| 4 | 20 | 10 | 0 | 0.00% | 1.0000 |
| in_sample | 40 | 22 | 2 | 9.09% | 0.3236 |
| out_of_sample | 40 | 21 | 4 | 19.05% | 0.0224 |

## 9. Sensibilidad a los umbrales

- Rango de tasas sobre 48 configuraciones con al menos 20 alertas: min 7.02%, mediana 11.94%, máx 16.00%.
- Configuración de la especificación: 11.84% (9/76), p=0.0182.

Vecindario de la ventana 5,0%–7,0% variando el tope de dispersión:

| Δ máximo | Alertas | Tasa |
| --- | --- | --- |
| 3.0% | 64 | 7.81% |
| 4.7% | 76 | 11.84% |
| 6.0% | 80 | 13.75% |
| 100.0% | 80 | 13.75% |

Vecindario con Δ fijo en 4,7% variando la ventana de probabilidad:

| Ventana | Alertas | Tasa |
| --- | --- | --- |
| (4.5%, 6.5%] | 46 | 13.04% |
| (4.5%, 7.0%] | 76 | 11.84% |
| (4.5%, 7.5%] | 78 | 11.54% |
| (4.5%, 8.0%] | 85 | 10.59% |
| (5.0%, 6.5%] | 46 | 13.04% |
| (5.0%, 7.0%] | 76 | 11.84% |
| (5.0%, 7.5%] | 78 | 11.54% |
| (5.0%, 8.0%] | 85 | 10.59% |
| (5.5%, 6.5%] | 36 | 13.89% |
| (5.5%, 7.0%] | 66 | 12.12% |
| (5.5%, 7.5%] | 68 | 11.76% |
| (5.5%, 8.0%] | 75 | 10.67% |

- La señal no depende de un único par de umbrales: la ventaja se mantiene en un entorno amplio, lo que reduce (sin eliminar) la probabilidad de que los umbrales se hayan ajustados a esta muestra concreta.

## 10. Control de multiplicidad (Holm-Bonferroni)

| Hipótesis | Observaciones | p | p (Holm) | ¿Significativo? |
| --- | --- | --- | --- | --- |
| zona <5% de la matriz completa | 25 | 1.0000 | 1.0000 | no |
| zona 5-7% de la matriz completa | 913 | 0.0641 | 0.3206 | no |
| zona 7-8% de la matriz completa | 125 | 0.7926 | 1.0000 | no |
| zona >8% de la matriz completa | 137 | 0.9773 | 1.0000 | no |
| ventana operativa de M4 | 80 | 0.0031 | 0.0187 | sí |
| efecto acelerador del anclaje | 47 | 0.6388 | 1.0000 | no |

## 11. Estado del motor de alertas en vivo

- Estimador de la ventana: 8/47 aciertos observados.
- Estimador de alertas: 6/43 aciertos observados.
- Stake de referencia (Kelly fraccional y encogido): 0.97% del bankroll (tasa usada 6.56%, equilibrio 2.78%).

## Conclusión operativa

- El identificador de sorteo [248978] reaparece después de sorteos posteriores: son predicciones obsoletas re-escritas en el log, no observaciones independientes.
- El orden de filas del log no es monótono en `no. sorteo`: el paso temporal debe derivarse del orden de fila, no del identificador de sorteo.
- 35 filas (29.2%) pertenecen a 6 grupos de predicción con resultados contradictorios: su etiqueta no es verificable y se descartan del cálculo principal.

### Veredicto por regla de la especificación

- **Se mantienen** (4): zona de ruido, zona de convergencia global, trampa estadística, tasa óptima de M4. Son las propiedades de calibración del vector de probabilidades y se replican exactamente sobre el log.
- **Requieren contexto** (1): filtro de dispersión.
- **No se sostienen** (4): aislamiento de modelo, acelerador de anclaje, ventaja base de la casa, concentración de aciertos.

### Configuración operativa recomendada

1. **Aislamiento de modelo**: la lectura literal del PDF (M4 debe emitir la probabilidad principal) produce 0 alertas: la condición y la ventana de probabilidad nunca coinciden en este histórico. La configuración utilizable es la lectura operativa (`any`), donde M4 conduce la alerta y el resto de modelos se tratan como ruido de fondo.
2. **Filtro de dispersión**: opcional. Descarta 4 rondas y 2 aciertos, y mueve la tasa de 13.75% a 11.84%. Aplicarlo o no es una decisión de riesgo, no de acierto.
3. **Anclaje [1, 7, 8]**: se conserva la escalada de confianza a «Crítico» como etiqueta operativa, pero **sin** multiplicador de stake: la diferencia observada no es estadísticamente significativa (Fisher p=0.639).
4. **Línea base de comparación**: usar 1/19 = 5,26%, la probabilidad equiprobable del espacio de resultados realmente observado, y no 1/37 = 2,70%. Bajo 1/19 la ventaja de la ventana M4 es 2.61× sobre el log bruto (13.75%) y 3.23× sobre el log depurado, no 5×.

### Magnitud del efecto y riesgo

- Sobre el log depurado la ventana M4 produce 8/47 = 17.02% con IC 95% [8.89%, 30.14%]: el intervalo es ancho y su extremo inferior roza el punto de equilibrio del modelo de pago de 19 casillas.
- La ventaja está concentrada: retirando el número 2 la tasa cae a 11.90% y el contraste frente a 1/19 deja de ser significativo (p=0.068).
- El PnL plano sobre señales válidas (european37) da +173 unidades en 43 apuestas (ROI +402.3%, IC 95% [+67.4%, +820.9%], drawdown máximo 13 u). La varianza domina al resultado: la estrategia necesita muchas más apuestas para que ese ROI sea distinguible del azar.
- El módulo de Kelly no arriesga capital mientras no se acumulen 20 observaciones y 4 aciertos en las cohortes de alerta, y limita la exposición por ronda al 2.0% del bankroll por defecto.

### Siguiente paso defendible

1. Ejecutar el motor en modo papel, con los umbrales congelados, y registrar cada alerta con su resultado antes de arriesgar capital.
2. Corregir el log de supervisión: cada fila debe corresponden a un único sorteo con una única etiqueta, o las métricas seguirán sin poder distinguir señal de artefacto de registro.
3. No reentrenar los umbrales con cada resultado: la ventaja mostrada en el vecindario de umbrales es sólida frente a ruido de estimación, pero frágil frente a re-selección de reglas.

Este informe describe lo que el histórico **permite** afirmar. Ninguna métrica de un log de 120 filas, con 35 de ellas de etiqueta no verificable, sustituye una validación acumulada fuera de muestra con meses de datos.
