# Guía: Crear Cloudflare Worker para evitar bloqueos de Loteka

## ¿Por qué usar Cloudflare Worker?

Loteka bloquea las IPs de centros de datos (Vercel, Render, AWS). Con Cloudflare Worker, las peticiones salen desde las IPs de Cloudflare, que son menos propensas a ser bloqueadas.

## Paso 1: Instalar Wrangler CLI

Necesitas tener Node.js instalado. Luego instala Wrangler:

`ash
npm install -g wrangler
`

O usar npx sin instalar globalmente.

## Paso 2: Autenticarse con Cloudflare

`ash
wrangler login
`

Se abrirá una ventana del navegador para autenticarte con tu cuenta de Cloudflare.

## Paso 3: Desplegar el Worker

Desde la carpeta del proyecto:

`ash
cd "C:\Users\diose\OneDrive\Desktop\Ruleta Express"
wrangler deploy
`

O para el entorno de producción:

`ash
wrangler deploy --env production
`

Al terminar, te mostrará la URL del worker, algo como:
`
https://ruleta-express-proxy.tu-cuenta.workers.dev
`

## Paso 4: Configurar la URL del Worker

Copia la URL que te dio Wrangler y configúrala en tu entorno.

### Para Vercel:
1. Ve a tu proyecto en Vercel > Settings > Environment Variables
2. Agrega: CLOUDFLARE_WORKER_URL = https://ruleta-express-proxy.tu-cuenta.workers.dev
3. Redespliega el proyecto

### Para Render:
1. En tu Web Service y Background Worker, agrega la variable de entorno
2. CLOUDFLARE_WORKER_URL = https://ruleta-express-proxy.tu-cuenta.workers.dev

### Para desarrollo local:
Crea un archivo .env con:
`
CLOUDFLARE_WORKER_URL=https://ruleta-express-proxy.tu-cuenta.workers.dev
`

## Paso 5: Configurar el dominio personalizado (opcional)

Si quieres usar tu propio dominio con el Worker:

1. Ve a Cloudflare Dashboard > Workers & Pages > tu worker
2. Settings > Triggers > Custom Domains
3. Agrega tu dominio

## Paso 6: Probar

Prueba que el worker funcione:
`ash
curl https://ruleta-express-proxy.tu-cuenta.workers.dev
`

Deberías ver el HTML de loteka.com.do

## Notas importantes

- El worker copia headers como User-Agent, Accept, etc.
- Soporta peticiones condicionales (If-None-Match, If-Modified-Since) para aprovechar el cache
- Tiene CORS habilitado con Access-Control-Allow-Origin: *
- Las IPs de Cloudflare son compartidas, pero suelen funcionar mejor que las de Vercel/Render
- Si sigue bloqueado, considera usar también proxies residenciales combinados con el worker
