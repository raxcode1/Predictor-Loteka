from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

import horario_loteka
from config_sync import (
    INTERVALO_MINIMO,
    INTERVALO_SINCRONIZACION,
    intervalo_valido,
)


URL = "https://loteka.com.do/"
CSV_PATH = Path(__file__).with_name("Resultados_RuletaExpress.csv")
TIME_ZONE = ZoneInfo("America/Santo_Domingo")
EXPECTED_COLUMNS = ["Fecha", "Hora", "No_Sorteo", "Numero"]

ROW_PATTERN = re.compile(
    r'<li>\s*'
    r'<div[^>]*>\s*'
    r'<span[^>]*>(?P<hora>\d{1,2}:\d{2}\s*[AP]M)</span>\s*'
    r'<span[^>]*>No\.\s*(?P<sorteo>\d+)</span>.*?'
    r'<span[^>]*>(?P<numero>\d{1,2})</span>',
    re.IGNORECASE | re.DOTALL,
)


def fetch_page() -> str:
    request = Request(
        URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; RuletaExpressCSV/1.0; "
                "+https://loteka.com.do/)"
            )
        },
    )
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def extract_results(html: str) -> list[dict[str, str]]:
    section_start = html.find('id="result-chanceexpress2"')
    if section_start < 0:
        raise RuntimeError(
            "No se encontró la sección pública de Ruleta Express "
            "(result-chanceexpress2)."
        )

    section_end = html.find("</ul>", section_start)
    section = html[section_start:section_end if section_end >= 0 else None]
    today = datetime.now(TIME_ZONE).strftime("%d/%m/%Y")
    results = []

    for match in ROW_PATTERN.finditer(section):
        number = int(match.group("numero"))
        if not 0 <= number <= 18:
            continue
        results.append(
            {
                "Fecha": today,
                "Hora": match.group("hora").upper(),
                "No_Sorteo": f"No. {match.group('sorteo')}",
                "Numero": f"{number:02d}",
            }
        )

    if not results:
        raise RuntimeError("No se pudieron extraer resultados de Ruleta Express.")
    return results


def sync_csv(path: Path, results: list[dict[str, str]]) -> int:
    if path.exists():
        frame = pd.read_csv(path, dtype=str)
        missing = [column for column in EXPECTED_COLUMNS if column not in frame]
        if missing:
            raise ValueError(f"Faltan columnas requeridas en el CSV: {missing}")
    else:
        frame = pd.DataFrame(columns=EXPECTED_COLUMNS)

    known_draws = set(frame["No_Sorteo"].dropna().astype(str))
    new_rows = [row for row in results if row["No_Sorteo"] not in known_draws]
    updated = pd.concat([frame, pd.DataFrame(new_rows)], ignore_index=True)
    updated = updated.drop_duplicates(subset=["No_Sorteo"], keep="last")
    updated["_sorteo_num"] = (
        updated["No_Sorteo"]
        .str.extract(r"(\d+)", expand=False)
        .astype("int64")
    )
    updated = updated.sort_values(
        "_sorteo_num",
        ascending=False,
        kind="stable",
    ).drop(columns="_sorteo_num")
    updated.to_csv(path, index=False, encoding="utf-8-sig")
    return len(new_rows)


def run_once(path: Path) -> None:
    checked_at = datetime.now(TIME_ZONE).strftime("%Y-%m-%d %H:%M:%S")
    results = extract_results(fetch_page())
    added = sync_csv(path, results)
    latest = results[0] if results else None
    status = "NUEVO RESULTADO" if added else "sin cambios"
    print(
        f"[{checked_at}] Consulta completada: {status}; "
        f"publicados={len(results)}, nuevos={added}",
        flush=True,
    )
    if latest:
        print(
            f"Más reciente publicado: {latest['No_Sorteo']} "
            f"{latest['Hora']} -> {latest['Numero']}",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sincroniza resultados públicos de Ruleta Express con el CSV."
    )
    parser.add_argument(
        "--watch",
        type=int,
        nargs="?",
        const=INTERVALO_SINCRONIZACION,
        metavar="SEGUNDOS",
        help=(
            "Repetir la consulta con el intervalo indicado. Sin valor usa el "
            f"intervalo configurado en config_sync.py ({INTERVALO_SINCRONIZACION} s)."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Consultar aunque Loteka esté cerrado (fuera del horario de "
            "publicación 07:00-22:00, hora de República Dominicana)."
        ),
    )
    args = parser.parse_args()

    if args.watch is None:
        if not args.force and not horario_loteka.abierto():
            local = horario_loteka.ahora()
            estado = horario_loteka.estado()
            print(
                f"Loteka está cerrado ({local.strftime('%H:%M')} DR). "
                f"No se consulta: la publicación va de las {estado['apertura']} a las "
                f"{estado['cierre']} y abre de nuevo en "
                f"{horario_loteka.proxima_apertura().strftime('%H:%M')} DR. "
                "Usa --force para consultar de todas formas."
            )
            return
        run_once(CSV_PATH)
        return
    intervalo_valido(args.watch)

    while True:
        if not args.force and not horario_loteka.abierto():
            local = horario_loteka.ahora()
            print(
                f"Loteka cerrado ({local.strftime('%H:%M')} DR). "
                f"Se duerme hasta las {horario_loteka.HORA_APERTURA.strftime('%H:%M')} DR.",
                flush=True,
            )
            horario_loteka.esperar_hasta_apertura()
            print(
                "Apertura de Loteka: se retoma el scraping con una "
                "sincronización completa.",
                flush=True,
            )
            continue
        try:
            run_once(CSV_PATH)
        except Exception as error:
            print(f"Error de sincronización: {error}")
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
