// Cloudflare Worker: proxy de loteka.com.do
//
// IMPORTANTE: reenvía la ruta y la query string tal cual. Si siempre se
// pidiera https://loteka.com.do/ se romperían las consultas al histórico
// (getRuletaExpress.php?fechaSeleccionada=...), que son las que rellenan los
// sorteos faltantes. Sin ese relleno, el panel detecta huecos de numeración y
// los muestra como «sorteos no evaluados» aunque Loteka sí los publicó.
export default {
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
      newHeaders.set('Access-Control-Allow-Origin', '*');
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
