from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from config_sync import INTERVALO_SINCRONIZACION, descripcion

SCRIPT = Path(__file__).with_name("sync_ruleta_express.py")
INTERVAL = INTERVALO_SINCRONIZACION


def run_sync() -> bool:
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
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
    despliegues en Vercel por su cuenta. Cuando quieras subir lo acumulado:

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


def main() -> None:
    print(
        f"Iniciando bucle de sincronización {descripcion()} "
        f"(configurable en config_sync.py)...",
        flush=True,
    )
    print("Presiona Ctrl+C para detener.", flush=True)
    while True:
        ok = run_sync()
        if ok:
            commit_if_changed()
        else:
            print("Sincronización falló, reintentando en el siguiente ciclo.")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDetenido por el usuario.")
