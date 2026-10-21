# Guía para publicar Ruleta Express en Render

Esta guía explica cómo publicar el dashboard en Render **sin que Render
consulte Loteka nunca**, que es la forma más difícil de bloquear por parte de
Loteka.

## 1. La idea: el PC consulta, Render sirve

El bloqueo de Loteka se produce contra los rangos de IP de los centros de
datos. Vercel, Render, AWS y DigitalOcean están en ese rango. Tu conexión
residencial, no.

Por eso el proyecto se despliega así:

```text
   [ Tu PC · IP residencial ]  --scraping-->  Loteka
            |
            |  HTTPS POST /api/ingest   (token compartido)
            v
   [ Render · sólo sirve el dashboard ]  -->  navegador
```

- **Render nunca pide una página a Loteka.** Ni una sola vez. No hay nada que
  bloquear.
- **El scraper sigue en tu PC**, que es donde ya funciona y donde la IP no
  está en la lista negra.
- Los resultados viajan del PC a Render cifrados y autenticados con un token
  que sólo tú conoces.
- El dashboard, la alerta M4 y el registro de predicciones se sirven desde
  Render, que es rápido, no se duerme mientras lo miras y sobrevive a que
  cierres el portátil.

Ventaja añadida: si Render se reinicia, el PC detecta que el servicio perdió
el estado y le reenvía el histórico completo. No se pierde ni un sorteo.

## 2. Requisitos previos

- Una cuenta en [Render](https://render.com).
- Acceso al repositorio `raxcode1/RuletaExpressPredictorLoteka`.
- Python 3.11 o superior en el PC (para el script de envío).
- El token compartido que vas a generar en el paso 4.

## 3. Crear el servicio en Render

1. Entra en [dashboard.render.com](https://dashboard.render.com) y pulsa
   **New > Web Service**.
2. Conecta el repositorio de GitHub y elige la rama `master`.
3. Configura el servicio:

   | Campo | Valor |
   |---|---|
   | Name | `ruleta-express` |
   | Region | Oregon (o el más cercano a ti) |
   | Branch | `master` |
   | Root Directory | *(vacío)* |
   | Runtime | Python |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `gunicorn render_app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120` |
   | Health Check Path | `/healthz` |

   **Workers: 1.** El estado (histórico, caché del análisis y registro vivo)
   vive en memoria del proceso. Con más de un worker cada uno tendría una
   copia distinta y las respuestas no cuadrarían.

4. Pulsa **Create Web Service** y espera al primer despliegue.

## 4. Variables de entorno

En **Environment** del servicio añade:

```text
PUSH_TOKEN=<secreto largo y aleatorio>
SESSION_SECRET=<otra cadena aleatoria larga>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<contraseña larga y única>
COOKIE_SECURE=1
REFRESH_INTERVAL_SECONDS=30
```

- `PUSH_TOKEN`: es la llave que abre `/api/ingest`. Debe ser **exactamente**
  igual a la que pongas en `config_render.py` en el PC. Genera una con
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- `SESSION_SECRET`: firma la cookie de sesión (7 días).
- `ADMIN_USERNAME` / `ADMIN_PASSWORD`: acceso al dashboard.
- `COOKIE_SECURE=1`: obligatorio en Render (HTTPS). Ponlo en `0` sólo si
  pruebas en local por HTTP.
- `REFRESH_INTERVAL_SECONDS`: cada cuánto refresca el navegador. En Render no
  hace falta cada 45 segundos: los datos llegan cuando el PC empuja, así que
  30 es suficiente.

Guarda el `PUSH_TOKEN` en un lugar seguro: lo necesitarás en el paso 6.

### Disco persistente (opcional, plan de pago)

Sin disco, el servicio arranca con el CSV del repositorio y reconstruye el
registro vivo al arrancar, y los sorteos que el PC envíe durante el tiempo
que el servicio estuvo caído se recuperan en el siguiente envío completo.

Con disco, el estado sobrevive a los reinicios sin recalcular nada:

1. En el servicio, **Disks > Add Disk** (requiere un plan de pago).
2. Monta en `/var/data` y define `RENDER_DATA_DIR=/var/data`.
3. El servicio guardará allí `Resultados_RuletaExpress.csv` y
   `registro_live.json`.

En el plan gratuito no hay disco: funciona igual, sólo recalcula.

## 5. Comprobar el despliegue

```text
https://ruleta-express.onrender.com/healthz      -> {"ok": true, "rows": 28563}
https://ruleta-express.onrender.com/              -> pantalla de acceso
```

Inicia sesión con `ADMIN_USERNAME` y `ADMIN_PASSWORD`. Verás el dashboard con
el histórico del repositorio, el análisis de `state_recovery` y la alerta M4
(la primera carga tarda unos segundos: recalcula el análisis sobre 28 mil
filas).

## 6. Configurar el PC para que empuje los datos

En `config_render.py` (archivo local, no se sube):

```python
URL_RENDER = "https://ruleta-express.onrender.com"
TOKEN_RENDER = "<el mismo PUSH_TOKEN que pusiste en Render>"
```

Guarda el archivo y arranca el bucle:

```powershell
python .\sync_local_loop.py
```

El bucle ahora hace tres cosas:

1. Consulta Loteka desde tu IP residencial, con intervalo jitterizado
   (20-60 segundos).
2. Guarda los resultados nuevos en un commit local.
3. **Empuja los sorteos nuevos a Render** en cuanto aparecen.

Los envíos son incrementales: sólo viaja lo nuevo. Cada 20 ciclos (unos 15-20
minutos) se manda además el histórico completo, y también se manda completo
de inmediato si el servicio responde con menos filas de las que tiene el PC
(eso delata un reinicio del servicio).

### Probar el envío a mano

```powershell
python -c "import push_render, pandas as pd; f=pd.read_csv('Resultados_RuletaExpress.csv',dtype=str).fillna(''); print(push_render.sincronizar(f[['Fecha','Hora','No_Sorteo','Numero']].to_dict(orient='records')))"
```

## 7. Medidas anti-bloqueo

Todo lo que hace el proyecto para que Loteka no lo bloquee:

| Medida | Dónde | Efecto |
|---|---|---|
| Cero tráfico desde IPs de datacenter | `render_app.py` no consulta Loteka | No hay IP bloqueable |
| Intervalo jitterizado de 20-60 s | `config_sync.py` | Nada de cadencias fijas |
| Un solo número por consulta, con caché de 4 rondas | `sync_ruleta_express.py` | 1 petición cada ~4 sorteos |
| User-Agent honesto y descriptive | `sync_ruleta_express.py` | No se disimula un bot |
| Espera exponencial tras un fallo | `sync_local_loop.py` | Nunca se insiste en bucle |
| Pausa larga de 30 min tras 5 fallos seguidos | `sync_local_loop.py` | Se cede el paso si el origen dice basta |
| Historial en el PC como fuente de verdad | `Resultados_RuletaExpress.csv` | Un bloqueo no borra datos |

**Lo que NO hace el proyecto**, y que conviene no añadir: rotación de
User-Agent, proxies, rotación de IP, ni reintentos agresivos. Todo eso
convierte un scraping cortés en un abuso y garantiza el bloqueo.

### ¿Y si Render se usa como scraper de respaldo?

Se puede añadir un Cron Job de Render que consulte Loteka, **pero no se
recomienda**: su IP es de datacenter y es exactamente lo que Loteka bloquea.
Si alguna vez lo usas, ponle un intervalo amplio (15 minutos), un jitter
grande y que nunca compita con el PC. El diseño con el PC como única fuente
de scraping es el que resiste.

## 8. Problemas frecuentes

### El dashboard muestra «Sin conexión» o tarda

El plan gratuito de Render apaga el servicio tras 15 minutos sin tráfico. La
primera visita tras un rato tarda unos 30 segundos (arranque en frío +
recalculo del análisis). El plan de pago evita la espera.

### `/api/ingest` devuelve 401

El `PUSH_TOKEN` del PC y el de Render no coinciden. Se comparan con
`hmac.compare_digest`, así que distingue mayúsculas y espacios.

### `/api/ingest` devuelve 503

`PUSH_TOKEN` no está definido en el entorno del servicio.

### El PC no manda nada

Comprueba que `URL_RENDER` y `TOKEN_RENDER` no están vacíos en
`config_render.py` y que el bucle se reinició después de editarlos (el
script los lee al arrancar). En la salida del bucle debe verse
«Render actualizado: N sorteos».

### Los datos del panel no cuadran con el CSV

El PC es la fuente de verdad. Si Render muestra menos filas, el siguiente
envío completo lo corrige (o borra `estado_push_render.json` en el PC para
forzar el envío completo en el siguiente ciclo).

### Render se reinició y perdió los datos

No debería pasar: el siguiente push del PC detecta que el servicio tiene
menos filas y reenvía el histórico completo. Si quieres forzarlo:

```powershell
Remove-Item .\estado_push_render.json
```

## 9. Render frente a Vercel

| | Vercel | Render |
|---|---|---|
| Modelo | funciones serverless | servicio web persistente |
| Scraper | dentro de la función (IP de datacenter) | sólo en el PC (IP residencial) |
| Estado | Vercel Blob | disco o memoria, reconstruido |
| Latencia | arranque en frío | proceso vivo, 30 s de refresco |
| Riesgo de bloqueo | alto | prácticamente nulo |
| Archivos | `api/dashboard.py`, `vercel.json` | `render_app.py` |

Puedes usar los dos a la vez: Vercel para tener una URL de respaldo y Render
como principal. El bucle del PC sólo tiene que apuntar a uno.

## 10. Actualizar el sitio

Render se redespliega automáticamente con cada `git push` a `master`:

```powershell
git add .
git commit -m "Actualizar dashboard de Ruleta Express"
git push
```

Recuerda que el bucle local **no** hace `git push`: los commits de resultados
se acumulan en local y los subes cuando quieras, para no disparar despliegues
en cada sorteo.
