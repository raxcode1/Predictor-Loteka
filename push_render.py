"""Cliente que empuja los resultados del PC al servicio de Render.

El envío es incremental: sólo viajan los sorteos nuevos, salvo que el
servicio responda con menos filas de las que tiene el PC. En ese caso se
reenvía el histórico completo, de modo que un reinicio del servicio nunca
deja huecos en los datos.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from config_render import (
    ARCHIVO_ESTADO,
    ENVIAR_COMPLETO_AL_REINICIAR,
    TIEMPO_ESPERA,
    TOKEN_RENDER,
    URL_RENDER,
)


def draw_number(draw: str) -> int:
    """Extrae el número de sorteo de una etiqueta tipo «No. 249491»."""
    try:
        return int(str(draw).rsplit(" ", 1)[-1])
    except (TypeError, ValueError):
        return -1


def _estado_path(base: Path) -> Path:
    return base / ARCHIVO_ESTADO


def leer_estado(base: Path) -> dict[str, Any]:
    path = _estado_path(base)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def guardar_estado(base: Path, estado: dict[str, Any]) -> None:
    path = _estado_path(base)
    try:
        path.write_text(
            json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def enviar(
    url: str,
    token: str,
    rows: list[dict[str, str]],
    full: bool = False,
) -> dict[str, Any]:
    """Envía las filas al endpoint `/api/ingest` y devuelve la respuesta."""
    from urllib.request import Request, urlopen

    payload = json.dumps({"rows": rows, "full": full}, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{url.rstrip('/')}/api/ingest",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Push-Token": token,
            "User-Agent": "RuletaExpressPush/1.0",
        },
    )
    with urlopen(request, timeout=TIEMPO_ESPERA) as response:
        return json.loads(response.read().decode("utf-8"))


def sincronizar(
    rows: Iterable[dict[str, str]],
    base: Path | None = None,
    url: str = URL_RENDER,
    token: str = TOKEN_RENDER,
    forzar_completo: bool = False,
) -> dict[str, Any]:
    """Sincroniza el CSV local con el servicio de Render.

    Devuelve un diccionario con el resultado y, si algo falla, el motivo.
    """
    base = base or Path.cwd()
    if not url or not token:
        return {"ok": False, "motivo": "Render no está configurado en config_render.py"}

    filas = list(rows)
    estado = leer_estado(base)
    ultimo = estado.get("last_draw")
    if ultimo:
        limite = draw_number(ultimo)
        nuevas = [row for row in filas if draw_number(row.get("No_Sorteo", "")) > limite]
    else:
        nuevas = list(filas)

    completo = not ultimo or forzar_completo
    respuesta = enviar(url, token, filas if completo else nuevas, full=completo)

    recibido = int(respuesta.get("rows", 0) or 0)
    # Si el servicio no tiene el mismo conjunto de sorteos (por ejemplo, le
    # faltan sorteos intermedios que el PC recuperó después), el conteo de
    # filas puede coincidir aunque los datos difieran. La huella lo detecta y
    # fuerza un envío completo; sin esto el hueco queda en el servidor y el
    # panel lo muestra como «sorteos no evaluados» para siempre.
    huella_local = f"{len(filas)}:{sum(draw_number(r.get('No_Sorteo', '')) for r in filas if draw_number(r.get('No_Sorteo', '')) >= 0)}"
    huella_servidor = respuesta.get("fingerprint")
    desajuste = (
        (huella_servidor is not None and huella_servidor != huella_local)
        or recibido < len(filas)
    )
    if desajuste and not completo and ENVIAR_COMPLETO_AL_REINICIAR:
        # El servicio no tiene todos los datos (se reinició o perdió el
        # estado): se reenvía el histórico completo.
        respuesta = enviar(url, token, filas, full=True)

    guardar_estado(
        base,
        {
            "last_draw": respuesta.get("last_draw"),
            "rows": respuesta.get("rows"),
            "actualizado": time_now(),
        },
    )
    return {"ok": True, "detalle": respuesta}


def time_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
