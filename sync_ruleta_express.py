from __future__ import annotations

import argparse
import csv
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

import horario_loteka
from config_sync import (
    INTERVALO_MINIMO,
    INTERVALO_SINCRONIZACION,
    intervalo_valido,
)


import os

WORKER_URL = os.environ.get("CLOUDFLARE_WORKER_URL", "").strip().rstrip("/")
if WORKER_URL:
    URL = f"{WORKER_URL}/"
else:
    URL = "https://loteka.com.do/"
if not URL.endswith("/"):
    URL = URL + "/"
TIME_ZONE = ZoneInfo("America/Santo_Domingo")
EXPECTED_COLUMNS = ["Fecha", "Hora", "No_Sorteo", "Numero"]

#: Sólo se intentan rellenar huecos de las últimas jornadas. Los huecos
#: históricos que Loteka no publica se consultarían en cada sincronización.
BACKFILL_RECENT_DAYS = 30

ROW_PATTERN = re.compile(
    r'<li>\s*'
    r'<div[^>]*>\s*'
    r'<span[^>]*>(?P<hora>\d{1,2}:\d{2}\s*[AP]M)</span>\s*'
    r'<span[^>]*>No\.\s*(?P<sorteo>\d+)</span>.*?'
    r'<span[^>]*>(?P<numero>\d{1,2})</span>',
    re.IGNORECASE | re.DOTALL,
)

SERVER_DATE_PATTERN = re.compile(
    r"var\s+fechaServidor\s*=\s*['\"](\d{2}/\d{2}/\d{4})['\"]",
    re.IGNORECASE,
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
    # El home expone la fecha de la jornada publicada; usarla evita filas sin
    # fecha y es más fiable que inferirla por la hora del sorteo.
    server_date = SERVER_DATE_PATTERN.search(html)
    draw_date = (
        server_date.group(1)
        if server_date
        else datetime.now(TIME_ZONE).strftime("%d/%m/%Y")
    )
    results = []

    for match in ROW_PATTERN.finditer(section):
        number = int(match.group("numero"))
        if not 0 <= number <= 18:
            continue
        results.append(
            {
                "Fecha": draw_date,
                "Hora": match.group("hora").upper(),
                "No_Sorteo": f"No. {match.group('sorteo')}",
                "Numero": f"{number:02d}",
            }
        )

    if not results:
        raise RuntimeError("No se pudieron extraer resultados de Ruleta Express.")
    results.sort(
        key=lambda row: int(row["No_Sorteo"].split()[-1]), reverse=True
    )
    return results


def fetch_history_date(day: date) -> list[dict[str, str]]:
    "Recupera todos los resultados publicados por Loteka para una fecha."
    params = urlencode({"fechaSeleccionada": day.strftime("%d/%m/%Y")})
    request = Request(
        f"https://loteka.com.do/wp-content/themes/loteka/getRuletaExpress.php?{params}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://loteka.com.do/"},
    )
    with urlopen(request, timeout=30) as response:
        html = response.read().decode("utf-8", errors="replace")
    historical = []
    historical_pattern = re.compile(
        r'<li[^>]*>.*?'
        r'<span[^>]*>\s*(?P<hora>\d{1,2}:\d{2}\s*[AP]M)\s*</span>\s*'
        r'<span[^>]*>\s*No\.\s*(?P<sorteo>\d+)\s*</span>.*?'
        r'<span[^>]*class="numero"[^>]*>\s*(?P<numero>\d{1,2})\s*</span>.*?</li>',
        re.IGNORECASE | re.DOTALL,
    )
    for match in historical_pattern.finditer(html):
        number = int(match.group("numero"))
        if 0 <= number <= 18:
            historical.append({
                "Fecha": day.strftime("%d/%m/%Y"),
                "Hora": match.group("hora").upper(),
                "No_Sorteo": f"No. {match.group('sorteo')}",
                "Numero": f"{number:02d}",
            })
    return historical

def backfill_missing_draws(frame: pd.DataFrame, fresh_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    "Consulta fechas que enmarcan huecos y devuelve sorteos históricos confirmados."
    if frame.empty:
        return []
    known: dict[int, dict[str, str]] = {}
    for row in frame.to_dict(orient="records"):
        match = re.search(r"(\d+)$", str(row.get("No_Sorteo", "")))
        if match:
            known[int(match.group(1))] = row
    incoming = {}
    for row in fresh_rows:
        match = re.search(r"(\d+)$", row.get("No_Sorteo", ""))
        if match:
            incoming[int(match.group(1))] = row
    known.update(incoming)
    ids = set(known)
    if not ids:
        return []
    min_id, max_id = min(ids), max(ids)
    holes = set(range(min_id, max_id + 1)) - ids
    if not holes:
        return []
    dated = {}
    for draw, row in known.items():
        try:
            dated[draw] = datetime.strptime(str(row.get("Fecha", "")), "%d/%m/%Y").date()
        except ValueError:
            pass
    ordered = sorted(dated)
    newest_date = max(dated.values(), default=None)
    query_days = set()
    for hole in holes:
        hi = next((n for n in ordered if n > hole), None)
        lo = next((n for n in reversed(ordered) if n < hole), None)
        if hi is None or lo is None:
            continue
        hi_date, lo_date = dated[hi], dated[lo]
        if newest_date and max(hi_date, lo_date) < newest_date - timedelta(
            days=BACKFILL_RECENT_DAYS
        ):
            continue
        start, end = (hi_date, lo_date) if hi_date <= lo_date else (lo_date, hi_date)
        if start == end:
            query_days.add(start)
        elif hi_date < lo_date:
            query_days.update(start + timedelta(days=i) for i in range((end-start).days + 1))
        else:
            # El cambio de jornada produce saltos numéricos no necesariamente
            # consecutivos; consultar la fecha del ID alto y su día vecino.
            query_days.update((hi_date, hi_date + timedelta(days=1)))

    recovered = {}
    for day in sorted(query_days):
        try:
            for row in fetch_history_date(day):
                draw = int(row["No_Sorteo"].split()[-1])
                if draw in holes:
                    recovered[draw] = row
        except Exception as error:
            print(f"Aviso: no se pudo backfillear {day:%d/%m/%Y}: {error}")
    return list(recovered.values())


def complete_missing_dates(
    frame: pd.DataFrame, fallback_date: date | None = None
) -> pd.DataFrame:
    "Completa fechas vacías consultando el histórico diario de Loteka."
    if frame.empty:
        return frame
    dates = frame["Fecha"].fillna("").astype(str).str.strip()
    missing = dates == ""
    if not missing.any():
        return frame

    draw_ids: dict[int, int] = {}
    for index, row in frame.iterrows():
        match = re.search(r"(\d+)$", str(row.get("No_Sorteo", "")))
        if match:
            draw_ids[index] = int(match.group(1))

    known_dates: dict[int, date] = {}
    for index, row in frame[~missing].iterrows():
        match = re.search(r"(\d+)$", str(row.get("No_Sorteo", "")))
        try:
            known_dates[int(match.group(1))] = datetime.strptime(
                str(row["Fecha"]).strip(), "%d/%m/%Y"
            ).date()
        except (AttributeError, ValueError):
            continue

    missing_ids = {
        draw_ids[index] for index in frame.index[missing] if index in draw_ids
    }
    if not missing_ids:
        return frame

    candidate_days: set[date] = set()
    if fallback_date:
        candidate_days.update(
            {fallback_date, fallback_date - timedelta(days=1)}
        )
    ordered = sorted(known_dates)
    if ordered:
        low_missing, high_missing = min(missing_ids), max(missing_ids)
        lower = [draw for draw in ordered if draw < low_missing]
        upper = [draw for draw in ordered if draw > high_missing]
        for draw in (max(lower) if lower else None, min(upper) if upper else None):
            if draw is not None:
                anchor = known_dates[draw]
                candidate_days.update(
                    anchor + timedelta(days=offset) for offset in (-1, 0, 1)
                )
        if lower and upper:
            start = known_dates[max(lower)]
            end = known_dates[min(upper)]
            if start <= end and (end - start).days <= 60:
                candidate_days.update(
                    start + timedelta(days=i)
                    for i in range((end - start).days + 1)
                )
    if not candidate_days:
        candidate_days.add(datetime.now(TIME_ZONE).date())

    resolved: dict[int, str] = {}
    for day in sorted(candidate_days):
        try:
            for row in fetch_history_date(day):
                draw = int(row["No_Sorteo"].split()[-1])
                if draw in missing_ids:
                    resolved[draw] = row["Fecha"]
        except Exception as error:
            print(f"Aviso: no se pudo completar la fecha de {day:%d/%m/%Y}: {error}")

    for index in frame.index[missing]:
        draw = draw_ids.get(index)
        if draw in resolved:
            frame.at[index, "Fecha"] = resolved[draw]
    return frame


def sync_csv(path: Path, results: list[dict[str, str]]) -> int:
    if path.exists():
        frame = pd.read_csv(path, dtype=str)
        missing = [column for column in EXPECTED_COLUMNS if column not in frame]
        if missing:
            raise ValueError(f"Faltan columnas requeridas en el CSV: {missing}")
    else:
        frame = pd.DataFrame(columns=EXPECTED_COLUMNS)

    fallback_date = None
    if results:
        try:
            fallback_date = datetime.strptime(results[0]["Fecha"], "%d/%m/%Y").date()
        except ValueError:
            pass
    frame = complete_missing_dates(frame, fallback_date)

    fresh_rows = pd.DataFrame(results, columns=EXPECTED_COLUMNS)
    missing_rows = backfill_missing_draws(frame, results)
    if missing_rows:
        print(f"Backfill desde archivo histórico: {len(missing_rows)} sorteos recuperados.")
    updated = pd.concat([frame, fresh_rows, pd.DataFrame(missing_rows)], ignore_index=True)
    original_draws = set(frame["No_Sorteo"].dropna().astype(str)) if not frame.empty else set()
    updated = updated.drop_duplicates(subset=["No_Sorteo"], keep="last")
    blank_dates = updated["Fecha"].fillna("").astype(str).str.strip() == ""
    if blank_dates.any():
        raise ValueError(
            f"{int(blank_dates.sum())} sorteos quedaron sin Fecha; no se escribe el CSV."
        )
    updated["_sorteo_num"] = pd.to_numeric(
        updated["No_Sorteo"].str.extract(r"(\d+)", expand=False), errors="coerce"
    ).astype("Int64")
    updated = updated.sort_values(
        "_sorteo_num", ascending=False, kind="stable", na_position="last"
    ).drop(columns="_sorteo_num")
    updated.to_csv(path, index=False, encoding="utf-8-sig")
    return len(set(updated["No_Sorteo"].dropna().astype(str)) - original_draws)


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
        "--path",
        type=Path,
        default=Path(__file__).with_name("Resultados_RuletaExpress.csv"),
        help="Ruta del CSV que se actualizará (por defecto el histórico del proyecto).",
    )
    parser.add_argument(
        "--bootstrap-history",
        action="store_true",
        help=(
            "Permite inicializar un CSV vacío si Loteka está publicando al menos "
            "los resultados más recientes. Por seguridad, no reduce ni reemplaza "
            "un histórico local que ya tenga datos."
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
        if args.bootstrap_history:
            bootstrap_path = args.path
            if bootstrap_path.exists() and bootstrap_path.stat().st_size > 0:
                raise SystemExit(
                    f"El archivo {bootstrap_path} ya existe y no está vacío; "
                    "se cancela para proteger el histórico."
                )
            run_once(args.path)
            return
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
        run_once(args.path)
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
            run_once(args.path)
        except Exception as error:
            print(f"Error de sincronización: {error}")
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
