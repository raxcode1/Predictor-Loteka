"""Configuración centralizada de la frecuencia de consulta de Ruleta Express.

Este es el ÚNICO archivo que hay que editar para cambiar cada cuánto se
consultan los números. El valor se aplica a todos los puntos del proyecto:

- `sync_local_loop.py`: bucle local que consulta, hace commit y sube a GitHub.
- `sync_ruleta_express.py --watch`: consulta en modo continuo.
- `web_app.py`: hilo de sincronización del dashboard local.
- `api/dashboard.py` y `web/app.js`: refresco del navegador (el intervalo
  viaja en la respuesta de la API, así que tampoco hay que tocar el JavaScript).

Reglas prácticas: 15 segundos es el mínimo técnico (por debajo, Loteka puede
bloquear la IP residencial) y 45-60 segundos es un buen equilibrio entre
detectar rápido el resultado y no cargar el sitio de origen.
"""

from __future__ import annotations

# =============================================================================
#  CAMBIA AQUÍ los segundos entre consultas. Acepta 15 o más.
# =============================================================================
INTERVALO_SINCRONIZACION = 45

#: Límites aceptados para el intervalo, en segundos.
INTERVALO_MINIMO = 15
INTERVALO_MAXIMO = 3600


def intervalo_valido(valor: int) -> int:
    """Valida un intervalo y devuelve el valor normalizado.

    Lanza `ValueError` si está fuera de los límites permitidos.
    """
    if not isinstance(valor, int) or isinstance(valor, bool):
        raise ValueError(f"El intervalo debe ser un número entero, no {valor!r}.")
    if valor < INTERVALO_MINIMO:
        raise ValueError(
            f"El intervalo debe ser de al menos {INTERVALO_MINIMO} segundos "
            f"(recibido {valor})."
        )
    if valor > INTERVALO_MAXIMO:
        raise ValueError(
            f"El intervalo no puede superar {INTERVALO_MAXIMO} segundos "
            f"(recibido {valor})."
        )
    return valor


def intervalo_configurado() -> int:
    """Devuelve el intervalo configurado, validado."""
    return intervalo_valido(INTERVALO_SINCRONIZACION)


def descripcion() -> str:
    """Texto corto con el intervalo activo, para mostrar en consola y en la web."""
    return f"cada {INTERVALO_SINCRONIZACION} s"
