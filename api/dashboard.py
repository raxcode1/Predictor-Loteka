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
BLOB_PATH = "Resultados_RuletaExpress.csv"
PREDICTIONS_PATH = "ruleta_predictions.json"
BLOB_TOKEN = os.environ.get("BLOB_READ_WRITE_TOKEN")
BLOB_HOST = "https://blob.vercel-storage.com"
EXPECTED_COLUMNS = ["Fecha", "Hora", "No_Sorteo", "Numero"]

_cached_draw: str | None = None
_cached_recovery: dict | None = None
_local_predictions: list[dict] = []
_local_prediction_store: dict = {"predictions": [], "pauses": []}


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


def read_prediction_store() -> dict:
    global _local_prediction_store
    if not BLOB_TOKEN:
        return {
            "predictions": list(_local_prediction_store["predictions"]),
            "pauses": list(_local_prediction_store["pauses"]),
        }
    try:
        stored = json.loads(blob_request("GET", PREDICTIONS_PATH).decode("utf-8"))
        if isinstance(stored, list):
            return {"predictions": stored, "pauses": []}
        return {
            "predictions": stored.get("predictions", []),
            "pauses": stored.get("pauses", []),
        }
    except HTTPError as error:
        if error.code != 404:
            raise
        return {"predictions": [], "pauses": []}


def save_prediction_store(store: dict) -> None:
    global _local_prediction_store, _local_predictions
    _local_prediction_store = {
        "predictions": list(store["predictions"]),
        "pauses": list(store["pauses"]),
    }
    _local_predictions = list(store["predictions"])
    payload = json.dumps(_local_prediction_store, ensure_ascii=False, indent=2).encode("utf-8")
    if BLOB_TOKEN:
        blob_request(
            "PUT",
            PREDICTIONS_PATH,
            payload,
            "application/json; charset=utf-8",
        )


def read_predictions() -> list[dict]:
    return read_prediction_store()["predictions"]


def draw_number(draw: str) -> int:
    return int(draw.rsplit(" ", 1)[-1])


def update_predictions(
    predictions: list[dict],
    rows: list[dict[str, str]],
    recovery: dict,
) -> tuple[list[dict], list[dict]]:
    store = read_prediction_store()
    pauses = store["pauses"]
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
            predictions, pauses = update_predictions(read_predictions(), rows, recovery)
        except Exception as exception:
            predictions_error = str(exception)
            pauses = []
    else:
        pauses = []

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
