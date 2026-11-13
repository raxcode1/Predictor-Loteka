// Cloudflare Worker para hacer proxy de loteka.com.do
// Esto evita que tu sitio sea bloqueado directamente por Loteka

// CORS restringido: sólo se devuelve Access-Control-Allow-Origin a los
// orígenes listados en la variable ALLOWED_ORIGINS (separados por comas).
// Por defecto no se emite (el uso servidor-a-servidor no necesita CORS).
export function corsOrigin(request, env) {
  const origin = request.headers.get('Origin');
  if (!origin) return null;
  const allowed = String((env && env.ALLOWED_ORIGINS) || '')
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean);
  return allowed.includes(origin) ? origin : null;
}

export default {
  // Latido sin sesiones: el Cron Trigger llama al panel para que sincronice y
  // registre la predicción en papel aunque nadie tenga la web abierta.
  // Requiere las variables CRON_URL (https://tu-app.vercel.app/api/cron) y
  // CRON_SECRET (la misma que en Vercel). Sin ellas no hace nada.
  async scheduled(event, env, ctx) {
    if (!env || !env.CRON_URL || !env.CRON_SECRET) return;
    const latido = fetch(env.CRON_URL, {
      headers: { Authorization: 'Bearer ' + env.CRON_SECRET, 'User-Agent': 'ruleta-express-cron' },
    }).catch(() => null);
    if (ctx && ctx.waitUntil) ctx.waitUntil(latido);
    else await latido;
  },
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    
    // Construir URL para loteka.com.do
    const lotekaUrl = new URL('https://loteka.com.do' + url.pathname + url.search);
    
    // Copiar headers relevantes
    const headers = new Headers();
    headers.set('User-Agent', request.headers.get('User-Agent') || 
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');
    headers.set('Accept', request.headers.get('Accept') || 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8');
    headers.set('Accept-Language', request.headers.get('Accept-Language') || 'es-DO,es;q=0.9,en;q=0.8');
    
    // Copiar headers condicionales si existen (para ETag/If-Modified-Since)
    if (request.headers.get('If-None-Match')) {
      headers.set('If-None-Match', request.headers.get('If-None-Match'));
    }
    if (request.headers.get('If-Modified-Since')) {
      headers.set('If-Modified-Since', request.headers.get('If-Modified-Since'));
    }
    
    try {
      const response = await fetch(lotekaUrl.toString(), {
        method: request.method,
        headers: headers,
        body: request.method !== 'GET' && request.method !== 'HEAD' ? request.body : undefined,
      });
      
      // Copiar respuesta
      const responseHeaders = new Headers(response.headers);
      responseHeaders.delete('Access-Control-Allow-Origin');
      const allowedOrigin = corsOrigin(request, env);
      if (allowedOrigin) {
        responseHeaders.set('Access-Control-Allow-Origin', allowedOrigin);
        responseHeaders.set('Vary', 'Origin');
      }
      
      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: responseHeaders,
      });
    } catch (error) {
      return new Response(JSON.stringify({ error: error.message }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      });
    }
  },
};
