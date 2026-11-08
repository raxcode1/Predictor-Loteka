"""Modelos predictivos secuenciales sin look-ahead.

Todos los modelos son posteriores predictivos Dirichlet-Multinomial:

    P(X_t = j | contexto c, datos < t) = (N_{c,j} + alpha) / (N_c + 19 alpha)

- `uniform`    : P = 1/19 (hipótesis nula, sin parámetros).
- `dirichlet`  : orden 0, frecuencias marginales con prior Dirichlet(alpha).
- `markov`     : orden k = 1..3, una Dirichlet(alpha) por contexto.

`window` limita los conteos a las últimas `window` actualizaciones (modelo de
olvido para no estacionariedad). Un contexto sólo es válido cuando los k
sorteos previos son consecutivos en la numeración; si no, se usa el prior
predictivo (uniforme) y la observación no actualiza los conteos de contexto.

La matriz prequencial `P[t]` se calcula **antes** de incorporar `x[t]`, de
modo que cualquier métrica calculada sobre ella está libre de look-ahead por
construcción.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import OUTPUT_MODULUS
from .data import Dataset

K = OUTPUT_MODULUS


@dataclass(frozen=True)
class ModelSpec:
    family: str  # "uniform" | "dirichlet" | "markov"
    order: int = 0
    alpha: float = 1.0
    window: int | None = None

    def __post_init__(self) -> None:
        if self.family not in ("uniform", "dirichlet", "markov"):
            raise ValueError(f"Familia de modelo desconocida: {self.family}")
        if self.family == "markov" and not 1 <= self.order <= 3:
            raise ValueError("Markov admite órdenes 1..3.")
        if self.family != "markov" and self.order != 0:
            raise ValueError("Sólo Markov tiene orden > 0.")
        if self.alpha <= 0:
            raise ValueError("alpha debe ser > 0.")
        if self.window is not None and self.window <= 0:
            raise ValueError("window debe ser > 0.")

    @property
    def name(self) -> str:
        if self.family == "uniform":
            return "uniform"
        window = "all" if self.window is None else str(self.window)
        if self.family == "dirichlet":
            return f"dirichlet(a={self.alpha:g},w={window})"
        return f"markov{self.order}(a={self.alpha:g},w={window})"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "family": self.family,
            "order": self.order,
            "alpha": self.alpha,
            "window": self.window,
        }


UNIFORM = ModelSpec("uniform")


def _context_valid(consecutive: np.ndarray, order: int) -> np.ndarray:
    """valid[t] = los sorteos t-order..t son consecutivos (contexto observable)."""
    n = consecutive.size
    valid = np.zeros(n, dtype=bool)
    if order == 0:
        valid[:] = True
        return valid
    if n <= order:
        return valid
    # run[t] = longitud de la racha de consecutivos que termina en t.
    run = np.zeros(n, dtype=np.int64)
    for t in range(1, n):
        run[t] = run[t - 1] + 1 if consecutive[t] else 0
    valid = run >= order
    return valid


def prequential_counts(
    values: np.ndarray,
    consecutive: np.ndarray,
    order: int,
    window: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Conteos del contexto de cada t usando sólo datos < t.

    Devuelve `(counts, valid)` con `counts` de forma (n, 19). Si `valid[t]` es
    False, la fila es cero (el modelo no puede condicionar en ese contexto).
    """
    n = int(values.size)
    out = np.zeros((n, K), dtype=np.int32)
    valid = _context_valid(consecutive, order)
    table = np.zeros((K**order, K), dtype=np.int32)
    history: deque[tuple[int, int]] = deque()
    powers = [K ** (order - 1 - i) for i in range(order)]
    vals = values.tolist()
    for t in range(n):
        if not valid[t]:
            continue
        if order:
            ctx = 0
            for i, power in enumerate(powers):
                ctx += vals[t - order + i] * power
        else:
            ctx = 0
        out[t] = table[ctx]
        x = vals[t]
        table[ctx, x] += 1
        if window is not None:
            history.append((ctx, x))
            if len(history) > window:
                old_ctx, old_x = history.popleft()
                table[old_ctx, old_x] -= 1
    return out, valid


class PredictionCache:
    """Calcula y reutiliza conteos prequenciales por (orden, ventana)."""

    def __init__(self, dataset: Dataset) -> None:
        self.dataset = dataset
        self._consecutive = dataset.consecutive
        self._counts: dict[tuple[int, int | None], tuple[np.ndarray, np.ndarray]] = {}

    def counts(self, order: int, window: int | None) -> tuple[np.ndarray, np.ndarray]:
        key = (order, window)
        if key not in self._counts:
            self._counts[key] = prequential_counts(
                self.dataset.values, self._consecutive, order, window
            )
        return self._counts[key]

    def matrix(self, spec: ModelSpec) -> np.ndarray:
        """Matriz (n, 19) de probabilidades prequenciales del modelo."""
        n = len(self.dataset)
        if spec.family == "uniform":
            return np.full((n, K), 1.0 / K)
        counts, valid = self.counts(spec.order, spec.window)
        probabilities = (counts + spec.alpha) / (
            counts.sum(axis=1, keepdims=True) + K * spec.alpha
        )
        probabilities[~valid] = 1.0 / K
        return probabilities


def forecast_next(dataset: Dataset, spec: ModelSpec) -> np.ndarray:
    """Distribución predictiva para el sorteo siguiente al último observado."""
    if spec.family == "uniform" or len(dataset) == 0:
        return np.full(K, 1.0 / K)
    values = dataset.values
    order = spec.order
    consecutive = dataset.consecutive
    # Se reproduce la actualización completa y se consulta el contexto final.
    table = np.zeros((K**order, K), dtype=np.int64)
    valid = _context_valid(consecutive, order)
    history: deque[tuple[int, int]] = deque()
    powers = [K ** (order - 1 - i) for i in range(order)]
    vals = values.tolist()
    for t in range(len(vals)):
        if not valid[t]:
            continue
        ctx = sum(vals[t - order + i] * p for i, p in enumerate(powers)) if order else 0
        table[ctx, vals[t]] += 1
        if spec.window is not None:
            history.append((ctx, vals[t]))
            if len(history) > spec.window:
                old_ctx, old_x = history.popleft()
                table[old_ctx, old_x] -= 1
    n = len(vals)
    if order:
        # El contexto del sorteo n (aún no observado) son los últimos `order`
        # valores, que deben ser consecutivos entre sí.
        if n < order:
            return np.full(K, 1.0 / K)
        tail_ok = bool(np.all(consecutive[n - order + 1 : n])) if order > 1 else True
        if not tail_ok:
            return np.full(K, 1.0 / K)
        ctx = sum(vals[n - order + i] * p for i, p in enumerate(powers))
    else:
        ctx = 0
    row = table[ctx]
    return (row + spec.alpha) / (row.sum() + K * spec.alpha)


def default_grid() -> list[ModelSpec]:
    """Rejilla de candidatos preregistrada (no se amplía tras ver el test)."""
    grid: list[ModelSpec] = [UNIFORM]
    for window in (None, 2000):
        for alpha in (1.0, 10.0, 100.0):
            grid.append(ModelSpec("dirichlet", 0, alpha, window))
    for order in (1, 2, 3):
        for window in (None, 5000):
            for alpha in (1.0, 10.0, 100.0):
                grid.append(ModelSpec("markov", order, alpha, window))
    return grid
