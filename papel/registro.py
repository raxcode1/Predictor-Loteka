"""Libro de predicciones en papel (CSV que sólo crece)."""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from scipy import stats

from . import PAGO, K
from . import modelo as M
from .datos import Datos, hora_de_turno, siguiente_instante, turno_de

RAIZ = Path(__file__).resolve().parents[1]
LIBRO = "registro_papel.csv"
CACHE = "papel_modelo.json"
ULTIMA = "papel_ultima_prob.json"


class AlmacenArchivos:
    """Guarda el libro y las cachés como archivos de una carpeta (uso local)."""

    def __init__(self, carpeta: Path | None = None) -> None:
        # PAPEL_DIR permite apartar el libro (p. ej. en los tests o en un disco persistente).
        self.carpeta = Path(carpeta or os.environ.get("PAPEL_DIR") or RAIZ)

    def leer(self, nombre: str) -> str | None:
        ruta = self.carpeta / nombre
        return ruta.read_text(encoding="utf-8") if ruta.exists() else None

    def guardar(self, nombre: str, texto: str) -> None:
        tmp = self.carpeta / (nombre + ".tmp")
        tmp.write_text(texto, encoding="utf-8", newline="")
        tmp.replace(self.carpeta / nombre)


class AlmacenRemoto:
    """Adapta dos funciones (leer/guardar por nombre) de un almacenamiento remoto.

    `leer` debe devolver None SÓLO cuando el objeto no existe y lanzar una
    excepción ante cualquier otro fallo: así un corte de red nunca se confunde
    con un libro vacío. Si el objeto no existe y hay una copia versionada en
    el repositorio (`semilla`), se restaura desde ella y se vuelve a subir.
    """

    def __init__(self, leer, guardar, semilla: Path | None = RAIZ) -> None:
        self._leer, self._guardar, self.semilla = leer, guardar, semilla
        self.restaurados: list[str] = []

    def leer(self, nombre: str) -> str | None:
        texto = self._leer(nombre)
        if texto is None and self.semilla is not None and nombre == LIBRO:
            copia = Path(self.semilla) / nombre
            if copia.exists():
                texto = copia.read_text(encoding="utf-8")
                self._guardar(nombre, texto)
                self.restaurados.append(nombre)
        return texto

    def guardar(self, nombre: str, texto: str) -> None:
        self._guardar(nombre, texto)
ZONA = ZoneInfo("America/Santo_Domingo")
CAMPOS = ["creado_utc", "sorteo", "hora_prevista", "en_ventana", "regimen_ok", "apostar", "tardia",
          "pick_regla", "pick_modelo", "p_modelo", "p_regla", "resultado", "acierto_regla", "acierto_modelo", "p_resultado"]


def leer(almacen=None) -> list[dict[str, str]]:
    texto = (almacen or AlmacenArchivos()).leer(LIBRO)
    return list(csv.DictReader(io.StringIO(texto))) if texto else []


def escribir(filas: list[dict[str, str]], almacen=None) -> None:
    salida = io.StringIO()
    w = csv.DictWriter(salida, fieldnames=CAMPOS, lineterminator="\n")
    w.writeheader()
    w.writerows(filas)
    (almacen or AlmacenArchivos()).guardar(LIBRO, salida.getvalue())


def _modelo_en_cache(d: Datos, ventana, almacen) -> np.ndarray | None:
    """Reajusta una vez por jornada; el resto del día reutiliza los coeficientes."""
    clave = f"{d.ts[-1].date().isoformat()}|{ventana[0]}-{ventana[1]}"
    try:
        guardado = json.loads(almacen.leer(CACHE) or "{}")
        if guardado.get("clave") == clave:
            return np.array(guardado["beta"])
    except Exception:
        pass
    beta = M.entrenar(d, ventana, hasta_dia=int(d.dia.max()))   # sólo jornadas cerradas
    if beta is not None:
        try:
            almacen.guardar(CACHE, json.dumps({"clave": clave, "beta": beta.tolist()}))
        except Exception:
            pass
    return beta


def predecir(d: Datos, almacen=None, ahora: datetime | None = None) -> dict[str, str] | None:
    """Registra la predicción del sorteo siguiente al último del CSV.

    Devuelve la fila nueva, o None si ya estaba registrada o falta historia.
    """
    if len(d) < 4:
        return None
    almacen = almacen or AlmacenArchivos()
    objetivo = int(d.sorteo[-1]) + 1
    filas = leer(almacen)
    if any(f["sorteo"] == str(objetivo) for f in filas):
        return None
    prevista = siguiente_instante(d.ts[-1])
    ahora = ahora or datetime.now(ZONA).replace(tzinfo=None)
    historia_ok = bool(d.sorteo[-1] - d.sorteo[-3] == 2)
    ventana = M.ventana_diurna(d)
    ok, _ = M.regimen_vigente(d, ventana)
    turno = turno_de(prevista)
    en_ventana = ventana is not None and ventana[0] <= turno <= ventana[1]
    tardia = ahora > prevista          # el sorteo pudo haber salido ya: no cuenta
    pick_regla = int(d.num[-2])
    pick_modelo, p_modelo, p_regla = "", "", ""
    if ventana is not None and historia_ok:
        beta = _modelo_en_cache(d, ventana, almacen)
        if beta is not None:
            p = M.probabilidades(M.caracteristicas_siguiente(d), beta)
            pick_modelo, p_modelo, p_regla = str(int(p.argmax())), f"{p.max():.5f}", f"{p[pick_regla]:.5f}"
            try:
                almacen.guardar(ULTIMA, json.dumps({"sorteo": objetivo, "p": p.round(5).tolist()}))
            except Exception:
                pass
    fila = {
        "creado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sorteo": str(objetivo), "hora_prevista": prevista.strftime("%d/%m/%Y %H:%M"),
        "en_ventana": str(int(en_ventana)), "regimen_ok": str(int(ok)),
        "apostar": str(int(en_ventana and ok and historia_ok and not tardia)), "tardia": str(int(tardia)),
        "pick_regla": str(pick_regla) if historia_ok else "", "pick_modelo": pick_modelo,
        "p_modelo": p_modelo, "p_regla": p_regla,
        "resultado": "", "acierto_regla": "", "acierto_modelo": "", "p_resultado": "",
    }
    filas.append(fila)
    escribir(filas, almacen)
    return fila


def evaluar(d: Datos, almacen=None) -> int:
    """Rellena el resultado de las predicciones cuyo sorteo ya está en el CSV."""
    almacen = almacen or AlmacenArchivos()
    filas = leer(almacen)
    pos = {int(s): i for i, s in enumerate(d.sorteo)}
    try:
        ult = json.loads(almacen.leer(ULTIMA) or "{}")
    except Exception:
        ult = {}
    n = 0
    for f in filas:
        if f["resultado"] != "" or int(f["sorteo"]) not in pos:
            continue
        r = int(d.num[pos[int(f["sorteo"])]])
        f["resultado"] = str(r)
        f["acierto_regla"] = "" if f["pick_regla"] == "" else str(int(int(f["pick_regla"]) == r))
        f["acierto_modelo"] = "" if f["pick_modelo"] == "" else str(int(int(f["pick_modelo"]) == r))
        if ult.get("sorteo") == int(f["sorteo"]):
            f["p_resultado"] = f"{ult['p'][r]:.5f}"
        n += 1
    if n:
        escribir(filas, almacen)
    return n


def _medida(aciertos: int, n: int) -> dict:
    if n == 0:
        return {"n": 0, "aciertos": 0}
    t = aciertos / n
    ic = stats.binomtest(aciertos, n).proportion_ci()
    return {"n": n, "aciertos": aciertos, "tasa": t, "ic_bajo": ic.low, "ic_alto": ic.high, "roi": PAGO * t - 1,
            "p_azar": stats.binomtest(aciertos, n, 1 / K, alternative="greater").pvalue,
            "p_equilibrio": stats.binomtest(aciertos, n, 1 / PAGO, alternative="greater").pvalue}


def _linea(nombre: str, aciertos: int, n: int) -> str:
    m = _medida(aciertos, n)
    if n == 0:
        return f"{nombre:<34} sin datos"
    return (f"{nombre:<34} n={n:6d} aciertos={aciertos:5d} tasa={m['tasa']:.4f} IC95%=[{m['ic_bajo']:.4f},{m['ic_alto']:.4f}] "
            f"ROI@{PAGO:.0f}x={100 * m['roi']:+6.1f}% p(>1/19)={m['p_azar']:.4f} p(>1/{PAGO:.0f})={m['p_equilibrio']:.4f}")


MIN_APUESTAS = 2000   # ~2 semanas de ventana diurna; antes, el IC es demasiado ancho


def resumen(filas: list[dict[str, str]]) -> dict:
    """Métricas del libro: sólo predicciones evaluadas y registradas a tiempo."""
    ev = [f for f in filas if f["resultado"] != "" and f["tardia"] == "0"]
    ap = [f for f in ev if f["apostar"] == "1"]
    am = [f for f in ap if f["acierto_modelo"] != ""]
    fuera = [f for f in ev if f["en_ventana"] == "0" and f["pick_regla"] != ""]
    pr = np.array([float(f["p_resultado"]) for f in ap if f["p_resultado"] != ""])
    regla = _medida(sum(f["acierto_regla"] == "1" for f in ap), len(ap))
    if len(ap) < MIN_APUESTAS:
        veredicto = f"AÚN NO CONCLUYENTE: faltan {MIN_APUESTAS - len(ap)} predicciones apostables para el mínimo de {MIN_APUESTAS}."
    elif regla["p_equilibrio"] < 0.01:
        veredicto = "VEREDICTO: la regla supera el equilibrio (p<0.01)."
    else:
        veredicto = "VEREDICTO: la regla NO supera el equilibrio; no hay ventaja demostrada."
    return {"evaluadas": len(ev), "apostables": len(ap), "minimo": MIN_APUESTAS, "pago": PAGO,
            "azar": 1 / K, "equilibrio": 1 / PAGO, "regla": regla,
            "modelo": _medida(sum(f["acierto_modelo"] == "1" for f in am), len(am)),
            "control": _medida(sum(f["acierto_regla"] == "1" for f in fuera), len(fuera)),
            "log_perdida": float(-np.log(pr).mean()) if len(pr) else None, "log_perdida_uniforme": float(np.log(K)),
            "n_log_perdida": int(len(pr)), "veredicto": veredicto}


def informe(almacen=None) -> str:
    r = resumen(leer(almacen))
    out = [f"Predicciones evaluadas: {r['evaluadas']} (apostables: {r['apostables']}; control fuera de ventana: {r['control']['n']})",
           f"Referencias: azar = {1 / K:.4f} | equilibrio a {PAGO:.0f}x = {1 / PAGO:.4f}",
           _linea("Regla X_t-2 (apostables)", r["regla"]["aciertos"], r["regla"]["n"]),
           _linea("Modelo top-1 (apostables)", r["modelo"]["aciertos"], r["modelo"]["n"]),
           _linea("Control: X_t-2 fuera de ventana", r["control"]["aciertos"], r["control"]["n"])]
    if r["log_perdida"] is not None:
        out.append(f"Log-pérdida del modelo: {r['log_perdida']:.4f} vs uniforme {np.log(K):.4f} (n={r['n_log_perdida']}; menor es mejor)")
    out.append(r["veredicto"])
    return "\n".join(out)


def estado(d: Datos) -> str:
    v = M.ventana_diurna(d)
    ok, detalle = M.regimen_vigente(d, v)
    vt = "ninguna" if v is None else f"{hora_de_turno(v[0])}-{hora_de_turno(v[1])}"
    return f"Ventana diurna: {vt} | régimen {'VIGENTE' if ok else 'NO VIGENTE -> no apostar'} ({detalle})"
