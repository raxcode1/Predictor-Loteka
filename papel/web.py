"""Bloque `papel` de la respuesta de /api/dashboard.

Se llama en cada petición del panel: evalúa lo pendiente, registra la
predicción del próximo sorteo (una sola vez) y devuelve el resumen. Nunca
lanza: cualquier fallo vuelve como {"error": ...} para no tumbar el panel.
"""
from __future__ import annotations

from . import modelo as M
from . import registro as R
from .datos import desde_filas, hora_de_turno

ULTIMAS = 40


def panel(rows, almacen, persistente: bool = True) -> dict:
    try:
        d = desde_filas(rows)
        if len(d) < 4:
            return {"error": "Histórico insuficiente para el modo papel."}
        R.evaluar(d, almacen)
        R.predecir(d, almacen)
        filas = R.leer(almacen)
        ventana = M.ventana_diurna(d)
        ok, detalle = M.regimen_vigente(d, ventana)
        objetivo = str(int(d.sorteo[-1]) + 1)
        proxima = next((f for f in reversed(filas) if f["sorteo"] == objetivo), None)
        evaluadas = [f for f in filas if f["resultado"] != ""]
        return {
            "ventana": None if ventana is None else [hora_de_turno(ventana[0]), hora_de_turno(ventana[1])],
            "regimen_ok": ok, "regimen_detalle": detalle,
            "proxima": proxima, "resumen": R.resumen(filas),
            "ultimas": list(reversed(evaluadas[-ULTIMAS:])),
            "persistente": persistente,
            "restaurados": list(getattr(almacen, "restaurados", [])),
        }
    except Exception as error:  # el panel principal no debe caer por el modo papel
        return {"error": str(error)}
