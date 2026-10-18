from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import state_recovery
from sync_ruleta_express import extract_results, fetch_page


ROOT = Path(__file__).resolve().parents[1]
LOCAL_CSV = ROOT / "Resultados_RuletaExpress.csv"
BLOB_PATH = "Resultados_RuletaExpress.csv"
BLOB_TOKEN = os.environ.get("BLOB_READ_WRITE_TOKEN")
BLOB_HOST = "https://blob.vercel-storage.com"
EXPECTED_COLUMNS = ["Fecha", "Hora", "No_Sorteo", "Numero"]

_cached_draw: str | None = None
_cached_recovery: dict | None = None


def blob_request(method: str, path: str = "", body: bytes | None = None) -> bytes:
    if not BLOB_TOKEN:
        raise RuntimeError("Falta la variable de entorno BLOB_READ_WRITE_TOKEN.")
    request = Request(
        f"{BLOB_HOST}/{path}" if path else BLOB_HOST,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {BLOB_TOKEN}",
            "x-api-version": "7",
            "Content-Type": "text/csv; charset=utf-8",
        },
    )
    with urlopen(request, timeout=20) as response:
        return response.read()


def read_csv_bytes(data: bytes) -> list[dict[str, str]]:
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    missing = [column for column in EXPECTED_COLUMNS if column not in (rows[0] if rows else {})]
    if rows and missing:
        raise ValueError(f"Faltan columnas requeridas en el CSV: {missing}")
    return [{column: row.get(column, "") for column in EXPECTED_COLUMNS} for row in rows]


def read_rows() -> list[dict[str, str]]:
    if not BLOB_TOKEN:
        return read_csv_bytes(LOCAL_CSV.read_bytes())
    try:
        return read_csv_bytes(blob_request("GET", BLOB_PATH))
    except HTTPError as error:
        if error.code != 404:
            raise
        return read_csv_bytes(LOCAL_CSV.read_bytes())


def serialize_rows(rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=EXPECTED_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def merge_results(rows: list[dict[str, str]], new_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    merged = {row["No_Sorteo"]: row for row in rows if row.get("No_Sorteo")}
    merged.update({row["No_Sorteo"]: row for row in new_rows})
    return sorted(
        merged.values(),
        key=lambda row: int(row["No_Sorteo"].split()[-1]),
        reverse=True,
    )


def recovery_for_rows(rows: list[dict[str, str]]) -> dict:
    global _cached_draw, _cached_recovery
    current_draw = rows[0]["No_Sorteo"] if rows else None
    if current_draw == _cached_draw and _cached_recovery is not None:
        return _cached_recovery
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=".csv", delete=False
    ) as temporary:
        temporary.write(serialize_rows(rows))
        temporary_path = Path(temporary.name)
    try:
        recovery = state_recovery.build_state_recovery(temporary_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    _cached_draw = current_draw
    _cached_recovery = recovery
    return recovery


def dashboard() -> dict:
    rows = read_rows()
    error: str | None = None
    try:
        current = merge_results(rows, extract_results(fetch_page()))
        if BLOB_TOKEN and serialize_rows(current) != serialize_rows(rows):
            blob_request("PUT", BLOB_PATH, serialize_rows(current))
        rows = current
    except Exception as exception:
        error = str(exception)

    recovery_error: str | None = None
    recovery = None
    try:
        recovery = recovery_for_rows(rows)
    except Exception as exception:
        recovery_error = str(exception)

    return {
        "results": rows,
        "last_sync": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sync_error": error,
        "recovery": recovery,
        "recovery_error": recovery_error,
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        try:
            body = json.dumps(dashboard(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
        except Exception as exception:
            body = json.dumps({"error": str(exception)}).encode("utf-8")
            self.send_response(500)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


handler = Handler
