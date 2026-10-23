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
from live_monitor import LiveMonitor, json_file_store, memory_store
from m4_engine.kelly import KellyConfig
from sync_ruleta_express import extract_results, fetch_page


# Configurar URL usando Worker si está disponible
WORKER_URL = os.environ.get("CLOUDFLARE_WORKER_URL", "").strip().rstrip("/")
if WORKER_URL:
    os.environ["SCRAPER_URL"] = f"{WORKER_URL}/"
    import sync_ruleta_express
    sync_ruleta_express.URL = f"{WORKER_URL}/"
    import scraper_web
    scraper_web.URL = f"{WORKER_URL}/"
PREDICTIONS_PATH = "ruleta_predictions.json"
LOCAL_CSV = Path(__file__).resolve().parents[1] / "Resultados_RuletaExpress.csv"
EXPECTED_COLUMNS = ["Fecha", "Hora", "No_Sorteo", "Numero"]

# Compatibilidad con nombres alternativos
SUPABASE_URL = (
    os.environ.get("SUPABASE_URL")
    or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    or os.environ.get("SUPABASE_PROJECT_URL")
    or ""
).strip().rstrip("/")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_SERVICE_KEY")
    or os.environ.get("SUPABASE_SERVICE_ROLE")
    or os.environ.get("SUPABASE_ANON_KEY")
    or os.environ.get("SUPABASE_KEY")
    or ""
).strip()
SUPABASE_BUCKET = (
    os.environ.get("SUPABASE_BUCKET")
    or os.environ.get("SUPABASE_STORAGE_BUCKET")
    or os.environ.get("STORAGE_BUCKET")
    or "ruleta-data"
).strip()


_cached_draw: str | None = None
_cached_recovery: dict | None = None
_monitor: LiveMonitor | None = None
_last_csv_hash: str | None = None


def storage_object_url(pathname: str) -> str:
    if not SUPABASE_URL or not SUPABASE_BUCKET:
        raise RuntimeError("Faltan SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY o SUPABASE_BUCKET.")
    from urllib.parse import quote
    clean = pathname.strip().lstrip("/")
    # Algunos deployments de Supabase Storage requieren /public/ o rutas alternativas; probar ruta objeto directa
    return f"{SUPABASE_URL}/storage/v1/object/{quote(SUPABASE_BUCKET, safe='')} /{quote(clean)}".replace(" /", "/")


def storage_request(method: str, pathname: str, body: bytes | None = None, content_type: str = "application/json; charset=utf-8") -> bytes:
    if not SUPABASE_URL:
        raise RuntimeError("Falta SUPABASE_URL.")
    if not SUPABASE_KEY:
        raise RuntimeError("Falta SUPABASE_SERVICE_ROLE_KEY.")
    if not SUPABASE_BUCKET:
        raise RuntimeError("Falta SUPABASE_BUCKET.")
    from urllib.parse import quote
    clean = pathname.strip().lstrip("/")
    # No codificar el nombre del bucket, solo el path
    path_enc = quote(clean, safe='')
    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{path_enc}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "apikey": SUPABASE_KEY,
        "Content-Type": content_type,
        "User-Agent": "ruleta-express-vercel",
    }
    if method == "PUT":
        headers["x-upsert"] = "true"
        headers["cache-control"] = "no-cache"
    request = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=60) as response:
            return response.read()
    except HTTPError as error:
        try:
            detail = error.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        raise RuntimeError(f"Supabase Storage {method} {pathname} -> HTTP {error.code}: {detail[:500]}") from error
    except Exception as error:
        raise RuntimeError(f"Supabase Storage {method} {pathname} -> {error}") from error


def blob_request(
    method: str,
    pathname: str = "",
    body: bytes | None = None,
    content_type: str = "application/octet-stream",
) -> bytes:
    return storage_request(method, pathname, body, content_type)


def read_csv_bytes(data: bytes) -> list[dict[str, str]]:
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    missing = [column for column in EXPECTED_COLUMNS if column not in (rows[0] if rows else {})]
    if rows and missing:
        raise ValueError(f"Faltan columnas requeridas en el CSV: {missing}")
    return [{column: row.get(column, "") for column in EXPECTED_COLUMNS} for row in rows]


def read_rows() -> list[dict[str, str]]:
    "Carga el CSV remoto y combina el hist��rico versionado como respaldo/base."
    local_rows: list[dict[str, str]] = []
    if LOCAL_CSV.exists():
        try:
            local_rows = read_csv_bytes(LOCAL_CSV.read_bytes())
        except Exception:
            local_rows = []
    try:
        remote_rows = read_csv_bytes(blob_request("GET", "Resultados_RuletaExpress.csv"))
    except Exception:
        return local_rows
    return merge_results(local_rows, remote_rows)


def serialize_rows(rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=EXPECTED_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def prediction_store():
    """Almacén del registro vivo: Supabase Storage."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Faltan SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY o SUPABASE_BUCKET.")

    def load() -> dict:
        try:
            raw = blob_request("GET", PREDICTIONS_PATH)
        except Exception as error:
            message = str(error)
            if "404" in message:
                return {}
            raise
        try:
            stored = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "El registro de predicciones está dañado."
            ) from error
        if isinstance(stored, list):
            return {"predictions": stored, "pauses": []}
        if not isinstance(stored, dict):
            raise RuntimeError(
                "El registro de predicciones no tiene un formato válido."
            )
        return stored

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
    try:
        rows = read_rows()
    except Exception as e:
        rows = []
    error: str | None = None
    horario = horario_loteka.estado()
    # Fuera del horario de publicación (07:00-22:00, hora de R. Dominicana)
    # no se consulta el origen: se sirven los datos guardados.
    if horario["abierto"]:
        try:
            current = merge_results(rows, extract_results(fetch_page()))
            if SUPABASE_URL and SUPABASE_KEY and len(current) >= len(rows):
                try:
                    global _last_csv_hash
                    payload_csv = serialize_rows(current)
                    current_hash = str(hash(payload_csv))
                    if current_hash != _last_csv_hash and len(current) > len(rows):
                        blob_request(
                            "PUT",
                            "Resultados_RuletaExpress.csv",
                            payload_csv,
                            "text/csv; charset=utf-8",
                        )
                        _last_csv_hash = current_hash
                except Exception as ex:
                    error = f"Error guardando CSV en Supabase: {ex}"
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
        m = live_monitor()
        monitor_payload = m.update(rows)
    except Exception as e:
        monitor_error = str(e)
        monitor_payload = {}

    predictions = monitor_payload.get("predictions", [])

    return {
        "results": rows,
        "last_sync": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sync_error": error,
        "recovery": recovery,
        "recovery_error": recovery_error,
        "predictions": predictions,
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
            body = json.dumps({"results": [], "error": str(exception), "sync_error": str(exception)}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


handler = Handler
