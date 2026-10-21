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

import config_alertas
import state_recovery
import horario_loteka
try:
    from auth_utils import authorized
except ImportError:
    from api.auth_utils import authorized
from config_sync import INTERVALO_SINCRONIZACION
from live_monitor import LiveMonitor, memory_store
from m4_engine.kelly import KellyConfig
from sync_ruleta_express import extract_results, fetch_page


import os

URL = os.environ.get("CLOUDFLARE_WORKER_URL", "").strip().rstrip("/") or "https://loteka.com.do"
if not URL.endswith("/"):
    URL = URL + "/"

BLOB_PATH = "Resultados_RuletaExpress.csv"
PREDICTIONS_PATH = "ruleta_predictions.json"
BLOB_TOKEN = os.environ.get("BLOB_READ_WRITE_TOKEN")
BLOB_HOST = "https://blob.vercel-storage.com"
EXPECTED_COLUMNS = ["Fecha", "Hora", "No_Sorteo", "Numero"]

_cached_draw: str | None = None
_cached_recovery: dict | None = None
_monitor: LiveMonitor | None = None


def blob_request(
    method: str,
    path: str = "",
    body: bytes | None = None,
    content_type: str = "application/octet-stream",
) -> bytes:
    if not BLOB_TOKEN:
        raise RuntimeError("Falta la variable de entorno BLOB_READ_WRITE_TOKEN.")
    request = Request(
        f"{BLOB_HOST}/{path}" if path else BLOB_HOST,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {BLOB_TOKEN}",
            "x-api-version": "7",
            "Content-Type": content_type,
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


def prediction_store():
    """Almacén del registro vivo: Vercel Blob si hay token, memoria si no."""
    if not BLOB_TOKEN:
        return memory_store()

    def load() -> dict:
        try:
            stored = json.loads(blob_request("GET", PREDICTIONS_PATH).decode("utf-8"))
        except HTTPError as error:
            if error.code != 404:
                raise
            return {}
        if isinstance(stored, list):
            return {"predictions": stored, "pauses": []}
        return stored if isinstance(stored, dict) else {}

    def save(data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        blob_request(
            "PUT",
            PREDICTIONS_PATH,
            payload,
            "application/json; charset=utf-8",
        )

    return load, save


def live_monitor() -> LiveMonitor:
    """Monitor vivo compartido por las invocaciones de la función."""
    global _monitor
    if _monitor is None:
        _monitor = LiveMonitor(
            *prediction_store(),
            spec=config_alertas.spec(),
            payout=config_alertas.modelo_pago(),
            kelly_config=KellyConfig(
                fraction=config_alertas.FRACCION_KELLY,
                max_fraction=config_alertas.TOPE_EXPOSICION,
                min_observations=config_alertas.OBSERVACIONES_MINIMAS,
                min_hits=config_alertas.ACIERTO_MINIMO,
            ),
            bankroll=config_alertas.BANCO_INICIAL,
            backfill_rounds=config_alertas.RONDAS_BACKFILL,
            max_records=config_alertas.RONDAS_GUARDADAS,
        )
    return _monitor


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
    horario = horario_loteka.estado()
    # Fuera del horario de publicación (07:00-22:00, hora de R. Dominicana)
    # no se consulta el origen: se sirven los datos guardados.
    if horario["abierto"]:
        try:
            current = merge_results(rows, extract_results(fetch_page()))
            if BLOB_TOKEN and serialize_rows(current) != serialize_rows(rows):
                blob_request(
                    "PUT",
                    BLOB_PATH,
                    serialize_rows(current),
                    "text/csv; charset=utf-8",
                )
            rows = current
        except Exception as exception:
            error = str(exception)

    recovery_error: str | None = None
    recovery = None
    try:
        recovery = recovery_for_rows(rows)
    except Exception as exception:
        recovery_error = str(exception)

    monitor_error: str | None = None
    monitor_payload: dict = {}
    try:
        monitor_payload = live_monitor().update(rows)
    except Exception as exception:
        monitor_error = str(exception)

    return {
        "results": rows,
        "last_sync": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sync_error": error,
        "recovery": recovery,
        "recovery_error": recovery_error,
        "predictions": monitor_payload.get("predictions", []),
        "pauses": monitor_payload.get("pauses", []),
        "predictions_error": monitor_error,
        "alert": monitor_payload.get("alert"),
        "alert_stats": monitor_payload.get("alert_stats"),
        "alert_history": monitor_payload.get("alert_history", []),
        "prediction_stats": monitor_payload.get("prediction_stats"),
        "refresh_interval_seconds": INTERVALO_SINCRONIZACION,
        "horario": horario,
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if not authorized(self):
            body = b'{"error":"Autenticacion requerida"}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
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
