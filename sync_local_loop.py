from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

SCRIPT = Path(__file__).with_name("sync_ruleta_express.py")
INTERVAL = 15  # segundos


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


def git_push_if_changed() -> None:
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
        subprocess.run(["git", "push"], check=False)
        print("Cambios subidos a GitHub; Vercel desplegará automáticamente.")
    else:
        print("Sin cambios nuevos.")


def main() -> None:
    print(f"Iniciando bucle de sincronización cada {INTERVAL} segundos...")
    print("Presiona Ctrl+C para detener.")
    while True:
        ok = run_sync()
        if ok:
            git_push_if_changed()
        else:
            print("Sincronización falló, reintentando en el siguiente ciclo.")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDetenido por el usuario.")
