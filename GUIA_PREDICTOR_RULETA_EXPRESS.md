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
