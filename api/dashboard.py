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
try:
    from auth_utils import authorized
except ImportError:
    from api.auth_utils import authorized
from sync_ruleta_express import extract_results, fetch_page


ROOT = Path(__file__).resolve().parents[1]
LOCAL_CSV = ROOT / "Resultados_RuletaExpress.csv"
LOCAL_PREDICTIONS = ROOT / "ruleta_predictions.json"
BLOB_PATH = "Resultados_RuletaExpress.csv"
PREDICTIONS_PATH = "ruleta_predictions.json"
def get_blob_token() -> str | None:
    token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if token:
        return token.strip().strip('"').strip("'")
    for key, value in os.environ.items():
        if "READ_WRITE_TOKEN" in key and value:
            return value.strip().strip('"').strip("'")
    return None


BLOB_TOKEN = get_blob_token()
BLOB_HOST = "https://blob.vercel-storage.com"
EXPECTED_COLUMNS = ["Fecha", "Hora", "No_Sorteo", "Numero"]

_cached_draw: str | None = None
_cached_recovery: dict | None = None
_local_predictions: list[dict] = []
_local_prediction_store: dict = {"predictions": [], "pauses": []}
_blob_urls: dict[str, str] = {}
_last_storage_error: str | None = None


def _resolve_blob_url(path: str) -> str:
    """Find the public URL of a blob stored in Vercel Blob by its pathname."""
    if path in _blob_urls:
        return _blob_urls[path]
    if not BLOB_TOKEN:
        raise RuntimeError("Falta la variable de entorno BLOB_READ_WRITE_TOKEN.")
    base_prefix = path.rsplit(".", 1)[0]
    list_request = Request(
        f"{BLOB_HOST}?prefix={base_prefix}&limit=100",
        method="GET",
        headers={
            "Authorization": f"Bearer {BLOB_TOKEN}",
            "x-api-version": "7",
        },
    )
    with urlopen(list_request, timeout=20) as response:
        result = json.loads(response.read())
    blobs = result.get("blobs", [])
    # 1. Exact pathname match
    for blob in blobs:
        if blob.get("pathname") == path:
            url = blob.get("url", "")
            if url:
                _blob_urls[path] = url
                return url
    # 2. Prefix match (in case of random suffix), sort newest first
    matching = [
        blob for blob in blobs
        if blob.get("pathname", "").startswith(base_prefix)
    ]
    if matching:
        matching.sort(key=lambda b: b.get("uploadedAt", ""), reverse=True)
        url = matching[0].get("url", "")
        if url:
            _blob_urls[path] = url
            return url
    raise HTTPError(f"{BLOB_HOST}/{path}", 404, "Not Found", None, None)


def blob_request(
    method: str,
    path: str = "",
    body: bytes | None = None,
    content_type: str = "application/octet-stream",
) -> bytes:
    if not BLOB_TOKEN:
        raise RuntimeError("Falta la variable de entorno BLOB_READ_WRITE_TOKEN.")
    # GET: resolve the actual public URL via the list API, then fetch with cache=0.
    if method == "GET" and path:
        url = _resolve_blob_url(path)
        separator = "&" if "?" in url else "?"
        fetch_url = f"{url}{separator}cache=0"
        fetch_req = Request(
            fetch_url,
            headers={"Cache-Control": "no-cache"},
        )
        try:
            with urlopen(fetch_req, timeout=20) as response:
                return response.read()
        except HTTPError as error:
            if error.code in (401, 403):
                auth_req = Request(
                    fetch_url,
                    headers={
                        "Authorization": f"Bearer {BLOB_TOKEN}",
                        "Cache-Control": "no-cache",
                    },
                )
                with urlopen(auth_req, timeout=20) as response:
                    return response.read()
            raise
    # PUT / other methods: send to the management endpoint.
    headers = {
        "Authorization": f"Bearer {BLOB_TOKEN}",
        "x-api-version": "7",
        "Content-Type": content_type,
    }
    query_params = ""
    if method == "PUT":
        headers["x-add-random-suffix"] = "false"
        headers["x-allow-overwrite"] = "true"
        query_params = "?addRandomSuffix=false&allowOverwrite=true"
    request_url = f"{BLOB_HOST}/{path}{query_params}" if path else BLOB_HOST
    request = Request(
        request_url,
        data=body,
        method=method,
        headers=headers,
    )
    with urlopen(request, timeout=20) as response:
        data = response.read()
    # After a successful PUT, cache the public URL returned by the API
    if method == "PUT" and path:
        try:
            result = json.loads(data)
            url = result.get("url", "")
            if url:
                _blob_urls[path] = url
                if result.get("pathname"):
                    _blob_urls[result["pathname"]] = url
        except (json.JSONDecodeError, KeyError):
            pass
    return data


def read_csv_bytes(data: bytes) -> list[dict[str, str]]:
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    missing = [column for column in EXPECTED_COLUMNS if column not in (rows[0] if rows else {})]
    if rows and missing:
        raise ValueError(f"Faltan columnas requeridas en el CSV: {missing}")
    return [{column: row.get(column, "") for column in EXPECTED_COLUMNS} for row in rows]


def read_rows() -> list[dict[str, str]]:
    global _last_storage_error
    if BLOB_TOKEN:
        try:
            return read_csv_bytes(blob_request("GET", BLOB_PATH))
        except Exception as error:
            if not (isinstance(error, HTTPError) and error.code == 404):
                _last_storage_error = f"Error al leer CSV de Vercel Blob: {error}"
    try:
        return read_csv_bytes(LOCAL_CSV.read_bytes())
    except Exception as error:
        return []


def serialize_rows(rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=EXPECTED_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def read_prediction_store() -> dict:
    global _local_prediction_store, _last_storage_error
    if BLOB_TOKEN:
        try:
            data = blob_request("GET", PREDICTIONS_PATH)
            stored = json.loads(data.decode("utf-8"))
            if isinstance(stored, list):
                return {"predictions": stored, "pauses": []}
            return {
                "predictions": stored.get("predictions", []),
                "pauses": stored.get("pauses", []),
            }
        except Exception as error:
            if not (isinstance(error, HTTPError) and error.code == 404):
                _last_storage_error = f"Error al leer predicciones de Vercel Blob: {error}"
    if LOCAL_PREDICTIONS.exists():
        try:
            stored = json.loads(LOCAL_PREDICTIONS.read_text(encoding="utf-8"))
            if isinstance(stored, list):
                return {"predictions": stored, "pauses": []}
            return {
                "predictions": stored.get("predictions", []),
                "pauses": stored.get("pauses", []),
            }
        except Exception:
            pass
    return {
        "predictions": list(_local_prediction_store["predictions"]),
        "pauses": list(_local_prediction_store["pauses"]),
    }


def save_prediction_store(store: dict) -> None:
    global _local_prediction_store, _local_predictions, _last_storage_error
    _local_prediction_store = {
        "predictions": list(store["predictions"]),
        "pauses": list(store["pauses"]),
    }
    _local_predictions = list(store["predictions"])
    payload = json.dumps(_local_prediction_store, ensure_ascii=False, indent=2).encode("utf-8")
    if BLOB_TOKEN:
        try:
            blob_request(
                "PUT",
                PREDICTIONS_PATH,
                payload,
                "application/json; charset=utf-8",
            )
            _last_storage_error = None
        except Exception as error:
            _last_storage_error = f"Error guardando en Vercel Blob: {error}"
    else:
        try:
            LOCAL_PREDICTIONS.write_bytes(payload)
        except Exception:
            pass


def read_predictions() -> list[dict]:
    return read_prediction_store()["predictions"]


def draw_number(draw: str) -> int:
    return int(draw.rsplit(" ", 1)[-1])


def update_predictions(
    store: dict,
    rows: list[dict[str, str]],
    recovery: dict,
) -> tuple[list[dict], list[dict]]:
    predictions = list(store.get("predictions", []))
    pauses = list(store.get("pauses", []))
    draws = sorted(
        (row for row in rows if row.get("No_Sorteo")),
        key=lambda row: draw_number(row["No_Sorteo"]),
    )
    by_source = {item["source_draw"] for item in predictions}
    changed = False

    for prediction in predictions:
        if prediction.get("status") != "pendiente":
            continue
        source_number = draw_number(prediction["source_draw"])
        next_draw = next(
            (row for row in draws if draw_number(row["No_Sorteo"]) > source_number),
            None,
        )
        if next_draw is None:
            continue
        source_number = draw_number(prediction["source_draw"])
        next_number = draw_number(next_draw["No_Sorteo"])
        if next_number > source_number + 1:
            prediction.update(
                {
                    "status": "omitida_pausa",
                    "observed_draw": next_draw["No_Sorteo"],
                    "actual_number": None,
                    "matched": "no evaluada",
                }
            )
            changed = True
            continue
        actual = int(next_draw["Numero"])
        markov_rank = next(
            (
                index + 1
                for index, item in enumerate(prediction["markov"])
                if item["number"] == actual
            ),
            None,
        )
        heuristic_rank = next(
            (
                index + 1
                for index, item in enumerate(prediction["heuristic"])
                if item["number"] == actual
            ),
            None,
        )
        prediction.update(
            {
                "status": "acierto" if markov_rank or heuristic_rank else "fallo",
                "observed_draw": next_draw["No_Sorteo"],
                "actual_number": actual,
                "markov_rank": markov_rank,
                "heuristic_rank": heuristic_rank,
                "matched": (
                    "ambos"
                    if markov_rank and heuristic_rank
                    else "markov"
                    if markov_rank
                    else "heuristico"
                    if heuristic_rank
                    else "ninguno"
                ),
            }
        )
        changed = True

    if rows:
        source_draw = rows[0]["No_Sorteo"]
        source_number = draw_number(source_draw)
        previous_draws = [
            draw_number(item["source_draw"])
            for item in predictions
            if item.get("source_draw")
        ]
        previous_draw = max(previous_draws, default=None)
        if previous_draw is not None and source_number > previous_draw + 1:
            pause = {
                "from_draw": f"No. {previous_draw + 1}",
                "to_draw": f"No. {source_number - 1}",
                "skipped_count": source_number - previous_draw - 1,
                "detected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            if not any(
                item["from_draw"] == pause["from_draw"]
                and item["to_draw"] == pause["to_draw"]
                for item in pauses
            ):
                pauses.append(pause)
                changed = True
        if source_draw not in by_source:
            predictions.append(
                {
                    "source_draw": source_draw,
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "status": "pendiente",
                    "markov": recovery["markov_forecast"],
                    "heuristic": recovery["heuristic_forecast"],
                }
            )
            changed = True

    if changed:
        save_prediction_store({"predictions": predictions, "pauses": pauses})
    return sorted(
        predictions,
        key=lambda item: draw_number(item["source_draw"]),
        reverse=True,
    ), sorted(pauses, key=lambda item: draw_number(item["from_draw"]), reverse=True)


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
    global BLOB_TOKEN
    BLOB_TOKEN = get_blob_token()
    rows = read_rows()
    error: str | None = None
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
    predictions: list[dict] = []
    predictions_error: str | None = None
    try:
        recovery = recovery_for_rows(rows)
    except Exception as exception:
        recovery_error = str(exception)
    if recovery is not None:
        try:
            store = read_prediction_store()
            predictions, pauses = update_predictions(store, rows, recovery)
        except Exception as exception:
            predictions_error = str(exception)
            pauses = []
    else:
        pauses = []

    storage_diagnostic: str | None = None
    if not BLOB_TOKEN:
        relevant_keys = [
            k for k in os.environ.keys()
            if any(w in k.upper() for w in ("BLOB", "TOKEN", "STORAGE", "READ_WRITE"))
        ]
        storage_diagnostic = (
            f"ADVERTENCIA: No se encontró token de Vercel Blob (variables detectadas: {relevant_keys or 'ninguna'}). "
            "Para activarlo: asegúrate de que el Blob Store esté conectado con la casilla 'Production' y haz clic en 'Redeploy' en Vercel."
        )
    elif _last_storage_error:
        token_info = (
            f"Token en uso: {BLOB_TOKEN[:14]}...{BLOB_TOKEN[-6:]} (longitud {len(BLOB_TOKEN)})"
            if BLOB_TOKEN
            else "Sin token"
        )
        storage_diagnostic = f"{_last_storage_error} ({token_info})"

    if storage_diagnostic:
        predictions_error = (
            f"{predictions_error} · {storage_diagnostic}"
            if predictions_error
            else storage_diagnostic
        )

    return {
        "results": rows,
        "last_sync": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sync_error": error,
        "recovery": recovery,
        "recovery_error": recovery_error,
        "predictions": predictions,
        "pauses": pauses,
        "predictions_error": predictions_error,
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
