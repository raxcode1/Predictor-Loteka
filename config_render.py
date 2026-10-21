"""Configuración del despliegue en Render y del envío de datos desde el PC.

La arquitectura es deliberadamente «el PC consulta, Render sirve»:

- El scraper corre en el PC del usuario, con su IP residencial. Loteka no ve
  tráfico desde centros de datos, que es exactamente lo que bloquea.
- Render sólo publica el dashboard y la API. Nunca consulta Loteka.
- Los resultados viajan del PC a Render por HTTPS con un token compartido.

Si `URL_RENDER` queda vacío, el envío se desactiva y todo sigue funcionando
en local.
"""

from __future__ import annotations

#: URL base del servicio en Render, sin barra final. Vacío = envío desactivado.
URL_RENDER = ""

#: Secreto compartido entre el PC y Render (cabecera `X-Push-Token`).
#: Debe coincidir con la variable de entorno `PUSH_TOKEN` del servicio.
TOKEN_RENDER = ""

#: Archivo local donde se guarda el estado del envío. Permite detectar si el
#: servicio de Render se reinició y perdió los datos.
ARCHIVO_ESTADO = "estado_push_render.json"

#: Tiempo máximo de espera de la respuesta del servicio, en segundos.
TIEMPO_ESPERA = 30

#: Enviar el histórico completo en el primer envío y tras cada reinicio del
#: servicio. Mantenerlo en `True` es lo recomendado: el envío completo son
#: unos 1 MB y sólo ocurre cuando el servicio lo necesita.
ENVIAR_COMPLETO_AL_REINICIAR = True
