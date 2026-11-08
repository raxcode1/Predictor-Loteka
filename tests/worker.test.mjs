// Tests del proxy de Cloudflare (node --test). Se simula `fetch` del origen.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function loadWorker(file) {
  return (await import(pathToFileURL(path.join(root, file)).href)).default;
}

function stubFetch() {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return new Response('ok', { status: 200, headers: { 'Access-Control-Allow-Origin': '*' } });
  };
  return calls;
}

for (const file of ['cloudflare-worker-loteka.js', 'worker.js']) {
  test(`${file}: reenvía pathname y query al origen`, async () => {
    const worker = await loadWorker(file);
    const calls = stubFetch();
    const request = new Request(
      'https://proxy.example/wp-content/themes/loteka/getRuletaExpress.php?fechaSeleccionada=07%2F11%2F2026'
    );
    const response = await worker.fetch(request, {}, {});
    assert.equal(response.status, 200);
    assert.equal(
      calls[0].url,
      'https://loteka.com.do/wp-content/themes/loteka/getRuletaExpress.php?fechaSeleccionada=07%2F11%2F2026'
    );
  });

  test(`${file}: no emite CORS "*" por defecto`, async () => {
    const worker = await loadWorker(file);
    stubFetch();
    const request = new Request('https://proxy.example/', { headers: { Origin: 'https://evil.example' } });
    const response = await worker.fetch(request, {}, {});
    assert.equal(response.headers.get('Access-Control-Allow-Origin'), null);
  });

  test(`${file}: permite sólo orígenes configurados`, async () => {
    const worker = await loadWorker(file);
    stubFetch();
    const env = { ALLOWED_ORIGINS: 'https://panel.example, https://otro.example' };
    const ok = await worker.fetch(
      new Request('https://proxy.example/', { headers: { Origin: 'https://panel.example' } }), env, {}
    );
    assert.equal(ok.headers.get('Access-Control-Allow-Origin'), 'https://panel.example');
    const denied = await worker.fetch(
      new Request('https://proxy.example/', { headers: { Origin: 'https://evil.example' } }), env, {}
    );
    assert.equal(denied.headers.get('Access-Control-Allow-Origin'), null);
  });
}
