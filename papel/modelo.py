"""Ventana diurna, interruptor de régimen y modelo logit condicional."""
from __future__ import annotations

import numpy as np

from . import K
from .datos import TURNOS_DIA, Datos

DIAS_VENTANA = 30        # jornadas usadas para aprender la ventana diurna
MARGEN_TURNOS = 2        # se recorta la ventana por ambos lados
MIN_TURNOS = 60          # una ventana más corta no se considera régimen
DIAS_VIGILANCIA = 7      # jornadas recientes que vigila el interruptor
MAX_TASA_REPETICION = 0.01   # si se supera en la ventana, el régimen se da por roto
MIN_ENTRENO = 3000       # sorteos mínimos en ventana para ajustar el modelo

NOMBRES = [f"antig={k}" for k in range(1, 9)] + ["antig=15-30", "antig>30", "misma_unidad", "adyacente"] + [
    f"num={v:02d}" for v in range(1, K)
]
NF = len(NOMBRES)
#: antigüedad 1..30 -> cubeta 0..9 ; >30 -> 10. La cubeta 8 (9-14) es la referencia.
_CUBETA = np.r_[0, np.arange(0, 8), np.full(6, 8), np.full(16, 9), 10]


def repeticiones(d: Datos) -> np.ndarray:
    return np.r_[False, d.num[1:] == d.num[:-1]] & d.consec


def ventana_diurna(d: Datos, hasta_dia: int | None = None) -> tuple[int, int] | None:
    """Racha contigua más larga de turnos SIN repeticiones en las últimas jornadas.

    Sólo usa jornadas < hasta_dia (por defecto, todas). Devuelve (turno_ini,
    turno_fin) inclusive, o None si no hay régimen reconocible.
    """
    fin = int(d.dia.max()) + 1 if hasta_dia is None else hasta_dia
    m = (d.dia < fin) & (d.dia >= fin - DIAS_VENTANA)
    if m.sum() < DIAS_VENTANA * TURNOS_DIA * 0.5:
        return None
    rep = np.bincount(d.turno[m & repeticiones(d)], minlength=TURNOS_DIA)[:TURNOS_DIA]
    mejor, ini = (0, 0, 0), None
    for i in range(TURNOS_DIA + 1):
        if i < TURNOS_DIA and rep[i] == 0:
            ini = i if ini is None else ini
        else:
            if ini is not None and i - ini > mejor[0]:
                mejor = (i - ini, ini, i - 1)
            ini = None
    a, b = mejor[1] + MARGEN_TURNOS, mejor[2] - MARGEN_TURNOS
    return (a, b) if b - a + 1 >= MIN_TURNOS else None


def regimen_vigente(d: Datos, ventana: tuple[int, int] | None) -> tuple[bool, str]:
    """Interruptor: ¿sigue sin haber repeticiones dentro de la ventana?"""
    if ventana is None:
        return False, "sin ventana diurna reconocible"
    ult = int(d.dia.max())
    m = (d.dia > ult - DIAS_VIGILANCIA) & (d.turno >= ventana[0]) & (d.turno <= ventana[1]) & d.consec
    n = int(m.sum())
    if n < 200:
        return False, f"sólo {n} sorteos recientes en ventana"
    r = int((repeticiones(d) & m).sum())
    ok = r / n <= MAX_TASA_REPETICION
    return ok, f"{r} repeticiones en {n} sorteos de los últimos {DIAS_VIGILANCIA} días ({100 * r / n:.2f}%)"


def _fila(num_prev: int, antig: np.ndarray) -> np.ndarray:
    """Matriz K x NF de características de cada candidato para un sorteo."""
    f = np.zeros((K, NF), np.float32)
    cub = _CUBETA[np.minimum(antig, 31)]
    for v in range(K):
        if cub[v] != 8:
            f[v, cub[v] - (cub[v] > 8)] = 1
        f[v, 10] = abs(v - num_prev) == 10
        f[v, 11] = abs(v - num_prev) == 1
        if v > 0:
            f[v, 11 + v] = 1
    return f


def caracteristicas(d: Datos, indices: np.ndarray) -> np.ndarray:
    """Tensor (len(indices), K, NF) usando sólo sorteos anteriores a cada índice."""
    quiero = {int(t): i for i, t in enumerate(indices)}
    F = np.zeros((len(indices), K, NF), np.float32)
    ultimo = np.full(K, -10**6)
    for t in range(len(d)):
        i = quiero.get(t)
        if i is not None:
            F[i] = _fila(int(d.num[t - 1]), t - ultimo)
        ultimo[d.num[t]] = t
    return F


def caracteristicas_siguiente(d: Datos) -> np.ndarray:
    """Características para el sorteo que aún no ha salido (K x NF)."""
    n = len(d)
    ultimo = np.full(K, -10**6)
    for t in range(max(0, n - 400), n):
        ultimo[d.num[t]] = t
    return _fila(int(d.num[-1]), n - ultimo)


def ajustar(F: np.ndarray, y: np.ndarray, ridge: float = 1e-2, iters: int = 25) -> np.ndarray:
    """Logit condicional por Newton-Raphson: P(v) ∝ exp(beta · f(v))."""
    F = F.astype(np.float64)
    b = np.zeros(NF)
    for _ in range(iters):
        p = probabilidades(F, b)
        g = F[np.arange(len(y)), y].sum(0) - np.einsum("nk,nkf->f", p, F) - ridge * b
        Fw = F * p[:, :, None]
        mu = Fw.sum(1)
        Hs = np.einsum("nkf,nkg->fg", Fw, F) - mu.T @ mu + ridge * np.eye(NF)
        paso = np.linalg.solve(Hs, g)
        b += paso
        if np.abs(paso).max() < 1e-7:
            break
    return b


def probabilidades(F: np.ndarray, beta: np.ndarray) -> np.ndarray:
    u = F.astype(np.float64) @ beta
    u -= u.max(-1, keepdims=True)
    p = np.exp(u)
    return p / p.sum(-1, keepdims=True)


def indices_entreno(d: Datos, ventana: tuple[int, int], hasta_dia: int | None = None) -> np.ndarray:
    hist = np.zeros(len(d), bool)
    hist[3:] = d.sorteo[3:] - d.sorteo[:-3] == 3
    m = hist & (d.turno >= ventana[0]) & (d.turno <= ventana[1]) & (np.arange(len(d)) > 300)
    if hasta_dia is not None:
        m &= d.dia < hasta_dia
    return np.flatnonzero(m)


def entrenar(d: Datos, ventana: tuple[int, int], hasta_dia: int | None = None) -> np.ndarray | None:
    idx = indices_entreno(d, ventana, hasta_dia)
    if len(idx) < MIN_ENTRENO:
        return None
    return ajustar(caracteristicas(d, idx), d.num[idx])
