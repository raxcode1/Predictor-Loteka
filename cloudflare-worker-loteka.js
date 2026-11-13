// Cloudflare Worker: proxy de loteka.com.do
//
// IMPORTANTE: reenvía la ruta y la query string tal cual. Si siempre se
// pidiera https://loteka.com.do/ se romperían las consultas al histórico
// (getRuletaExpress.php?fechaSeleccionada=...), que son las que rellenan los
// sorteos faltantes. Sin ese relleno, el panel detecta huecos de numeración y
// los muestra como «sorteos no evaluados» aunque Loteka sí los publicó.
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
    const incoming = new URL(request.url);
    const targetUrl = new URL('https://loteka.com.do' + incoming.pathname + incoming.search);

    const headers = new Headers();
    headers.set(
      'User-Agent',
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
        '(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
    );
    headers.set(
      'Accept',
      request.headers.get('Accept') ||
        'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    );
    headers.set('Accept-Language', 'es-DO,es;q=0.9,*;q=0.8');
    headers.set('Referer', 'https://loteka.com.do/');
    // Evita respuestas cacheadas/vacías del origen.
    headers.set('Cache-Control', 'no-cache');
    if (request.headers.get('If-None-Match')) {
      headers.set('If-None-Match', request.headers.get('If-None-Match'));
    }
    if (request.headers.get('If-Modified-Since')) {
      headers.set('If-Modified-Since', request.headers.get('If-Modified-Since'));
    }

    try {
      const response = await fetch(targetUrl.toString(), {
        method: request.method,
        headers,
        redirect: 'follow',
        cache: 'no-store',
      });

      const newHeaders = new Headers(response.headers);
      newHeaders.delete('Access-Control-Allow-Origin');
      const allowedOrigin = corsOrigin(request, env);
      if (allowedOrigin) {
        newHeaders.set('Access-Control-Allow-Origin', allowedOrigin);
        newHeaders.set('Vary', 'Origin');
      }
      newHeaders.set('Cache-Control', 'no-store');

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: newHeaders,
      });
    } catch (e) {
      return new Response('Error: ' + e.message, { status: 500 });
    }
  },
};
