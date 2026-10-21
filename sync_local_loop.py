"""Bucle local de sincronización.

Responsabilidades:

1. Consultar Loteka desde el PC (IP residencial) con un intervalo jitterizado.
2. Guardar los resultados nuevos en un commit local.
3. Empujar los resultados al servicio de Render, si está configurado.
4. Aplicar una pausa larga cuando Loteka rechaza repetidamente las
   peticiones, para no insistir y no quemar la IP residencial.
5. Respetar el **horario de publicación de Loteka**: se consulta sólo entre
   las 07:00 y las 22:00 (hora de República Dominicana). Fuera de esa ventana
   el bucle duerme hasta la apertura y, al reabrir, arranca con todo
   (sincronización y envío completos).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

import horario_loteka
from config_render import TOKEN_RENDER, URL_RENDER
from config_sync import INTERVALO_SINCRONIZACION, descripcion
from push_render import sincronizar

SCRIPT = Path(__file__).with_name("sync_ruleta_express.py")
CSV_PATH = Path(__file__).with_name("Resultados_RuletaExpress.csv")
INTERVAL = INTERVALO_SINCRONIZACION

#: Intentos fallidos consecutivos antes de aplicar la pausa larga.
FALLOS_PARA_PAUSA = 5

#: Duración de la pausa larga, en minutos, cuando el origen insiste.
PAUSA_LARGA_MINUTOS = 30

#: Espera máxima entre reintentos tras un fallo, en segundos.
ESPERA_MAXIMA = 300

#: Ciclos entre envíos completos de respaldo, aunque no haya sorteos nuevos.
CICLOS_ENVIO_COMPLETO = 20


def run_sync() -> bool:
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--force"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        return result.returncode == 0
    except Exception as error:
        print(f"Error ejecutando sincronización: {error}", file=sys.stderr)
        return False


def commit_if_changed() -> None:
    """Guarda los resultados nuevos en un commit local.

    No sube nada a GitHub: el `push` es manual, para que el bucle no dispare
    despliegues en Vercel o Render por su cuenta. Cuando quieras subir lo
    acumulado:

        git push
    """
    subprocess.run(["git", "add", "Resultados_RuletaExpress.csv"], check=False)
    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        capture_output=True,
    )
    if diff.returncode != 0:
        subprocess.run(
            ["git", "commit", "-m", "Actualizar resultados de Ruleta Express"],
            check=False,
        )
        print(
            "Resultados guardados en un commit local; "
            "súbelos a GitHub con 'git push' cuando quieras."
        )
    else:
        print("Sin cambios nuevos.")


def read_rows() -> list[dict[str, str]]:
    frame = pd.read_csv(CSV_PATH, dtype=str).fillna("")
    columnas = ["Fecha", "Hora", "No_Sorteo", "Numero"]
    return frame[columnas].to_dict(orient="records")


def enviar_a_render(forzar_completo: bool = False) -> None:
    """Empuja los resultados al servicio de Render."""
    if not URL_RENDER or not TOKEN_RENDER:
        return
    try:
        resultado = sincronizar(
            read_rows(),
            base=Path.cwd(),
            url=URL_RENDER,
            token=TOKEN_RENDER,
            forzar_completo=forzar_completo,
        )
        if resultado.get("ok"):
            detalle = resultado.get("detalle") or {}
            print(
                f"Render actualizado: {detalle.get('rows')} sorteos, "
                f"último {detalle.get('last_draw')}."
            )
        else:
            print(f"Envío a Render omitido: {resultado.get('motivo')}")
    except Exception as error:
        print(f"Error enviando a Render: {error}", file=sys.stderr)


def main() -> None:
    print(
        f"Iniciando bucle de sincronización {descripcion()} "
        f"(configurable en config_sync.py)...",
        flush=True,
    )
    if URL_RENDER and TOKEN_RENDER:
        print(f"Envío a Render configurado: {URL_RENDER}", flush=True)
    print("Presiona Ctrl+C para detener.", flush=True)

    fallos = 0
    ciclo = 0
    # Al reabrir tras la pausa nocturna se arranca con todo: sincronización
    # y envío completo del histórico.
    arranque_completo = False
    while True:
        # Horario de publicación: fuera de la ventana no se consulta.
        if not horario_loteka.abierto():
            local = horario_loteka.ahora()
            print(
                f"Loteka cerrado ({local.strftime('%H:%M')} DR). "
                f"Se duerme hasta las {horario_loteka.HORA_APERTURA.strftime('%H:%M')} DR.",
                flush=True,
            )
            horario_loteka.esperar_hasta_apertura()
            arranque_completo = True
            print(
                "Apertura de Loteka: se retoma el scraping con sincronización "
                "y envío completos.",
                flush=True,
            )
            continue

        marca = CSV_PATH.stat().st_mtime if CSV_PATH.exists() else 0.0
        ok = run_sync()
        if ok:
            fallos = 0
            commit_if_changed()
            nuevo = CSV_PATH.exists() and CSV_PATH.stat().st_mtime != marca
            ciclo += 1
            completo = arranque_completo or ciclo % CICLOS_ENVIO_COMPLETO == 0
            arranque_completo = False
            if nuevo or completo:
                enviar_a_render(forzar_completo=completo)
            else:
                print("Sin sorteos nuevos; no se envía nada a Render.")
        else:
            fallos += 1
            print(
                f"Sincronización fallida ({fallos} consecutivas).",
                flush=True,
            )
            if fallos >= FALLOS_PARA_PAUSA:
                print(
                    f"Pausa de {PAUSA_LARGA_MINUTOS} minutos: el origen está "
                    "rechazando las peticiones y no conviene insistir.",
                    flush=True,
                )
                time.sleep(PAUSA_LARGA_MINUTOS * 60)
                fallos = 0
                continue
        espera = min(INTERVAL * (2 ** min(fallos, 4)), ESPERA_MAXIMA)
        time.sleep(espera)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDetenido por el usuario.")
