"""Tests de integración. Ninguno consulta Loteka ni Supabase (todo inyectado)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import urllib.request
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant import integration
from quant.data import from_rows, load_csv
from quant.report import build_report, summarize, to_markdown
from tests.conftest import CSV_PATH, ROOT, markov_chain, rows_from_values

APP_JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def small_rows() -> list[dict[str, str]]:
    return rows_from_values(markov_chain(3000, seed=11, stay_out=0.8), start=5000)


@pytest.fixture(scope="module")
def small_csv(tmp_path_factory, small_rows) -> Path:
    path = tmp_path_factory.mktemp("csv") / "Resultados_RuletaExpress.csv"
    pd.DataFrame(small_rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path


# ---------------------------------------------------------------- CSV -> informe
def test_real_csv_loads_with_expected_schema():
    dataset = load_csv(CSV_PATH)
    assert len(dataset) > 30000
    assert dataset.values.min() >= 0 and dataset.values.max() <= 18
    assert np.all(np.diff(dataset.draw_ids) > 0)


def test_report_end_to_end_is_serializable_and_abstains(small_csv, monkeypatch):
    monkeypatch.delenv("RULETA_TRADING_MODE", raising=False)
    report = build_report(load_csv(small_csv), mc_sims=100)
    json.dumps(report)  # sin tipos numpy
    summary = summarize(report)
    json.dumps(summary)
    assert report["decision"]["trading_mode"] == "paper"
    assert report["decision"]["signals_allowed"] is False
    assert report["dataset"]["sha256"] == load_csv(small_csv).sha256
    markdown = to_markdown(report)
    assert "Dataset SHA-256" in markdown and "Test bloqueado" in markdown


def test_report_is_reproducible_by_seed(small_csv):
    a = build_report(load_csv(small_csv), mc_sims=100, seed=3)
    b = build_report(load_csv(small_csv), mc_sims=100, seed=3)
    for key in ("walk_forward", "goodness_of_fit", "monte_carlo", "bayesian"):
        assert json.dumps(a[key], sort_keys=True) == json.dumps(b[key], sort_keys=True)


def test_report_requires_minimum_rows():
    with pytest.raises(ValueError):
        build_report(from_rows(rows_from_values([1, 2, 3])))


def test_cli_writes_reports(small_csv, tmp_path):
    from quant.__main__ import main

    assert main(["--csv", str(small_csv), "--out-dir", str(tmp_path), "--mc-sims", "50"]) == 0
    data = json.loads((tmp_path / "informe_quant.json").read_text(encoding="utf-8"))
    assert data["decision"]["status"] in ("SIN VENTAJA", "VENTAJA_CANDIDATA")
    assert (tmp_path / "informe_quant.md").exists()


# ---------------------------------------------------------------- parsers / backfill
HOME_HTML = """
<script>var fechaServidor = '07/11/2026';</script>
<div id="result-chanceexpress2"><ul>
<li><div class="x"><span class="h">09:03 PM</span><span class="n">No. 253153</span><span>a</span><span class="b">07</span></div></li>
<li><div class="x"><span class="h">09:08 PM</span><span class="n">No. 253154</span><span>a</span><span class="b">15</span></div></li>
</ul></div>
"""

HISTORY_HTML = """
<li class="r"><span>08:58 PM</span><span>No. 253152</span><span class="numero">09</span></li>
<li class="r"><span>08:53 PM</span><span>No. 253151</span><span class="numero">13</span></li>
"""


def test_extract_results_parses_home():
    import sync_ruleta_express as sync

    rows = sync.extract_results(HOME_HTML)
    assert [r["No_Sorteo"] for r in rows] == ["No. 253154", "No. 253153"]
    assert rows[0] == {"Fecha": "07/11/2026", "Hora": "09:08 PM", "No_Sorteo": "No. 253154", "Numero": "15"}
    with pytest.raises(RuntimeError):
        sync.extract_results("<html></html>")


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self.body = body.encode("utf-8")

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


def test_fetch_history_preserves_query_and_parses(monkeypatch):
    import sync_ruleta_express as sync

    seen = {}

    def fake_urlopen(request, timeout=30):
        seen["url"] = request.full_url
        return _FakeResponse(HISTORY_HTML)

    monkeypatch.setattr(sync, "urlopen", fake_urlopen)
    rows = sync.fetch_history_date(date(2026, 11, 7))
    assert "getRuletaExpress.php?fechaSeleccionada=07%2F11%2F2026" in seen["url"]
    assert {r["No_Sorteo"] for r in rows} == {"No. 253152", "No. 253151"}


def test_fetch_history_rejects_homepage_from_proxy(monkeypatch):
    import sync_ruleta_express as sync

    monkeypatch.setattr(sync, "urlopen", lambda request, timeout=30: _FakeResponse(HOME_HTML))
    with pytest.raises(RuntimeError):
        sync.fetch_history_date(date(2026, 11, 7))


def test_backfill_recovers_only_missing_draws(monkeypatch):
    import sync_ruleta_express as sync

    frame = pd.DataFrame(
        [
            {"Fecha": "07/11/2026", "Hora": "09:08 PM", "No_Sorteo": "No. 253154", "Numero": "15"},
            {"Fecha": "07/11/2026", "Hora": "08:53 PM", "No_Sorteo": "No. 253151", "Numero": "13"},
        ]
    )
    monkeypatch.setattr(sync, "urlopen", lambda request, timeout=30: _FakeResponse(HISTORY_HTML))
    recovered = sync.backfill_missing_draws(frame, [])
    assert [r["No_Sorteo"] for r in recovered] == ["No. 253152"]


# ---------------------------------------------------------------- integración payload
def test_attach_gates_a_fired_alert(small_rows, monkeypatch):
    monkeypatch.delenv("RULETA_TRADING_MODE", raising=False)
    payload = {
        "alert": {"available": True, "fired": True, "stake_applied": True, "stake_fraction": 0.02, "stake_amount": 20.0},
        "alert_stats": {"alerts": 30, "alert_hits": 6},
    }
    out = integration.attach(payload, rows=small_rows)
    assert out["quant_error"] is None
    assert out["quant"]["decision"]["status"] in ("SIN VENTAJA", "VENTAJA_CANDIDATA")
    assert out["decision"]["signals_allowed"] is False
    assert out["alert"]["fired"] is False and out["alert"]["rule_fired"] is True
    assert out["alert"]["stake_amount"] == 0.0
    assert out["m4_gate"]["passed"] is False


def test_attach_reports_explicit_error_on_bad_rows():
    out = integration.attach({"alert": None}, rows=rows_from_values([1, 2, 3]))
    assert out["quant"] is None
    assert "ValueError" in out["quant_error"]
    assert out["decision"]["status"] == "SIN VENTAJA"


def _keys_used_by_frontend(prefix: str) -> set[str]:
    return set(re.findall(rf"{prefix}\.([a-z_0-9]+)", APP_JS))


def assert_frontend_contract(payload: dict) -> None:
    """Cada campo de primer nivel que app.js lee existe en el payload."""
    # `horario` y `refresh_interval_seconds` son opcionales: app.js comprueba su presencia.
    for key in _keys_used_by_frontend("data") - {"refresh_interval_seconds", "horario"}:
        assert key in payload, f"el frontend lee data.{key} y el payload no lo trae"
    if payload.get("quant"):
        for key in _keys_used_by_frontend("quant"):
            assert key in payload["quant"], f"falta quant.{key}"
    for key in _keys_used_by_frontend("decision"):
        if key in ("reasons", "status", "signals_allowed", "trading_mode"):
            assert key in payload["decision"]


# ---------------------------------------------------------------- servidor local (web_app)
def test_web_app_http_endpoints(small_csv, tmp_path, monkeypatch):
    import web_app

    monkeypatch.setattr(web_app, "CSV_PATH", small_csv)
    monkeypatch.setattr(web_app, "REGISTRO_PATH", tmp_path / "registro_live.json")
    state = web_app.DashboardState()
    state.update_monitor()
    state.update_quant()
    web_app.RequestHandler.state = state
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_app.RequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/api/dashboard", timeout=30) as response:
            payload = json.loads(response.read())
        assert payload["api_version"] == 2
        assert payload["decision"]["trading_mode"] == "paper"
        assert len(payload["results"]) == 3000
        assert_frontend_contract(payload)
        with urllib.request.urlopen(f"{base}/healthz", timeout=10) as response:
            assert json.loads(response.read()) == {"ok": True}
        with urllib.request.urlopen(f"{base}/readyz", timeout=10) as response:
            assert json.loads(response.read())["ready"] is True
        with urllib.request.urlopen(f"{base}/api/v2/analysis", timeout=10) as response:
            assert json.loads(response.read())["quant"]["dataset"]["rows"] == 3000
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------- Render (Flask)
def test_render_app_endpoints(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.delenv("RENDER_DATA_DIR", raising=False)
    from auth_utils import SESSION_COOKIE, create_session

    import render_app

    render_app.estado.actualizar_quant()
    client = render_app.app.test_client()
    assert client.get("/healthz").get_json()["ok"] is True
    ready = client.get("/readyz")
    assert ready.status_code in (200, 503) and "checks" in ready.get_json()
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/api/v2/analysis").status_code == 401
    client.set_cookie(SESSION_COOKIE, create_session("admin"))
    payload = client.get("/api/dashboard").get_json()
    assert payload["api_version"] == 2
    assert payload["decision"]["signals_allowed"] is False
    assert_frontend_contract(payload)
    assert client.get("/api/v2/analysis").get_json()["quant_error"] is None


# ---------------------------------------------------------------- Vercel (almacenamiento mock)
def test_vercel_dashboard_with_mock_storage(small_rows, monkeypatch):
    import dashboard as vercel

    from live_monitor import LiveMonitor, memory_store

    monkeypatch.setattr(vercel, "read_rows", lambda: list(small_rows))
    monkeypatch.setattr(vercel.horario_loteka, "estado", lambda: {"abierto": False})
    monitor = LiveMonitor(*memory_store())
    monkeypatch.setattr(vercel, "live_monitor", lambda: monitor)
    payload = vercel.dashboard()
    assert payload["sync_error"] is None
    assert payload["api_version"] == 2
    assert payload["decision"]["status"] in ("SIN VENTAJA", "VENTAJA_CANDIDATA")
    assert payload["decision"]["signals_allowed"] is False
    assert payload["recovery"]["sample_size"] == 3000  # campo heredado intacto
    assert_frontend_contract(payload)


# ---------------------------------------------------------------- Worker
@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible")
def test_cloudflare_worker_forwards_path_and_restricts_cors():
    result = subprocess.run(
        ["node", "--test", str(ROOT / "tests" / "worker.test.mjs")],
        check=False, capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr
