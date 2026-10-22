from __future__ import annotations
import argparse
import csv
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
EXPECTED_COLUMNS = ["Fecha", "Hora", "No_Sorteo", "Numero"]
HISTORY_URL = "https://loteka.com.do/wp-content/themes/loteka/getRuletaExpress.php"
# El endpoint histórico conserva el patrón de elemento <li>: hora + No. + valor.
ROW_PATTERN = re.compile(
    r'<li[^>]*>.*?'
    r'<span[^>]*>\s*(?P<hora>\d{1,2}:\d{2}\s*[AP]M)\s*</span>\s*'
    r'<span[^>]*>\s*No\.\s*(?P<sorteo>\d+)\s*</span>.*?'
    r'<span[^>]*class="numero"[^>]*>\s*(?P<numero>\d{1,2})\s*</span>.*?</li>',
    re.IGNORECASE | re.DOTALL,
)


def draw_id(row: dict[str, str]) -> int:
    match = re.search(r"(\d+)$", row.get("No_Sorteo", ""))
    if not match:
        raise ValueError(f"No_Sorteo inválido: {row.get('No_Sorteo')!r}")
    return int(match.group(1))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in EXPECTED_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"Faltan columnas requeridas: {missing}")
        return [
            {column: (row.get(column) or "").strip() for column in EXPECTED_COLUMNS}
            for row in reader
        ]


def fetch_date(day: date) -> list[dict[str, str]]:
    "Consulta el archivo por día, no sólo los resultados recientes del home."
    params = urlencode({"fechaSeleccionada": day.strftime("%d/%m/%Y")})
    request = Request(
        f"{HISTORY_URL}?{params}",
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; RuletaExpressCSV/1.0)",
            "Referer": "https://loteka.com.do/",
        },
    )
    with urlopen(request, timeout=30) as response:
        html = response.read().decode("utf-8", errors="replace")

    results = []
    for match in ROW_PATTERN.finditer(html):
        number = int(match.group("numero"))
        if 0 <= number <= 18:
            results.append({
                "Fecha": day.strftime("%d/%m/%Y"),
                "Hora": match.group("hora").upper(),
                "No_Sorteo": f"No. {match.group('sorteo')}",
                "Numero": f"{number:02d}",
            })
    return results

def missing_ranges(ids: set[int]) -> list[tuple[int, int]]:
    missing = sorted(set(range(min(ids), max(ids) + 1)) - ids, reverse=True)
    ranges: list[list[int]] = []
    for number in missing:
        if not ranges or number != ranges[-1][-1] - 1:
            ranges.append([number, number])
        else:
            ranges[-1][-1] = number
    return [(bounds[0], bounds[1]) for bounds in ranges]


def fetch_days_for_holes(holes: set[int], known_dates: dict[int, date], margin: int = 0) -> set[date]:
    "Mapea huecos a fechas por interpolación entre identificadores adyacentes."
    fetch_days: set[date] = set()
    known_sorted = sorted(known_dates)
    for number in holes:
        previous = next((known for known in known_sorted if known > number), None)
        following = next((known for known in reversed(known_sorted) if known < number), None)
        if previous is None or following is None:
            continue
        # Sort IDs récentes están en orden cronológico ascendente. Si el CSV
        # tiene un corte de varios días sin registros, el intervalo entero se
        # consulta: los IDs pueden quedar pegados a un límite de jornada.
        high_date, low_date = known_dates[previous], known_dates[following]
        # La fecha del ID alto es la referencia más confiable para una caída
        # entre jornadas; consultar sólo ese día y márgenes configurados.
        start = end = high_date
        for delta in range(-margin, (end - start).days + margin + 1):
            fetch_days.add(start + timedelta(days=delta))
    return fetch_days

def main() -> None:
    parser = argparse.ArgumentParser(description="Detecta y repara sorteos ausentes en Ruleta Express.")
    parser.add_argument("--csv", type=Path, default=Path(__file__).with_name("Resultados_RuletaExpress.csv"))
    parser.add_argument("--dry-run", action="store_true", help="Sólo consulta e informa; no escribe el CSV.")
    parser.add_argument("--extra-days", type=int, default=2, help="Margen de días a consultar a cada lado de la fecha estimada.")
    args = parser.parse_args()
    if args.extra_days < 0:
        parser.error("--extra-days debe ser cero o positivo")

    path = args.csv.resolve()
    rows = read_csv(path)
    ids = {draw_id(row) for row in rows}
    if not ids:
        raise SystemExit("CSV sin sorteos; no se puede inferir el rango histórico.")
    low, high = min(ids), max(ids)
    ranges = missing_ranges(ids)
    holes = set(range(low, high + 1)) - ids
    print(f"Archivo: {path}")
    print(f"Filas: {len(rows):,}; identificadores únicos: {len(ids):,}; rango {low}-{high}")
    print(f"Sorteos faltantes: {len(holes):,} en {len(ranges)} tramo(s)")
    for upper, lower in ranges:
        print(f"  {upper}-{lower} ({upper-lower+1})")
    if not holes:
        print("No se encontraron huecos dentro del rango.")
        return
    # Estimar fecha para cada ID con el vecino conocido más cercano, usando el
    # ritmo observado del historial (habitualmente ~288 sorteos por día).
    known_dates: dict[int, date] = {}
    for row in rows:
        try:
            known_dates[draw_id(row)] = datetime.strptime(row["Fecha"], "%d/%m/%Y").date()
        except ValueError:
            pass
    # Las fechas del CSV anclan fechas exactas para cada sorteo. Interpolamos
    # entre los identificadores conocidos adyacentes a cada hueco.
    # Hay cortes históricos de muchos días. Las fechas por sorteo conocidas
    # son mejores guías que extrapolar a través de un vacío; aumentar ventana
    # alrededor de cada hueco ayuda a encontrar jornadas con horario variable.
    fetch_days = fetch_days_for_holes(holes, known_dates, max(args.extra_days, 14))
    # La API diaria devuelve las jornadas solicitadas tal cual; para un tramo
    # completo sin registros, consultar todos los días entre las fechas-ancla.
    known_sorted = sorted(known_dates)
    for upper, lower in ranges:
        left = next((number for number in known_sorted if number > upper), None)
        right = next((number for number in reversed(known_sorted) if number < lower), None)
        if left is not None and right is not None:
            start, end = sorted((known_dates[left], known_dates[right]))
            for day_offset in range(-(args.extra_days + 7), (end - start).days + args.extra_days + 8):
                fetch_days.add(start + timedelta(days=day_offset))
    if len(fetch_days) > 450:
        raise SystemExit(
            f"La búsqueda abarcaría {len(fetch_days)} días; demasiado extensa para una consulta segura. "
            "Divide el rango o aumenta la precisión de fechas antes de continuar."
        )

    recovered: dict[int, dict[str, str]] = {}
    source_day_for_draw: dict[int, date] = {}
    for day in sorted(fetch_days):
        try:
            daily = fetch_date(day)
            day_recovered = 0
            # Sólo se incorporan sorteos con resultado confirmado: crear filas
            # con Numero vacío contamina el CSV y rompe los análisis.
            for row in daily:
                number = draw_id(row)
                if number in holes:
                    recovered[number] = row
                    source_day_for_draw[number] = day
            day_recovered = sum(1 for number in holes if source_day_for_draw.get(number) == day)
            print(f"Loteka {day:%d/%m/%Y}: {len(daily)} filas; huecos identificados {day_recovered}")
        except Exception as error:
            print(f"No se pudo consultar {day:%d/%m/%Y}: {error}")

    missing_after = sorted(holes - recovered.keys(), reverse=True)
    print(f"Recuperados confirmados: {len(recovered):,}/{len(holes):,}")
    if missing_after:
        print("No localizados aún: " + ", ".join(map(str, missing_after[:100])))
        if len(missing_after) > 100:
            print(f"... y {len(missing_after)-100} más")
    if args.dry_run:
        print("Dry-run: el CSV no fue modificado.")
        return
    if not recovered:
        raise SystemExit("No se confirmó ningún sorteo faltante; CSV intacto.")

    merged = {draw_id(row): row for row in rows}
    merged.update(recovered)
    final_rows = [merged[number] for number in sorted(merged, reverse=True)]
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPECTED_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(final_rows)
    final_ids = set(merged)
    remaining = set(range(min(final_ids), max(final_ids) + 1)) - final_ids
    print(f"CSV actualizado: {len(final_rows):,} filas, añadidas {len(recovered):,}.")
    print(f"Respaldo creado: {backup}")
    print(f"Huecos restantes dentro del rango: {len(remaining):,}")

if __name__ == "__main__":
    main()
