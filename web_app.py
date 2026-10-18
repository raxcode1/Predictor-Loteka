from __future__ import annotations

import argparse
import json
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd

import state_recovery
from sync_ruleta_express import CSV_PATH, run_once


HOST = "127.0.0.1"
SYNC_INTERVAL = 15
TIME_ZONE = ZoneInfo("America/Santo_Domingo")
WEB_PATH = Path(__file__).with_name("web")


class DashboardState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.last_draw: str | None = None
        self.last_sync: str | None = None
        self.sync_error: str | None = None
        self.recovery: dict | None = None
        self.recovery_error: str | None = None

    def update_recovery(self) -> None:
        recovery = state_recovery.build_state_recovery(CSV_PATH)
        with self.lock:
            self.recovery = recovery
            self.recovery_error = None

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "results": read_results(),
                "last_sync": self.last_sync,
                "sync_error": self.sync_error,
                "recovery": self.recovery,
                "recovery_error": self.recovery_error,
            }


def read_results() -> list[dict[str, str]]:
    if not CSV_PATH.exists():
        return []
    frame = pd.read_csv(CSV_PATH, dtype=str).fillna("")
    expected = ("Fecha", "Hora", "No_Sorteo", "Numero")
    missing = [column for column in expected if column not in frame.columns]
    if missing:
        raise ValueError(f"Faltan columnas requeridas en el CSV: {missing}")
    return frame[list(expected)].to_dict(orient="records")


def latest_draw() -> str | None:
    results = read_results()
    return results[0]["No_Sorteo"] if results else None


def sync_loop(state: DashboardState) -> None:
    while True:
        try:
            with state.lock:
                previous_draw = state.last_draw
            with state.lock:
                run_once(CSV_PATH)
                current_draw = latest_draw()
                state.last_sync = datetime.now(TIME_ZONE).isoformat(timespec="seconds")
                state.sync_error = None
                state.last_draw = current_draw
            if current_draw and current_draw != previous_draw:
                try:
                    state.update_recovery()
                except Exception as error:
                    with state.lock:
                        state.recovery_error = str(error)
        except Exception as error:
            with state.lock:
                state.sync_error = str(error)
                state.last_sync = datetime.now(TIME_ZONE).isoformat(timespec="seconds")
        time.sleep(SYNC_INTERVAL)


class RequestHandler(BaseHTTPRequestHandler):
    state: DashboardState

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/dashboard":
            self.send_json(self.state.snapshot())
            return
        if path == "/" or path == "/index.html":
            self.send_file(WEB_PATH / "index.html", "text/html; charset=utf-8")
            return
        if path == "/app.js":
            self.send_file(WEB_PATH / "app.js", "text/javascript; charset=utf-8")
            return
        if path == "/styles.css":
            self.send_file(WEB_PATH / "styles.css", "text/css; charset=utf-8")
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def send_json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Muestra resultados de Ruleta Express y los sincroniza automáticamente."
    )
    parser.add_argument("--host", default=HOST, help="Interfaz donde escuchar.")
    parser.add_argument("--port", type=int, default=8000, help="Puerto HTTP.")
    args = parser.parse_args()

    state = DashboardState()
    try:
        state.last_draw = latest_draw()
        state.update_recovery()
    except Exception as error:
        state.recovery_error = str(error)

    RequestHandler.state = state
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    threading.Thread(target=sync_loop, args=(state,), daemon=True).start()
    print(
        f"Dashboard disponible en http://{args.host}:{args.port} "
        f"(sincronización cada {SYNC_INTERVAL} segundos)",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
