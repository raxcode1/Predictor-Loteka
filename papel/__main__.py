from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from . import modelo as M
from . import registro as R
from .datos import CSV_PATH, cargar


def backtest(d, burn: int = 30, reajuste: int = 7) -> str:
    """Walk-forward: ventana y modelo se aprenden sólo con jornadas anteriores."""
    nd = int(d.dia.max()) + 1
    hist = np.zeros(len(d), bool)
    hist[3:] = d.sorteo[3:] - d.sorteo[:-3] == 3
    ar = am = n = 0
    for d0 in range(burn, nd, reajuste):
        v = M.ventana_diurna(d, hasta_dia=d0)
        if v is None:
            continue
        idx = np.flatnonzero(hist & (d.dia >= d0) & (d.dia < d0 + reajuste) & (d.turno >= v[0]) & (d.turno <= v[1]))
        if not len(idx):
            continue
        n += len(idx)
        ar += int((d.num[idx] == d.num[idx - 2]).sum())
        beta = M.entrenar(d, v, hasta_dia=d0)
        if beta is not None:
            p = M.probabilidades(M.caracteristicas(d, idx), beta)
            am += int((p.argmax(1) == d.num[idx]).sum())
    return "\n".join([f"Walk-forward desde la jornada {burn + 1} ({nd} jornadas)",
                      R._linea("Regla X_t-2", ar, n), R._linea("Modelo top-1", am, n)])


def main() -> None:
    ap = argparse.ArgumentParser(prog="papel", description="Modo papel de Ruleta Express (no apuesta).")
    ap.add_argument("orden", choices=["predecir", "evaluar", "informe", "estado", "ciclo", "backtest"])
    ap.add_argument("--csv", type=Path, default=CSV_PATH)
    ap.add_argument("--cada", type=int, default=45, help="segundos entre pasadas en 'ciclo'")
    a = ap.parse_args()
    if a.orden == "informe":
        print(R.informe())
        return
    d = cargar(a.csv)
    if a.orden == "estado":
        print(R.estado(d))
    elif a.orden == "backtest":
        print(backtest(d))
    elif a.orden == "evaluar":
        print(f"Resultados rellenados: {R.evaluar(d)}")
    elif a.orden == "predecir":
        R.evaluar(d)
        f = R.predecir(d)
        print(R.estado(d))
        print("Sin predicción nueva (ya registrada o falta historia)." if f is None else
              f"Sorteo {f['sorteo']} ({f['hora_prevista']}): regla={f['pick_regla']} modelo={f['pick_modelo']} "
              f"p={f['p_modelo']} apostar={'SÍ (papel)' if f['apostar'] == '1' else 'NO'}{' [TARDÍA]' if f['tardia'] == '1' else ''}")
    else:
        visto = None
        while True:
            try:
                d = cargar(a.csv)
                if len(d) and int(d.sorteo[-1]) != visto:
                    visto = int(d.sorteo[-1])
                    R.evaluar(d)
                    f = R.predecir(d)
                    if f:
                        print(f"[{f['creado_utc']}] sorteo {f['sorteo']}: regla={f['pick_regla']} modelo={f['pick_modelo']} apostar={f['apostar']}", flush=True)
            except Exception as error:  # el bucle no debe morir por un CSV a medio escribir
                print(f"Aviso: {error}", flush=True)
            time.sleep(max(15, a.cada))


if __name__ == "__main__":
    main()
