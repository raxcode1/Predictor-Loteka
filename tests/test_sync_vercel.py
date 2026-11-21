"""Sincronización en Vercel: guardado del CSV, caché y protección contra pisarlo."""
from __future__ import annotations

import pytest


def _fila(n: int, fecha: str = "20/11/2026") -> dict[str, str]:
    return {"Fecha": fecha, "Hora": "10:00 AM", "No_Sorteo": f"No. {n}", "Numero": f"{n % 19:02d}"}


@pytest.fixture
def D(monkeypatch, tmp_path):
    from api import dashboard as D

    monkeypatch.setattr(D, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(D, "SUPABASE_KEY", "k")
    monkeypatch.setattr(D, "LOCAL_CSV", tmp_path / "no_existe.csv")
    monkeypatch.setattr(D, "_rows_cache", None)
    monkeypatch.setattr(D, "_rows_cache_at", 0.0)
    monkeypatch.setattr(D, "_last_csv_hash", None)
    monkeypatch.setattr(D, "_lectura_remota_fallida", False)
    monkeypatch.setattr(D.horario_loteka, "estado", lambda: {"abierto": True})
    # Sin red: la portada devuelve dos sorteos nuevos y no hay huecos que rellenar.
    monkeypatch.setattr(D, "fetch_page", lambda: "html")
    monkeypatch.setattr(D, "extract_results", lambda html: [_fila(102), _fila(101)])
    monkeypatch.setattr(D, "backfill_rows", lambda rows: rows)
    monkeypatch.setattr(D, "live_monitor", lambda: (_ for _ in ()).throw(RuntimeError("sin monitor")))
    monkeypatch.setattr(D, "almacen_papel", lambda: (_ for _ in ()).throw(RuntimeError("sin papel")))
    return D


def _nube(D, monkeypatch, inicial: bytes | None, fallar_lectura: bool = False):
    nube = {} if inicial is None else {"Resultados_RuletaExpress.csv": inicial}
    lecturas = []

    def blob(method, pathname="", body=None, content_type=""):
        if method == "PUT":
            nube[pathname] = body
            return b""
        lecturas.append(pathname)
        if fallar_lectura:
            raise RuntimeError("Supabase Storage GET -> timed out")
        if pathname not in nube:
            raise RuntimeError('{"error":"not_found","message":"Object not found"}')
        return nube[pathname]

    monkeypatch.setattr(D, "blob_request", blob)
    return nube, lecturas


def test_cron_ligero_guarda_los_sorteos_nuevos(D, monkeypatch):
    nube, _ = _nube(D, monkeypatch, D.serialize_rows([_fila(100)]))
    datos = D.dashboard(ligero=True)
    assert datos["results"][0]["No_Sorteo"] == "No. 102"
    guardado = D.read_csv_bytes(nube["Resultados_RuletaExpress.csv"])
    assert [r["No_Sorteo"] for r in guardado] == ["No. 102", "No. 101", "No. 100"]
    assert "quant" not in datos and datos["recovery"] is None


def test_reutiliza_el_csv_en_caliente(D, monkeypatch):
    _, lecturas = _nube(D, monkeypatch, D.serialize_rows([_fila(100)]))
    D.dashboard(ligero=True)
    D.dashboard(ligero=True)
    assert lecturas.count("Resultados_RuletaExpress.csv") == 1


def test_no_pisa_el_csv_si_no_pudo_leerlo(D, monkeypatch):
    nube, _ = _nube(D, monkeypatch, D.serialize_rows([_fila(n) for n in range(1, 100)]), fallar_lectura=True)
    antes = nube["Resultados_RuletaExpress.csv"]
    datos = D.dashboard(ligero=True)
    assert nube["Resultados_RuletaExpress.csv"] == antes
    assert "no se guarda" in datos["sync_error"]


def test_si_falla_el_guardado_reintenta_en_la_siguiente(D, monkeypatch):
    nube, lecturas = _nube(D, monkeypatch, D.serialize_rows([_fila(100)]))
    original = D.blob_request

    def put_falla(method, *args, **kwargs):
        if method == "PUT":
            raise RuntimeError("HTTP 402")
        return original(method, *args, **kwargs)

    monkeypatch.setattr(D, "blob_request", put_falla)
    assert "Error guardando" in D.dashboard(ligero=True)["sync_error"]
    monkeypatch.setattr(D, "blob_request", original)
    D.dashboard(ligero=True)
    assert len(D.read_csv_bytes(nube["Resultados_RuletaExpress.csv"])) == 3


def test_backfill_ignora_huecos_viejos(monkeypatch):
    import pandas as pd
    import sync_ruleta_express as sync

    frame = pd.DataFrame([_fila(10, "01/11/2026"), _fila(5, "01/11/2026"), _fila(20, "20/11/2026"), _fila(19, "20/11/2026")])
    pedidos = []
    monkeypatch.setattr(sync, "fetch_history_date", lambda day: pedidos.append(day) or [])
    sync.backfill_missing_draws(frame, [], recent_days=1)
    # Hueco 11-18: entre el 01/11 y el 20/11, toca hoy -> se consulta entero.
    # Hueco 6-9: sólo del 01/11 -> se ignora (no añade consultas propias).
    assert len(pedidos) == 20
    pedidos.clear()
    frame = pd.DataFrame([_fila(10, "01/11/2026"), _fila(5, "01/11/2026"), _fila(11, "20/11/2026")])
    sync.backfill_missing_draws(frame, [], recent_days=1)
    assert pedidos == []
