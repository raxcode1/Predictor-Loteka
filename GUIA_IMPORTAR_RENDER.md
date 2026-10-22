# Guía para publicar Ruleta Express en Render (100% automático)

Render monta **todo** el sistema: un *worker* que consulta Loteka y un
*servicio web* que publica el dashboard. No hace falta que tu PC esté
encendido.

El reto es que Render usa IPs de centro de datos, que es justo lo que
Loteka bloquea. La solución no es un truco, son cuatro capas que juntas
hacen que el scraping sea indistinguible del de un visitante puntual.

```text
   [ Worker en Render ]  --jitter + ETag + proxy-->  Loteka
            |
            |  POST /api/ingest  (token)
            v
   [ Web Service en Render ]  -->  navegador (dashboard + alerta M4)
```

## 1. Las cuatro capas anti-bloqueo

| Capa | Qué hace | Por qué ayuda |
|---|---|---|
| **1. Proxy de IP residencial** | El worker sale a internet por una IP de casa, no por la de Render | La IP que ve Loteka no es una IP de datacenter. Si esa IP se bloquea, el worker rota a la siguiente del conjunto y sigue trabajando |
| **2. Jitter** | El intervalo se sortea en una ventana (por defecto 60-180 s) | Un patrón fijo es lo que disparan los detectores de bots. Nunca hay cadencia |
| **3. Petición condicional (ETag)** | Se manda `If-None-Match`; si la página no cambió, el servidor responde `304` sin cuerpo | El sitio no sirve ni sirve el HTML. Es exactamente lo que hace un navegador al recargar |
| **4. Interruptor de circuito** | Tras un fallo: 2, 5, 10, 20, 30 minutos de pausa, **persistidos en disco** | No se insiste nunca. Y como la pausa está en disco, un reinicio del worker no la reinicia |

Además, el scraping va **separado** del dashboard: si un día bloquean la IP
del worker, el dashboard sigue sirviendo y mostrando los últimos datos
conocidos, y el worker vuelve a intentarlo solo cuando pasa la pausa.

## 2. Requisitos

- Una cuenta en [Render](https://render.com).
- Acceso al repositorio `raxcode1/RuletaExpressPredictorLoteka`.
- **Un proxy de IP residencial** (recomendado). Sin él el sistema funciona,
  pero es bloqueable. Ver la sección 6.

## 3. Crear el servicio web (dashboard)

1. [dashboard.render.com](https://dashboard.render.com) → **New > Web Service**.
2. Conecta el repositorio, rama `master`.
3. Configuración:

   | Campo | Valor |
   |---|---|
   | Name | `ruleta-express` |
   | Runtime | Python |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `gunicorn render_app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120` |
   | Health Check Path | `/healthz` |

   **Workers: 1**, porque el estado vive en la memoria del proceso.

4. Variables de entorno (**Environment**):

```text
PUSH_TOKEN=<secreto largo y aleatorio>
SESSION_SECRET=<otra cadena aleatoria larga>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<contraseña larga y única>
COOKIE_SECURE=1
REFRESH_INTERVAL_SECONDS=30
RENDER_DATA_DIR=/var/data        # sólo si montas disco (ver sección 7)
```

5. **Create Web Service**. Anota la URL (ej. `https://ruleta-express.onrender.com`).

## 4. Crear el worker (scraping automático)

1. **New > Background Worker** (no Cron Job: el worker debe vivir siempre
   activo, un cron de un minuto se bloquea por justa causa y consume cuota).
2. Mismo repositorio y rama. Configuración:

   | Campo | Valor |
   |---|---|
   | Name | `ruleta-scraper` |
   | Runtime | Python |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `python render_worker.py` |

3. Variables de entorno del worker:

```text
WEB_SERVICE_URL=https://ruleta-express.onrender.com
PUSH_TOKEN=<el mismo PUSH_TOKEN que el del servicio web>
SCRAPER_INTERVAL_MIN=60
SCRAPER_INTERVAL_MAX=180
SCRAPER_BACKOFF_MAX_MIN=30
SCRAPER_PROXY_URLS=http://usuario:clave@host1:port,http://usuario:clave@host2:port
RENDER_DATA_DIR=/var/data
```

   - `SCRAPER_INTERVAL_MIN/MAX`: ventana del jitter. 60-180 s es un ritmo
     emulado al de un visitante casual y muy por debajo de lo que un
     limiter consideraría abusivo. **No los bajes de 30 s.**
   - `SCRAPER_PROXY_URLS`: lista de proxies separados por comas. El worker
     rota al siguiente cuando uno falla. Si se deja vacío, sale directo con
     la IP de Render (funciona, pero es lo bloqueable).

4. **Create Background Worker**.

## 5. Probar que funciona

```text
https://ruleta-express.onrender.com/healthz
```

Debe devolver `{"ok": true, "rows": 28563}`. Entra en la web con
`ADMIN_USERNAME` y `ADMIN_PASSWORD`.

En los **logs del worker** debe aparecer cada uno o dos minutos:

```text
Consulta ok · publicados=204 · histórico=28563 (+1)
Enviado a https://ruleta-express.onrender.com: 28564 sorteos, último No. 249493.
```

Si ves `304 sin cambios`, la capa condicional está funcionando: el sitio no
sirvió el HTML porque no había nada nuevo.

## 6. El proxy de IP residencial (lo importante)

Sin proxy, la petición sale desde la IP de Render. Con proxy, sale desde una
IP residencial: para el sitio es indistinguible de un visitante más.

Cualquier servicio de scraping con IPs residenciales sirve (ScraperAPI,
Zyte, Apify, o un proxy doméstico propio). Lo único que necesita el proyecto
es una URL con usuario y clave:

```text
http://usuario:clave@host:puerto
```

- Con **uno solo**: el worker sale por esa IP. Si la bloquean, entra la pausa
  de 30 minutos y vuelve a intentarlo. Es un mal día, no una caída.
- Con **varios** (`SCRAPER_PROXY_URLS` con comas): el worker rota al
  siguiente cuando uno falla. Un bloqueo sólo mata una dirección. Esto es lo
  que hace que el sistema sea **difícil de bloquear**: no hay una sola IP que
  pueda caerse.

Genera los tokens con:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## 7. Disco persistente (opcional, plan de pago)

Sin disco, el worker arranca con el CSV del repositorio y el servicio web
reconstruye el registro vivo al calcular. Los sorteos que se pierdan durante
un reinicio los recupera el envío completo que el worker detecta y repite.

Con disco, en cada servicio: **Disks > Add Disk**, montar en `/var/data` y
definir `RENDER_DATA_DIR=/var/data`. El worker guarda ahí el histórico y su
estado de pausas; el servicio web, el registro vivo.

## 8. Tu PC deja de ser necesario

Con el worker desplegado, el bucle del PC es **opcional**. Sigue siendo útil
como red de seguridad:

- Si el worker se queda bloqueado, el PC (IP residencial, sin proxy)
  sigue alimentando el dashboard.
- El PC hace commits locales del CSV, así que el histórico nunca depende de
  la nube.

Si lo mantienes activo, configura `config_render.py` con la URL y el token
del servicio web, y ejecuta:

```powershell
python .\sync_local_loop.py
```

El bucle empuja los sorteos al mismo endpoint `/api/ingest`. Si el worker y
el PC empujan a la vez, da igual: la ingesta deduplica por número de sorteo.

## 9. Problemas frecuentes

### El worker se queda dormido

El plan gratuito de Render apaga las instancias tras 15 minutos sin
actividad. El worker nunca está inactivo (consulta cada 1-3 minutos), pero
si lo ves apagado, el plan de pago lo evita. Tras despertarse, el primer
ciclo tarda lo que le reste de la ventana de jitter.

### `Pausa activa: N s restantes` en los logs

Es el interruptor de circuito funcionando: el scraping falló y el worker está
esperando antes de reintentar. No es un error del proyecto; es la protección
contra insistir. Si dura más de 30 minutos, revisa que el proxy funciona
(probable causa: proxy caducado o credenciales mal).

### El dashboard no actualiza

1. Mira los logs del worker: ¿hay `Consulta ok`? Si no, el problema es de
   red hacia Loteka.
2. ¿Hay `Enviado a ...`? Si no, el problema es del worker hacia el servicio
   web: revisa `WEB_SERVICE_URL` y `PUSH_TOKEN`.
3. Si ambos están bien, el servicio web habrá merged los datos: comprueba
   `/healthz` y que `rows` crezca.

### `/api/ingest` devuelve 401

El `PUSH_TOKEN` del worker y el del servicio web no coinciden (se comparan
con `hmac.compare_digest`: distingue mayúsculas y espacios).

### El proxy no funciona

Prueba el proxy con `curl` antes de culpar al worker:

```powershell
curl.exe -x "http://usuario:clave@host:puerto" https://loteka.com.do/ -o nul -w "%{http_code}"
```

Debe devolver `200`.

## 10. Lo que este proyecto no hace

Y no conviene añadir: rotación de User-Agent, resolución DNS exótica,
captcha solving ni reintentos agresivos. Todo eso convierte un scraping
cortés en un abuso y garantiza el bloqueo. El objetivo aquí es **volumen bajo
y patrón natural**, no camuflarse.

## 11. Render frente a Vercel

| | Vercel | Render (worker + web) |
|---|---|---|
| Automático sin PC | sí (scraping desde datacenter) | sí (scraping desde worker, con proxy opcional) |
| Scraper | dentro de la función | worker dedicado, con pausa persistida |
| Resiliencia a un bloqueo | la función sigue fallando | el dashboard sobrevive; el worker reintenta |
| Estado | Vercel Blob | disco o memoria, reconstruido |
| Ritmo de refresco | 45 s | 30 s |

Puedes usar los dos a la vez: Vercel como URL de respaldo y Render como
principal.

## 12. Actualizar el sitio

Render redespliega con cada `git push` a `master`:

```powershell
git add .
git commit -m "Actualizar dashboard de Ruleta Express"
git push
```

El bucle local **no** hace `git push`: los commits de resultados se acumulan
en local y los subes cuando quieras, para no disparar despliegues en cada
sorteo.




