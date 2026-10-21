"""Horario de publicación de Ruleta Express (hora de República Dominicana).

Loteka publica Ruleta Express en una ventana acotada: **abre a las 05:00** y
**cierra a las 22:00** (hora de Santo Domingo, UTC-4). Fuera de esa ventana no
hay sorteos nuevos, así que el scraper **no consulta**: se duerme hasta la
apertura.

Consecuencias prácticas:

- Se reduce a la mitad el tráfico diario contra el sitio de origen.
- No se insisten peticiones a un origen que ya no publica nada, que es
  justo lo que hace que un origen decida bloquearte.
- La pausa es de horas, no de segundos: el sistema no queda «colgado» en un
  bucle de reintentos nocturnos.

Al abrir, el scraper **arranca con todo**: descarga completa (ignorando la
petición condicional) y envío completo del histórico al servicio web, de modo
que no queda ningún hueco por la pausa nocturna.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from time import sleep
from zoneinfo import ZoneInfo

#: Zona horaria de República Dominicana.
ZONA = ZoneInfo("America/Santo_Domingo")

#: Hora de apertura de la publicación (05:00).
HORA_APERTURA = time(5, 0)

#: Hora de cierre de la publicación (22:00).
HORA_CIERRE = time(22, 0)

#: Trozo máximo de espera al dormir, para poder parar el proceso a tiempo.
PASO_ESPERA = 60


def ahora() -> datetime:
    """Instante actual en hora de República Dominicana."""
    return datetime.now(ZONA)


def _local(momento: datetime | None = None) -> datetime:
    momento = momento or ahora()
    if momento.tzinfo is None:
        return momento.replace(tzinfo=ZONA)
    return momento.astimezone(ZONA)


def abierto(momento: datetime | None = None) -> bool:
    """¿Está Loteka publicando en este momento?"""
    local = _local(momento)
    return HORA_APERTURA <= local.time() < HORA_CIERRE


def segundos_hasta_apertura(momento: datetime | None = None) -> int:
    """Segundos que faltan para la próxima apertura."""
    local = _local(momento)
    apertura = local.replace(
        hour=HORA_APERTURA.hour,
        minute=HORA_APERTURA.minute,
        second=0,
        microsecond=0,
    )
    if local < apertura:
        return int((apertura - local).total_seconds())
    siguiente = apertura + timedelta(days=1)
    return int((siguiente - local).total_seconds())


def segundos_hasta_cierre(momento: datetime | None = None) -> int:
    """Segundos que faltan para el cierre de la noche."""
    local = _local(momento)
    cierre = local.replace(
        hour=HORA_CIERRE.hour,
        minute=HORA_CIERRE.minute,
        second=0,
        microsecond=0,
    )
    if local < cierre:
        return int((cierre - local).total_seconds())
    siguiente = cierre + timedelta(days=1)
    return int((siguiente - local).total_seconds())


def proxima_apertura(momento: datetime | None = None) -> datetime:
    """Instante de la próxima apertura."""
    return _local(momento) + timedelta(seconds=segundos_hasta_apertura(momento))


def esperar_hasta_apertura(limite_segundos: int | None = None) -> bool:
    """Duerme, en trozos, hasta que abra la publicación.

    Se duerme en trozos cortos (``PASO_ESPERA``) en lugar de de una sola vez
    para que el proceso pueda detenerse con prontitud y para poder informar
    del progreso.

    Devuelve ``True`` si la ventana se abrió dentro del plazo y ``False`` si
    se agotó ``limite_segundos`` sin llegar a abrir.
    """
    esperado = 0
    while not abierto():
        if limite_segundos is not None and esperado >= limite_segundos:
            return False
        tramo = min(segundos_hasta_apertura(), PASO_ESPERA)
        if limite_segundos is not None:
            tramo = min(tramo, limite_segundos - esperado)
        if tramo <= 0:
            return False
        sleep(tramo)
        esperado += tramo
    return True


def estado() -> dict:
    """Resumen del horario, para la API y la interfaz."""
    local = _local()
    return {
        "zona": "America/Santo_Domingo",
        "apertura": HORA_APERTURA.strftime("%H:%M"),
        "cierre": HORA_CIERRE.strftime("%H:%M"),
        "abierto": abierto(local),
        "hora_local": local.strftime("%H:%M:%S"),
        "segundos_hasta_apertura": segundos_hasta_apertura(local),
        "segundos_hasta_cierre": segundos_hasta_cierre(local),
    }
