"""Utilidades estadísticas sin dependencias externas.

Se evita SciPy a propósito: el proyecto sólo declara `pandas` y `z3-solver`
en `requirements.txt`. Todas las funciones son exactas o numéricamente
estables para el rango de muestras usado en el análisis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Callable, Sequence

import numpy as np


def normal_quantile(probability: float) -> float:
    """Cuantil normal estándar sin dependencias externas."""
    return NormalDist().inv_cdf(probability)


@dataclass(frozen=True)
class Interval:
    low: float
    high: float

    @property
    def width(self) -> float:
        return self.high - self.low

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high

    def as_tuple(self) -> tuple[float, float]:
        return (self.low, self.high)


def wilson_interval(successes: int, trials: int, confidence: float = 0.95) -> Interval:
    """Intervalo de confianza de Wilson para una proporción.

    Es preferible al intervalo de Wald porque no se degrada con muestras
    pequeñas ni con proporciones cercanas a 0 o 1, que es exactamente el
    régimen en el que opera el motor de alertas.
    """
    if trials <= 0:
        return Interval(0.0, 1.0)
    if not 0 <= successes <= trials:
        raise ValueError("successes debe estar entre 0 y trials.")
    z = normal_quantile(0.5 + confidence / 2.0)
    proportion = successes / trials
    denominator = 1.0 + z**2 / trials
    center = (proportion + z**2 / (2.0 * trials)) / denominator
    margin = z * math.sqrt(
        proportion * (1.0 - proportion) / trials + z**2 / (4.0 * trials**2)
    ) / denominator
    return Interval(max(0.0, center - margin), min(1.0, center + margin))


def jeffreys_interval(successes: int, trials: int, confidence: float = 0.95) -> Interval:
    """Intervalo de Jeffreys (Bayesiano uniforme) para una proporción."""
    if trials <= 0:
        return Interval(0.0, 1.0)
    z = normal_quantile(0.5 + confidence / 2.0)
    low = _beta_quantile_approx(successes + 0.5, trials - successes + 0.5, z)
    high = _beta_quantile_approx(successes + 1.5, trials - successes + 0.5, z)
    return Interval(low, high)


def _beta_quantile_approx(alpha: float, beta: float, z: float) -> float:
    """Aproximación de Student-t al cuantil Beta(alpha, beta) por Cornish-Fisher."""
    mean = alpha / (alpha + beta)
    variance = alpha * beta / ((alpha + beta) ** 2 * (alpha + beta + 1.0))
    if variance <= 0.0:
        return mean
    skew = 2.0 * (beta - alpha) * math.sqrt(alpha + beta + 1.0) / ((alpha + beta + 2.0) * math.sqrt(alpha * beta))
    kurtosis = (
        6.0
        * (alpha**3 + beta**3 + alpha * beta * (alpha + beta - 2.0))
        / (alpha * beta * (alpha + beta + 2.0) * (alpha + beta + 3.0))
    )
    correction = (z**2 - 1.0) * skew / 6.0 + (z**3 - 3.0 * z) * kurtosis / 24.0 - z * skew**2 / 36.0
    return min(1.0, max(0.0, mean + math.sqrt(variance) * (z + correction)))


def binomial_survival(successes: int, trials: int, probability: float) -> float:
    """Probabilidad de observar `successes` o más aciertos bajo la hipótesis nula.

    Es la cola superior de la binomial exacta: `P(X >= successes)` con
    `probability` constante. Se usa para contrastar la tasa de acierto contra
    la línea base equiprobable.
    """
    if trials <= 0:
        return 1.0
    successes = max(0, min(successes, trials))
    total = 0.0
    for k in range(successes, trials + 1):
        total += math.comb(trials, k) * probability**k * (1.0 - probability) ** (trials - k)
    return min(1.0, max(0.0, total))


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Test exacto de Fisher para una tabla 2x2 [[a, b], [c, d]].

    Se usa para contrastar el acelerador de anclaje ({1, 7, 8}) sin asumir
    normalidad ni independencia de los subconjuntos.
    """
    total = a + b + c + d
    if total == 0:
        return 1.0
    row_one = a + b
    column_one = a + c

    def log_probability(x: int) -> float:
        # P(x aciertos en la fila 1) = C(fila1, x)·C(N-fila1, col1-x) / C(N, col1)
        return (
            math.lgamma(row_one + 1)
            - math.lgamma(x + 1)
            - math.lgamma(row_one - x + 1)
            + math.lgamma(total - row_one + 1)
            - math.lgamma(column_one - x + 1)
            - math.lgamma(total - row_one - column_one + x + 1)
            - math.lgamma(total + 1)
            + math.lgamma(column_one + 1)
            + math.lgamma(total - column_one + 1)
        )

    reference = log_probability(a)
    probability = 0.0
    lower = max(0, column_one - (total - row_one))
    upper = min(row_one, column_one)
    for x in range(lower, upper + 1):
        log_p = log_probability(x)
        if log_p <= reference + 1e-9:
            probability += math.exp(log_p)
    return min(1.0, max(0.0, probability))

def bootstrap_interval(
    values: Sequence[float],
    statistic: Callable[[np.ndarray], float],
    iterations: int = 4000,
    confidence: float = 0.95,
    seed: int = 20261021,
) -> Interval:
    """Intervalo de confianza percentil por bootstrap."""
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return Interval(float("nan"), float("nan"))
    generator = np.random.default_rng(seed)
    sample = generator.choice(array, size=(iterations, array.size), replace=True)
    statistics = np.array([statistic(row) for row in sample], dtype=float)
    alpha = (1.0 - confidence) / 2.0
    low, high = np.quantile(statistics, [alpha, 1.0 - alpha])
    return Interval(float(low), float(high))


def holm_bonferroni(p_values: Sequence[float]) -> list[float]:
    """Ajuste de Holm-Bonferroni para controles de multiplicidad."""
    if not p_values:
        return []
    order = sorted(range(len(p_values)), key=lambda index: p_values[index])
    adjusted = [0.0] * len(p_values)
    running = 0.0
    total = len(p_values)
    for rank, index in enumerate(order):
        value = min(1.0, (total - rank) * p_values[index])
        running = max(running, value)
        adjusted[index] = running
    return adjusted


def proportion_difference_test(
    successes_a: int,
    trials_a: int,
    successes_b: int,
    trials_b: int,
) -> dict[str, float]:
    """Comparación de dos proporciones con intervalo de Newcombe (no solapado)."""
    proportion_a = successes_a / trials_a if trials_a else 0.0
    proportion_b = successes_b / trials_b if trials_b else 0.0
    interval_a = wilson_interval(successes_a, trials_a)
    interval_b = wilson_interval(successes_b, trials_b)
    lower = proportion_a - interval_b.high
    upper = interval_a.low - proportion_b
    difference = proportion_a - proportion_b
    standard_error = math.sqrt(
        max(
            0.0,
            proportion_a * (1.0 - proportion_a) / trials_a
            + proportion_b * (1.0 - proportion_b) / trials_b,
        )
    ) if trials_a and trials_b else float("nan")
    z_score = difference / standard_error if standard_error else float("nan")
    p_value = 2.0 * (1.0 - NormalDist().cdf(abs(z_score))) if standard_error else float("nan")
    return {
        "difference": difference,
        "lower": lower,
        "upper": upper,
        "z_score": z_score,
        "p_value": p_value,
    }
