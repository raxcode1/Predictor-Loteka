// Cloudflare Worker para hacer proxy de loteka.com.do
// Esto evita que tu sitio sea bloqueado directamente por Loteka

export default {
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
      // Permitir CORS si es necesario
      responseHeaders.set('Access-Control-Allow-Origin', '*');
      
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
