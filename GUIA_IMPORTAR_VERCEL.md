# Guía para importar Ruleta Express en Vercel

Esta guía explica cómo importar este repositorio en Vercel y activar el
dashboard web, la función serverless y la persistencia del histórico mediante
Vercel Blob.

## 1. Requisitos previos

Necesitas:

- Una cuenta en [Vercel](https://vercel.com).
- Acceso al repositorio de GitHub
  `raxcode1/RuletaExpressPredictorLoteka`.
- Un repositorio conectado a Vercel mediante GitHub.
- Un navegador para comprobar el dashboard después del despliegue.

El proyecto ya incluye:

- `web/`: interfaz estática.
- `api/dashboard.py`: función serverless.
- `vercel.json`: rutas del frontend.
- `requirements.txt`: dependencias Python.
- `Resultados_RuletaExpress.csv`: histórico inicial.

## 2. Importar el repositorio

1. Entra en [vercel.com/new](https://vercel.com/new).
2. Selecciona **Continue with GitHub** si todavía no has conectado GitHub.
3. Busca `RuletaExpressPredictorLoteka`.
4. Pulsa **Import**.
5. En la configuración del proyecto usa estos valores:

   | Campo | Valor |
   |---|---|
   | Project Name | El nombre que prefieras |
   | Framework Preset | `Other` |
   | Root Directory | `./` |
   | Build Command | Déjalo vacío |
   | Output Directory | Déjalo vacío |
   | Install Command | Déjalo vacío |

6. No cambies el nombre de las carpetas `api` y `web`.
7. Todavía no pulses **Deploy** si aún no has configurado Blob.

Vercel detectará `api/dashboard.py` como una función serverless Python y
publicará los archivos de `web/` mediante las reglas de `vercel.json`.

## 3. Crear Vercel Blob

El CSV no debe modificarse directamente dentro del sistema de archivos de una
función serverless, porque ese almacenamiento no es persistente. El proyecto
usa Vercel Blob para guardar los resultados nuevos.

1. En el proyecto de Vercel abre la pestaña **Storage**.
2. Selecciona **Create Database** o **Create Storage**.
3. Elige **Blob**.
4. Crea un Blob Store, por ejemplo `ruleta-express-results`.
5. Conecta el Blob Store al proyecto.
6. Activa los entornos donde se usará:
   - **Production**
   - **Preview**, si quieres probar deployments preview
   - **Development**, solo si deseas probar Blob localmente

Al conectar el store, Vercel debe crear la variable de entorno:

```text
BLOB_READ_WRITE_TOKEN
```

El token debe existir en el entorno donde se ejecute la función. No lo
escribas en el código, no lo subas a GitHub y no lo incluyas en archivos
`.env` versionados.

## 4. Configurar la variable de entorno

Comprueba en **Project Settings > Environment Variables** que
`BLOB_READ_WRITE_TOKEN` esté configurada para **Production**. También puedes
marcar **Preview** y **Development**.

Si la variable no aparece:

1. Abre el Blob Store.
2. Selecciona **Connect Project**.
3. Elige este proyecto.
4. Habilita el entorno requerido.
5. Guarda los cambios.

Después de agregar o modificar una variable debes crear un nuevo deployment.
Los deployments anteriores no reciben automáticamente las variables nuevas.

## 5. Desplegar

Desde el panel de Vercel pulsa **Deploy**. También puedes hacerlo mediante la
CLI desde la carpeta del proyecto:

```powershell
npx vercel login
npx vercel link
npx vercel --prod
```

Si la CLI pregunta por valores:

- Selecciona la cuenta o equipo correcto.
- Confirma el repositorio/proyecto enlazado.
- Usa la carpeta actual como **Root Directory**.
- No configures un comando de build para este proyecto.

## 6. Comprobar la aplicación

Cuando el deployment termine, abre el dominio asignado por Vercel:

```text
https://TU-PROYECTO.vercel.app
```

Comprueba también directamente la API:

```text
https://TU-PROYECTO.vercel.app/api/dashboard
```

La respuesta debe ser JSON e incluir, como mínimo:

- `results`
- `recovery`
- `sync_error`
- `recovery_error`

En la página principal verifica que:

1. Aparezca el histórico guardado.
2. Se muestre el último número.
3. Aparezca el panel `State recovery`.
4. El estado indique `Sincronización activa · cada 15 s`.
5. No haya un error de Blob o de la API.

El navegador consulta la API cada 15 segundos. Vercel no mantiene un proceso
Python ejecutándose continuamente; cada consulta activa la función serverless,
que consulta Loteka y guarda el CSV actualizado en Blob.

## 7. Primer uso del histórico

Si `Resultados_RuletaExpress.csv` todavía no existe en Blob, la función usa el
CSV incluido en el repositorio como base inicial. Cuando consulta Loteka y
encuentra resultados, guarda la versión combinada y deduplicada en Blob.

Los siguientes deployments seguirán utilizando el archivo guardado en Blob,
por lo que los resultados no dependen del sistema de archivos temporal de
Vercel.

## 8. Problemas frecuentes

### `Falta la variable de entorno BLOB_READ_WRITE_TOKEN`

Conecta el Blob Store al proyecto y activa el entorno correcto. Después crea
un nuevo deployment.

### La página carga, pero `/api/dashboard` devuelve `500`

Revisa **Deployments > Functions > Logs**. Las causas más comunes son:

- El token de Blob no está disponible en Production.
- El Blob Store está conectado a otro proyecto.
- El histórico tiene columnas diferentes de `Fecha`, `Hora`, `No_Sorteo` y
  `Numero`.
- Loteka no está disponible temporalmente.

### No aparece un resultado nuevo inmediatamente

La actualización depende de dos intervalos:

1. El navegador consulta la API cada 15 segundos.
2. Loteka debe haber publicado el nuevo sorteo.

Recarga la página y revisa `sync_error` en `/api/dashboard` si tarda más de
unos minutos.

### El deployment intenta usar otro framework

En **Project Settings > Build and Deployment**, selecciona `Other`, deja
vacíos los comandos de build y verifica que **Root Directory** sea la raíz del
repositorio.

## 9. Seguridad y operación

- Nunca publiques `BLOB_READ_WRITE_TOKEN`.
- No permitas que el frontend reciba el token.
- Revisa los logs si aparecen errores repetidos de sincronización.
- Vercel Blob es la fuente persistente del histórico en producción.
- El análisis estadístico es informativo y no garantiza el siguiente número.

## 10. Actualizar el sitio

Después de cambiar el código:

```powershell
git add .
git commit -m "Actualizar dashboard de Ruleta Express"
git push
```

Si el repositorio está conectado a Vercel, cada `push` iniciará un nuevo
deployment automáticamente. Para producción también puedes ejecutar:

```powershell
npx vercel --prod
```

