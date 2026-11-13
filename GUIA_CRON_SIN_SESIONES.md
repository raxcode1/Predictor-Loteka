# Registrar predicciones sin sesiones abiertas

Vercel sólo ejecuta el panel cuando alguien lo visita. Para que el modo papel
registre cada sorteo sin nadie conectado, el Worker de Cloudflare (el mismo
proxy de Loteka) llama cada minuto a `/api/cron`.

## 1. Vercel
1. Project > Settings > Environment Variables: añade `CRON_SECRET` con una
   cadena larga aleatoria (por ejemplo, la salida de `python -c "import secrets; print(secrets.token_urlsafe(32))"`).
2. Redeploy.
3. Comprueba: `curl -H "Authorization: Bearer TU_SECRETO" https://TU-APP.vercel.app/api/cron`
   debe devolver `{"ok": true, ...}`. Sin la cabecera devuelve 401.

## 2. Cloudflare Worker
1. Edit code: pega el contenido actualizado de `cloudflare-worker-loteka.js`
   (o `worker.js`, el que tengas desplegado) y pulsa Deploy.
2. Settings > Variables and Secrets: añade
   - `CRON_URL` = `https://TU-APP.vercel.app/api/cron`
   - `CRON_SECRET` = el mismo valor que en Vercel (tipo Secret).
3. Settings > Triggers > Cron Triggers > Add: `* 9-23 * * *` y `* 0-2 * * *`
   (cada minuto de 05:00 a 22:59 hora de RD; Cloudflare usa UTC).
   Si despliegas con `wrangler deploy`, `wrangler.toml` ya los incluye.

## Qué esperar
- Unas 1 080 llamadas al día a Vercel y otras tantas al Worker.
- Fuera del horario de Loteka el panel no consulta el origen; el latido sólo
  evalúa y sale.
- Si el latido falla, no se pierde nada salvo esa predicción: la siguiente
  llamada continúa y las predicciones registradas tarde quedan marcadas y no cuentan.
