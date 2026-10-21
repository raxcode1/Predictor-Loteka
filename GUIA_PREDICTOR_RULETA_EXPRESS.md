# Guía del predictor de Ruleta Express

Este proyecto recopila resultados públicos de Ruleta Express, los almacena en
un CSV y ejecuta modelos estadísticos de caja negra para evaluar si existe
alguna señal reproducible en la secuencia.

El sistema no recupera una semilla real automáticamente. Una semilla solo puede
recuperarse si el generador y el mapeo de estados son compatibles con los datos
observados. El CSV contiene resultados truncados y puede incluir reglas de
postprocesamiento, como anti-repetición.

## Archivos

- `Resultados_RuletaExpress.csv`: histórico local de resultados.
- `sync_ruleta_express.py`: sincronizador de resultados públicos.
- `state_recovery.py`: diagnóstico, backtesting, ranking estadístico y búsqueda
  SMT opcional.
- `web_app.py`: sitio web local, API del dashboard y sincronización automática.
- `web/`: interfaz del dashboard.
- `api/dashboard.py`: función serverless para Vercel.
- `vercel.json`: rutas del sitio estático.
- `GUIA_IMPORTAR_VERCEL.md`: guía paso a paso para importar y configurar el
  proyecto en Vercel.
- `m4_engine/`: motor cuantitativo M4 (alertas de alta ventaja, validación
  empírica y stake dinámico).
- `ejemplo_alertas_en_vivo.py`: demostración del motor M4 ronda a ronda.
- `verificar_m4_engine.py`: comprobaciones automáticas del motor M4.
- `informe_validacion_m4.md` y `informe_validacion_m4.json`: última validación
  sobre el log de supervisión.

## Requisitos

Instalar las dependencias:

```powershell
python -m pip install pandas z3-solver
```

Si aparece `ImportError: cannot import name 'BitVec' from 'z3'`, hay un
paquete distinto llamado `z3` instalado y está ocultando `z3-solver`.
Corrígelo con:

```powershell
python -m pip uninstall -y z3
python -m pip install --force-reinstall z3-solver
```

El sincronizador usa únicamente la biblioteca estándar y `pandas`. El análisis
SMT requiere `z3-solver`.

## Sincronizar resultados una vez

Desde la carpeta del proyecto:

```powershell
python .\sync_ruleta_express.py
```

El script:

1. Consulta la página pública de Loteka.
2. Localiza la sección de Ruleta Express.
3. Extrae fecha, hora, número de sorteo y resultado.
4. Deduplica usando `No_Sorteo`.
5. Ordena el CSV por número de sorteo descendente.
6. Coloca los sorteos más recientes al principio del archivo.

Una ejecución repetida no debe crear duplicados.

## Mantener la sincronización activa

Puedes elegir cualquiera de estos intervalos:

```powershell
# Consultar cada 60 segundos
python .\sync_ruleta_express.py --watch 60

# Consultar cada 30 segundos
python .\sync_ruleta_express.py --watch 30

# Consultar cada 15 segundos
python .\sync_ruleta_express.py --watch 15
```

El intervalo mínimo aceptado es de 15 segundos. Un intervalo menor reduce el
tiempo de detección después de que Loteka publique un resultado, pero no
adelanta la publicación de los sorteos.

El modo `--watch` solo recopila resultados. No realiza apuestas ni interactúa
con controles de juego.

## Ejecutar sincronización automática cada 15 segundos desde tu PC (recomendado)

Como Loteka bloquea las peticiones desde los servidores de Vercel, la forma más
rápida de mantener los datos actualizados es ejecutar el sincronizador desde tu
PC (tu IP residencial no está bloqueada).

### 1. Crear el archivo de entorno

Copia `.env.example` a `.env` y rellena los valores:

```powershell
Copy-Item .env.example .env
```

Edita `.env` con tus credenciales:

- `SESSION_SECRET`: cualquier cadena larga y aleatoria para firmar las sesiones.
- `ADMIN_USERNAME`: usuario para entrar al dashboard (ej. `admin`).
- `ADMIN_PASSWORD`: contraseña larga y única.
- `BLOB_READ_WRITE_TOKEN`: opcional, solo si usas Vercel Blob para guardar los
  resultados en la nube.

### 2. Ejecutar el bucle de sincronización

```powershell
python .\sync_local_loop.py
```

Este script:

1. Ejecuta `sync_ruleta_express.py` cada 15 segundos.
2. Si hay resultados nuevos, hace `commit` y `push` automático a GitHub.
3. Ese `push` dispara un despliegue automático en Vercel.
4. La página web muestra los datos actualizados en cuanto termina el despliegue.

Deja el script ejecutándose en una terminal mientras quieras que los datos se
mantengan actualizados. Deténlo con `Ctrl+C`.

> **Advertencia:** 15 segundos es muy frecuente. Si Loteka termina bloqueando tu
> IP residencial, sube el intervalo en `sync_local_loop.py` (cambia
> `INTERVAL = 15` por `INTERVAL = 60` o más).

El modo `--watch` solo recopila resultados. No realiza apuestas ni interactúa
con controles de juego.

## Abrir el sitio web

```powershell
python .\web_app.py
```

Luego abre <http://127.0.0.1:8000>. El sitio muestra el CSV guardado, consulta
Loteka automáticamente cada 15 segundos y recalcula el análisis estadístico de
`state_recovery` cuando detecta un nuevo número de sorteo. El dashboard se
detiene con `Ctrl+C`.

## Publicar en Vercel

Para una guía completa de importación desde GitHub, configuración de Blob,
variables de entorno y comprobación del deployment, consulta
[GUIA_IMPORTAR_VERCEL.md](./GUIA_IMPORTAR_VERCEL.md).

La versión para Vercel usa una función serverless y Vercel Blob. No se puede
mantener `web_app.py` ejecutándose como proceso persistente en Vercel.

1. Crea un Blob Store en el proyecto de Vercel.
2. Añade la variable `BLOB_READ_WRITE_TOKEN` al entorno de producción.
3. Publica el repositorio:

   ```powershell
   npx vercel
   ```

4. Para producción:

   ```powershell
   npx vercel --prod
   ```

El navegador consulta `/api/dashboard` cada 15 segundos. Cada consulta obtiene
los resultados públicos, los deduplica y guarda el CSV actualizado en Vercel
Blob. El análisis `state_recovery` se recalcula cuando cambia el sorteo más
reciente. El archivo CSV incluido en el repositorio se usa como datos iniciales
si el Blob todavía no existe.

## Ejecutar el predictor

```powershell
python .\state_recovery.py
```

Esta ejecución:

- Lee todo el CSV.
- Convierte el orden del archivo, que es más reciente a más antiguo, en orden
  cronológico.
- Mide repeticiones adyacentes.
- Calcula un ranking heurístico basado en transiciones y frecuencia reciente.
- Compara modelos Markov de orden 1, 2 y 3.
- Prueba diferentes niveles de suavizado.
- Prueba el histórico completo y una ventana reciente.
- Evalúa una variante con restricción anti-repetición.
- Selecciona el modelo con menor `log-loss` en un holdout temporal.
- Muestra candidatos para el siguiente resultado.

En el dashboard web se muestran por separado el ranking heurístico y el
ranking Markov seleccionado.

También se conserva un historial de predicciones. Cada predicción queda
asociada al sorteo que sirvió como base y, cuando aparece el siguiente
resultado, se indica si el número estaba en el ranking Markov, en el
heurístico o en ambos, junto con su posición.

Si Vercel estuvo pausado, los sorteos que no pudieron observarse se registran
como una pausa. Sus predicciones quedan como `omitida_pausa` y no se cuentan
como aciertos ni fallos.

## Interpretar el resultado

El predictor muestra varias métricas:

- `top1`: porcentaje de veces que el candidato principal fue correcto.
- `top3`: porcentaje de veces que el resultado real estuvo entre los tres
  candidatos principales.
- `top5`: porcentaje de veces que estuvo entre los cinco principales.
- `log-loss`: penaliza las probabilidades mal calibradas; menor es mejor.

Como hay 19 resultados posibles, la línea base uniforme es:

```text
top-1: 1/19 = 5,26%
top-3: 3/19 = 15,79%
top-5: 5/19 = 26,32%
```

La métrica debe compararse contra esas líneas base usando datos que no se
utilizaron para ajustar el modelo. Un ranking de cinco números puede contener
el resultado real con frecuencia incluso sin tener capacidad predictiva útil.

Una señal sería creíble únicamente si:

1. Supera la línea base durante un período largo.
2. Mantiene la ventaja en datos nuevos.
3. Tiene un `log-loss` inferior al modelo uniforme.
4. No depende de cambiar el modelo después de observar cada resultado.

## Búsqueda SMT de semillas

La búsqueda de semillas está separada porque puede ser lenta y no debe bloquear
el predictor estadístico:

```powershell
python .\state_recovery.py --state-recovery
```

Prueba actualmente:

- LCG con parámetros de glibc.
- LCG compatible con `java.util.Random`.
- Diferentes desplazamientos (`shift`).
- Restricciones sobre los primeros 50 resultados cronológicos.

El resultado `sat` indica que el modelo tiene una solución bajo las hipótesis
probadas. Eso no basta para considerar la semilla válida: debe predecir
correctamente resultados posteriores que no participaron en el ajuste.

El resultado `unsat` indica que no existe una semilla compatible con el modelo
probado. El resultado `unknown` indica que Z3 agotó el tiempo configurado sin
poder decidir.

## Limitaciones conocidas

El análisis actual observó una tasa de repeticiones adyacentes muy inferior a la
esperada para 19 resultados independientes. Esto puede indicar una regla
anti-repetición o postprocesamiento. En ese caso, el valor publicado no es
necesariamente una salida directa del estado interno del PRNG.

Con solo el CSV no se puede distinguir de forma fiable entre:

- Un PRNG local con semilla.
- Un generador criptográfico.
- Un resultado producido por un servidor.
- Una secuencia precomputada.
- Un PRNG seguido de filtros o reglas de negocio.

Por ese motivo, los porcentajes son rankings estadísticos y no garantías de
acierto. El proyecto no debe utilizarse para asumir que un número concreto será
el siguiente ni para automatizar apuestas.

## Flujo recomendado

1. Ejecutar el sincronizador:

   ```powershell
   python .\sync_ruleta_express.py
   ```

2. Ejecutar el predictor:

   ```powershell
   python .\state_recovery.py
   ```

3. Guardar los resultados reales posteriores en el CSV mediante el sincronizador.

4. Repetir el backtesting sin cambiar manualmente el modelo por cada resultado.

5. Considerar que existe una mejora solo si las métricas fuera de muestra
   superan de forma estable las líneas base.

## Motor de alertas M4

El paquete `m4_engine` implementa la estrategia descrita en la «Especificación
de Estrategia Cuantitativa» (PDF) como código auditable: carga el log de
supervisión, reproduce los hallazgos del documento, los contrasta con pruebas
estadísticas y expone un motor de alertas que funciona ronda a ronda.

### Validar la estrategia

```powershell
python -m m4_engine --output informe_validacion_m4.md --json informe_validacion_m4.json
```

Opciones útiles:

- `--primary-mode any|tie|strict`: cómo se exige que M4 sea el modelo
  principal. `any` conduce la alerta con M4 y trata el resto de modelos como
  ruido de fondo; `tie` exige que M4 alcance la probabilidad máxima de la
  ronda; `strict` exige que sea el único máximo. La lectura literal del PDF es
  `strict`.
- `--probability-min`, `--probability-max`, `--max-spread`: umbrales del núcleo
  de la especificación.
- `--payout european37|express19`: modelo de pago usado para el dimensionamiento
  del stake. `european37` es el supuesto del PDF (37 casillas, 35:1, ventaja de
  la casa 2,70%); `express19` corresponde al espacio de resultados realmente
  observado (0..18, 18:1, equilibrio 5,26%).

El informe incluye la auditoría de integridad del log, el contraste de cada
afirmación del PDF, las zonas de probabilidad, las matrices de confusión, el PnL
simulado con apuestas planas, la validación cronológica fuera de muestra, el
mapa de sensibilidad de umbrales y el control de multiplicidad.

### Verificar el motor

```powershell
python .\verificar_m4_engine.py
```

Ejecuta 57 comprobaciones sin dependencias externas: carga del CSV,
reproducción de los números del PDF, integridad del log, estadística (Wilson,
binomial, Fisher, Holm-Bonferroni), módulo de Kelly, motor de alertas,
ausencia de look-ahead y coherencia de las métricas del informe.

### Usar el motor en tiempo real

```python
from m4_engine import AlertEngine, KellyConfig, StrategySpec, EUROPEAN_37

engine = AlertEngine(
    spec=StrategySpec(primary_mode="any"),
    payout=EUROPEAN_37,
    kelly_config=KellyConfig(fraction=0.25, max_fraction=0.02),
    bankroll=1000.0,
)

decision = engine.evaluate({
    "draw_id": 249408,
    "last_number": 7,
    "numbers": {"M1": 3, "M2": 11, "M3": 16, "M4": 9, "M5": 0,
                "H1": 14, "H2": 5, "H3": 8, "H4": 12, "H5": 17},
    "probabilities": {"M1": 0.0640, "M2": 0.0610, "M3": 0.0595, "M4": 0.0570,
                      "M5": 0.0555, "H1": 0.0545, "H2": 0.0535, "H3": 0.0530,
                      "H4": 0.0525, "H5": 0.0520},
})

if decision.fired:
    print(decision.number, decision.confidence, decision.stake.stake_fraction)

engine.observe(decision, resultado_real)  # cierra el ciclo tras el sorteo
```

`evaluate` no usa el resultado de la ronda: sólo `observe` actualiza los
estimadores, de modo que el dimensionamiento del stake se basa siempre en
evidencia ya resuelta.

La demostración completa está en:

```powershell
python .\ejemplo_alertas_en_vivo.py
```

### Stake dinámico (Criterio de Kelly defensivo)

`KellyStakeSizer` no aplica Kelly pleno. Encadena cinco cautelas:

1. Encogimiento bayesiano de la tasa de acierto hacia el punto de equilibrio
   del modelo de pago.
2. Uso del extremo inferior del intervalo de Wilson en lugar del estimador
   puntual.
3. Kelly fraccional (25% por defecto).
4. Exigencia de ventaja mínima frente al equilibrio.
5. Tope duro de exposición por ronda y comprobación de que el crecimiento
   esperado del logaritmo de la riqueza sigue siendo positivo.

Mientras no se acumulen suficientes observaciones y aciertos en la cohorte de
alertas, el motor no arriesga capital: devuelve `stake = 0`.

### Qué permite y qué no permite el histórico actual

El informe de validación sobre `registros_supervision_probs_120` concluye que:

- La calibración por zonas del PDF se reproduce exactamente: 0% por debajo del
  5%, 6,46% entre 5% y 7%, 2,19% por encima del 8%, y 13,75% para M4 dentro de
  la ventana operativa.
- La lectura literal del «aislamiento de modelo» no emite ninguna alerta: M4
  alcanza la probabilidad máxima en seis rondas y en ninguna de ellas P(M4) cae
  dentro de la ventana 5,0%–7,0%.
- El filtro de dispersión (Δ ≤ 4,7%) descarta cuatro rondas de la ventana,
  entre ellas dos aciertos, y reduce la tasa observada de 13,75% a 11,84%.
- El acelerador de anclaje {1, 7, 8} no es estadísticamente significativo
  (Fisher p = 0,64): se conserva la etiqueta «Crítico», no el aumento de stake.
- La línea base correcta es 1/19 = 5,26%, no 1/37 = 2,70%: la ventaja real es
  de 2,6 veces, no de 5 veces.
- El 29% del log tiene etiquetas no verificables (predicciones reescritas con
  resultados contradictorios) y debe excluirse de cualquier cálculo.
- La ventaja está concentrada en el número 2: sin él, la tasa cae a 11,90% y
  deja de ser significativa frente a 1/19.

Por tanto, la estrategia debe operarse en modo papel, con los umbrales
congelados, acumulando evidencia fuera de muestra antes de comprometer capital.
