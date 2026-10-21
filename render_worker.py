"""Worker de Render: scraping automático y envío al servicio web.

Se ejecuta como **Background Worker** de Render:

    python render_worker.py

Ciclo de trabajo:

1. Espera un intervalo sorteado dentro de la ventana configurada.
2. Descarga la página de Loteka con petición condicional (`ETag`) y, si se
   configura, a través de un proxy rotatorio de IP residencial.
3. Extrae los sorteos, los fusiona con el histórico y los guarda en disco.
4. Empuja los datos al servicio web (`POST /api/ingest`).
5. Ante un fallo, aplica espera exponencial (hasta 30 minutos) y **persiste**
   el estado del bloqueo, de modo que un reinicio no reinicie la penalización.

Además, el worker respeta el **horario de publicación de Loteka**: abre a
las 07:00 y cierra a las 22:00 (hora de República Dominicana). Fuera de esa
ventana no se consulta el origen: el worker duerme hasta la apertura y, al
reabrir, **arranca con todo** (descarga completa ignorando la petición
condicional y envío completo del histórico).

El worker nunca se salta la pausa aunque se reinicie: por eso el estado vive
en disco, no en memoria.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import horario_loteka
from push_render import enviar
from scraper_web import ClienteScraper, intervalo_jitter
from sync_ruleta_express import EXPECTED_COLUMNS, extract_results

ROOT = Path(__file__).resolve().parent
CSV_NAME = "Resultados_RuletaExpress.csv"
DATA_DIR = Path(os.environ.get("RENDER_DATA_DIR", "").strip() or (ROOT / "datos_worker"))
WEB_SERVICE_URL = os.environ.get("WEB_SERVICE_URL", "").strip()
PUSH_TOKEN = os.environ.get("PUSH_TOKEN", "").strip()

INTERVALO_MIN = int(os.environ.get("SCRAPER_INTERVAL_MIN", "60") or 60)
INTERVALO_MAX = int(os.environ.get("SCRAPER_INTERVAL_MAX", "180") or 180)
BACKOFF_MAX_MIN = int(os.environ.get("SCRAPER_BACKOFF_MAX_MIN", "30") or 30)

#: Escalera de espera tras fallos consecutivos, en minutos.
ESCALERA_BACKOFF = [2, 5, 10, 20, 30]

ESTADO = "estado_scraper.json"
CACHE = "cache_scraper.json"
CSV_WORKER = DATA_DIR / CSV_NAME

DATA_DIR.mkdir(parents=True, exist_ok=True)


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def leer_estado() -> dict:
    path = DATA_DIR / ESTADO
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def guardar_estado(estado: dict) -> None:
    try:
        (DATA_DIR / ESTADO).write_text(
            json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def leer_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    frame = pd.read_csv(path, dtype=str).fillna("")
    return frame[list(EXPECTED_COLUMNS)].to_dict(orient="records")


def escribir_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=list(EXPECTED_COLUMNS))
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def fusionar(
    existentes: list[dict[str, str]], nuevas: list[dict[str, str]]
) -> list[dict[str, str]]:
    combined: dict[str, dict[str, str]] = {}
    for row in existentes:
        if row.get("No_Sorteo"):
            combined[row["No_Sorteo"]] = row
    for row in nuevas:
        label = str(row.get("No_Sorteo") or "").strip()
        if label:
            combined[label] = {
                column: str(row.get(column) or "").strip()
                for column in EXPECTED_COLUMNS
            }

    def numero(draw: str) -> int:
        try:
            return int(str(draw).rsplit(" ", 1)[-1])
        except (TypeError, ValueError):
            return -1

    return sorted(combined.values(), key=lambda row: numero(row["No_Sorteo"]), reverse=True)


def numero_sorteo(draw: str) -> int:
    """Extrae el número de sorteo de una etiqueta tipo «No. 249491»."""
    try:
        return int(str(draw).rsplit(" ", 1)[-1])
    except (TypeError, ValueError):
        return -1


def enviar_envio(
    estado: dict,
    rows: list[dict[str, str]],
    url: str,
    token: str,
    forzar_completo: bool = False,
) -> dict:
    """Envía sólo lo nuevo; si el servicio perdió el estado, envía el todo.

    El servicio responde con cuántas filas tiene. Si tiene menos de las que
    tiene el worker (se reinició y perdió la memoria), se reenvía el
    histórico completo en el siguiente intento.

    Con ``forzar_completo`` se manda todo el histórico, sin importar el
    último envío: es lo que ocurre al reabrir tras la pausa nocturna.
    """
    ultimo = estado.get("ultimo_envio_sorteo", "")
    if ultimo and not forzar_completo:
        limite = numero_sorteo(ultimo)
        delta = [row for row in rows if numero_sorteo(row.get("No_Sorteo", "")) > limite]
    else:
        delta = list(rows)

    respuesta = enviar(url, token, delta, full=forzar_completo or not ultimo)
    recibido = int(respuesta.get("rows", 0) or 0)
    if recibido < len(rows):
        respuesta = enviar(url, token, rows, full=True)
    return respuesta


def main() -> None:
    cliente = ClienteScraper(cache_path=DATA_DIR / CACHE)
    estado = leer_estado()
    fallos = int(estado.get("fallos", 0) or 0)
    pausa_hasta = estado.get("pausa_hasta", "")
    ultimo_envio = estado.get("ultimo_envio", "")

    origen = CSV_WORKER if CSV_WORKER.exists() else ROOT / CSV_NAME
    rows = leer_csv(origen)
    escribir_csv(CSV_WORKER, rows)

    print(
        f"Worker de scraping · datos en {DATA_DIR} · "
        f"intervalo {INTERVALO_MIN}-{INTERVALO_MAX} s · "
        f"proxies: {len(cliente.proxies)}",
        flush=True,
    )
    if not WEB_SERVICE_URL or not PUSH_TOKEN:
        print(
            "AVISO: WEB_SERVICE_URL o PUSH_TOKEN sin configurar: "
            "el worker recoge resultados pero no los envía.",
            flush=True,
        )

    ciclo = 0
    # Al reabrir tras la pausa nocturna se arranca con todo: descarga sin
    # petición condicional y envío completo del histórico.
    arranque_completo = False
    while True:
        # 0. Horario de publicación de Loteka (07:00-22:00, hora de R. Dominicana).
        if not horario_loteka.abierto():
            local = horario_loteka.ahora()
            estado["horario"] = "cerrado"
            estado["proxima_apertura"] = horario_loteka.proxima_apertura().isoformat(
                timespec="seconds"
            )
            guardar_estado(estado)
            print(
                f"Loteka cerrado ({local.strftime('%H:%M')} DR). "
                f"Se duerme hasta las {horario_loteka.HORA_APERTURA.strftime('%H:%M')} DR.",
                flush=True,
            )
            horario_loteka.esperar_hasta_apertura()
            arranque_completo = True
            estado["horario"] = "abierto"
            print(
                "Apertura de Loteka: se retoma el scraping con descarga y "
                "envío completos.",
                flush=True,
            )
            continue

        # 1. Pausa vigente (persistida: sobrevive a los reinicios).
        if pausa_hasta:
            try:
                limite = datetime.fromisoformat(pausa_hasta)
            except ValueError:
                limite = ahora()
            if ahora() < limite:
                restante = int((limite - ahora()).total_seconds())
                print(f"Pausa activa: {restante} s restantes.", flush=True)
                time.sleep(min(restante, 60))
                continue

        # 2. Espera jitterizada (al abrir se consulta de inmediato).
        if arranque_completo:
            print("Apertura: consulta inmediata, sin espera previa.", flush=True)
        else:
            espera = intervalo_jitter(INTERVALO_MIN, INTERVALO_MAX)
            print(f"Esperando {espera} s antes de consultar...", flush=True)
            time.sleep(espera)
            if not horario_loteka.abierto():
                # La espera cruzó el cierre: no se inicia una consulta fuera
                # del horario de publicación.
                print(
                    "Se alcanzó el cierre de Loteka durante la espera: "
                    "no se consulta.",
                    flush=True,
                )
                continue
        completa = arranque_completo
        arranque_completo = False
        ciclo += 1

        try:
            html, no_modificado = cliente.descargar(forzar_completo=completa)
            if html is None:
                raise RuntimeError("La página no está disponible.")
            nuevos = extract_results(html)
            before = len(rows)
            rows = fusionar(rows, nuevos)
            escribir_csv(CSV_WORKER, rows)
            estado["ultimo_exito"] = ahora().isoformat(timespec="seconds")
            estado["ultima_consulta"] = estado["ultimo_exito"]
            estado["no_modificado"] = no_modificado
            fallos = 0
            estado["fallos"] = 0
            estado.pop("pausa_hasta", None)
            print(
                f"Consulta ok · publicados={len(nuevos)} · "
                f"histórico={len(rows)} (+{len(rows) - before})"
                + (" · 304 sin cambios" if no_modificado else "")
                + (" · descarga completa" if completa else ""),
                flush=True,
            )
        except Exception as error:
            fallos += 1
            minutos = ESCALERA_BACKOFF[min(fallos - 1, len(ESCALERA_BACKOFF) - 1)]
            minutos = min(minutos, BACKOFF_MAX_MIN)
            limite = ahora() + timedelta(minutes=minutos)
            pausa_hasta = limite.isoformat(timespec="seconds")
            estado["fallos"] = fallos
            estado["pausa_hasta"] = pausa_hasta
            estado["ultimo_error"] = str(error)[:300]
            print(
                f"Error en la consulta ({fallos}): {error}. "
                f"Pausa de {minutos} min (hasta {pausa_hasta}).",
                flush=True,
            )
            cliente.siguiente_proxy()
            guardar_estado(estado)
            time.sleep(min(minutos * 60, 60))
            continue

        # 3. Envío al servicio web.
        if WEB_SERVICE_URL and PUSH_TOKEN:
            try:
                respuesta = enviar_envio(
                    estado,
                    rows,
                    WEB_SERVICE_URL,
                    PUSH_TOKEN,
                    forzar_completo=completa,
                )
                estado["ultimo_envio"] = ahora().isoformat(timespec="seconds")
                estado["ultimo_envio_filas"] = respuesta.get("rows", len(rows))
                estado["ultimo_envio_sorteo"] = respuesta.get("last_draw")
                print(
                    f"Enviado a {WEB_SERVICE_URL}: {respuesta.get('rows')} sorteos, "
                    f"último {respuesta.get('last_draw')}"
                    + (" · envío completo" if completa else "") + ".",
                    flush=True,
                )
            except Exception as error:
                print(f"Error enviando al servicio web: {error}", file=sys.stderr, flush=True)

        guardar_estado(estado)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nWorker detenido.")
