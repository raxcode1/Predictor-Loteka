from __future__ import annotations

import csv
from datetime import datetime, timedelta

import numpy as np

from papel import modelo as M
from papel import registro as R
from papel.datos import cargar, siguiente_instante


def _csv(tmp_path, dias=40, seed=1, diurno_sin_repetir=True):
    """CSV sintético: 204 sorteos/día; de 08:33 a 19:58 no se repite el anterior."""
    rng = np.random.default_rng(seed)
    filas, sid, prev = [], 1000, 0
    for dnum in range(dias):
        t = datetime(2026, 1, 1, 5, 3) + timedelta(days=dnum)
        for turno in range(204):
            x = int(rng.integers(19))
            if diurno_sin_repetir and 42 <= turno <= 179:
                while x == prev:
                    x = int(rng.integers(19))
            filas.append({"Fecha": t.strftime("%d/%m/%Y"), "Hora": t.strftime("%I:%M %p"), "No_Sorteo": f"No. {sid}", "Numero": f"{x:02d}"})
            prev, sid, t = x, sid + 1, t + timedelta(minutes=5)
    path = tmp_path / "r.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["Fecha", "Hora", "No_Sorteo", "Numero"])
        w.writeheader()
        w.writerows(reversed(filas))
    return path


_csv_path = _csv


def test_siguiente_instante_cruza_la_noche():
    assert siguiente_instante(datetime(2026, 1, 1, 21, 58)) == datetime(2026, 1, 2, 5, 3)
    assert siguiente_instante(datetime(2026, 1, 1, 10, 3)) == datetime(2026, 1, 1, 10, 8)


def test_ventana_y_regimen_detectados(tmp_path):
    d = cargar(_csv(tmp_path))
    v = M.ventana_diurna(d)
    assert v is not None and v[0] <= 60 and v[1] >= 160
    assert M.regimen_vigente(d, v)[0]


def test_sin_regimen_en_datos_uniformes(tmp_path):
    d = cargar(_csv(tmp_path, diurno_sin_repetir=False))
    v = M.ventana_diurna(d)
    assert not M.regimen_vigente(d, v)[0]


def test_caracteristicas_no_miran_el_futuro(tmp_path):
    d = cargar(_csv(tmp_path, dias=4))
    idx = np.array([500])
    antes = M.caracteristicas(d, idx).copy()
    d.num[500:] = (d.num[500:] + 7) % 19          # alterar presente y futuro
    assert np.array_equal(antes, M.caracteristicas(d, idx))


def test_modelo_aprende_la_no_repeticion(tmp_path):
    d = cargar(_csv(tmp_path))
    beta = M.entrenar(d, M.ventana_diurna(d))
    assert beta is not None and beta[0] < -3       # antig=1 casi imposible


def test_libro_predice_una_vez_y_evalua(tmp_path):
    path = _csv(tmp_path)
    d = cargar(path)
    libro = R.AlmacenArchivos(tmp_path)
    antes = datetime(2026, 1, 1)
    f = R.predecir(d, libro, ahora=antes)
    assert f is not None and f["sorteo"] == str(int(d.sorteo[-1]) + 1) and f["tardia"] == "0"
    assert f["pick_regla"] == str(int(d.num[-2]))
    assert R.predecir(d, libro, ahora=antes) is None      # no se duplica
    assert R.evaluar(d, libro) == 0                              # aún no ha salido
    assert "AÚN NO CONCLUYENTE" in R.informe(libro)


def test_prediccion_tardia_no_cuenta(tmp_path):
    d = cargar(_csv(tmp_path))
    f = R.predecir(d, R.AlmacenArchivos(tmp_path), ahora=datetime(2030, 1, 1))
    assert f["tardia"] == "1" and f["apostar"] == "0"


def test_almacen_remoto_restaura_el_libro_perdido(tmp_path):
    (tmp_path / R.LIBRO).write_text("creado_utc,sorteo\nx,1\n", encoding="utf-8")
    nube: dict[str, str] = {}
    a = R.AlmacenRemoto(nube.get, nube.__setitem__, semilla=tmp_path)
    assert a.leer(R.LIBRO).startswith("creado_utc") and R.LIBRO in nube and a.restaurados == [R.LIBRO]
    assert a.leer(R.CACHE) is None                      # las cachés no se siembran


def test_almacen_remoto_no_confunde_error_con_vacio(tmp_path):
    def falla(_nombre):
        raise RuntimeError("timeout")
    a = R.AlmacenRemoto(falla, lambda *_: None, semilla=tmp_path)
    import pytest
    with pytest.raises(RuntimeError):
        a.leer(R.LIBRO)


def test_panel_web_devuelve_bloque_y_no_lanza(tmp_path):
    import csv as _csv

    from papel import web
    with _csv_path(tmp_path).open(encoding="utf-8-sig") as fh:
        rows = list(_csv.DictReader(fh))
    nube: dict[str, str] = {}
    a = R.AlmacenRemoto(nube.get, nube.__setitem__, semilla=None)
    b = web.panel(rows, a)
    assert "error" not in b and b["ventana"] and b["proxima"]["sorteo"] and R.LIBRO in nube
    assert web.panel([], a) == {"error": "Histórico insuficiente para el modo papel."}


def test_dashboard_restaura_csv_y_adjunta_papel(tmp_path, monkeypatch):
    import csv as _csv

    from api import dashboard as D
    with _csv_path(tmp_path).open(encoding="utf-8-sig") as fh:
        rows = list(_csv.DictReader(fh))
    nube: dict[str, bytes] = {}

    def blob(method, pathname="", body=None, content_type=""):
        if method == "PUT":
            nube[pathname] = body
            return b""
        if pathname not in nube:
            raise RuntimeError('HTTP 400: {"statusCode":"404","error":"not_found","message":"Object not found"}')
        return nube[pathname]

    monkeypatch.setattr(D, "blob_request", blob)
    monkeypatch.setattr(D, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(D, "SUPABASE_KEY", "k")
    monkeypatch.setattr(D, "LOCAL_CSV", _csv_path(tmp_path))
    assert D.read_rows() and D._csv_remoto_ausente
    assert "restaurado" in D.restaurar_csv_perdido(rows) and "Resultados_RuletaExpress.csv" in nube
    assert D.restaurar_csv_perdido(rows) is None                 # ya no falta
    almacen, persistente = D.almacen_papel()
    bloque = D.papel_web.panel(rows, almacen, persistente)
    assert persistente and "error" not in bloque and "registro_papel.csv" in nube
    assert not D.objeto_ausente("HTTP 404: Bucket not found") and not D.objeto_ausente("timed out")


def test_cron_exige_secreto_y_resume(monkeypatch):
    from api import cron
    assert not cron.autorizado(None, "s") and not cron.autorizado("Bearer s", "") and not cron.autorizado("Bearer x", "s")
    assert cron.autorizado("Bearer s", "s")
    monkeypatch.setattr(cron.panel, "dashboard", lambda **_: {
        "results": [{"No_Sorteo": "No. 9"}], "sync_error": None,
        "papel": {"proxima": {"sorteo": "10", "pick_regla": "4", "apostar": "1", "tardia": "0", "hora_prevista": "x"}, "persistente": True}})
    r = cron.ejecutar()
    assert r["ok"] and r["ultimo_sorteo"] == "No. 9" and r["prediccion"]["sorteo"] == "10" and "results" not in r
