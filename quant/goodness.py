"""Batería de bondad de ajuste e independencia frente a i.i.d. uniforme 0..18.

Cada test reporta estadístico, grados de libertad, p-value, tamaño de efecto y
limitaciones. Las p-values de la familia se corrigen con Holm (FWER) y con
Benjamini-Hochberg (FDR). Las transiciones sólo usan pares de sorteos
consecutivos en la numeración (los huecos no se tratan como adyacencias).

Notas de validez:
- Chi-cuadrado requiere frecuencias esperadas >= 5; se informa el mínimo.
- KS no es válido sobre una variable discreta; se aplica sobre la PIT
  aleatorizada u = (x + V)/19 con V ~ U(0,1) y semilla registrada, que bajo la
  nula es exactamente U(0,1) i.i.d. Es menos potente que Chi-cuadrado.
- Ljung-Box trata los valores como numéricos: sólo detecta dependencia lineal.
"""

from __future__ import annotations

import math
from itertools import pairwise
from typing import Any

import numpy as np
from scipy import stats
from sklearn.metrics import mutual_info_score

from . import BASELINE, OUTPUT_MODULUS
from .data import Dataset
from .validation import holm

K = OUTPUT_MODULUS


def _pairs(dataset: Dataset, lag: int) -> tuple[np.ndarray, np.ndarray]:
    values = dataset.values
    ids = dataset.draw_ids
    if values.size <= lag:
        return np.array([], dtype=np.int64), np.array([], dtype=np.int64)
    mask = (ids[lag:] - ids[:-lag]) == lag
    return values[:-lag][mask], values[lag:][mask]


def transition_matrix(dataset: Dataset, lag: int = 1) -> np.ndarray:
    previous, current = _pairs(dataset, lag)
    table = np.zeros((K, K), dtype=np.int64)
    np.add.at(table, (previous, current), 1)
    return table


def chi_square_uniform(values: np.ndarray) -> dict[str, Any]:
    counts = np.bincount(values, minlength=K)
    n = int(counts.sum())
    result = stats.chisquare(counts)
    expected = n / K
    return {
        "name": "chi2_frecuencias_uniformes",
        "statistic": float(result.statistic),
        "df": K - 1,
        "p_value": float(result.pvalue),
        "effect_size": {"cohen_w": math.sqrt(float(result.statistic) / n) if n else 0.0},
        "min_expected": expected,
        "detail": {
            "counts": counts.tolist(),
            "expected": expected,
            "standardized_residuals": ((counts - expected) / math.sqrt(expected)).round(3).tolist()
            if n
            else [],
        },
        "limitations": "Asume independencia entre sorteos.",
    }


def _contingency(name: str, table: np.ndarray, limitation: str) -> dict[str, Any]:
    # Se eliminan filas/columnas vacías para que el test esté definido.
    table = table[table.sum(axis=1) > 0][:, table.sum(axis=0) > 0]
    statistic, p_value, df, expected = stats.chi2_contingency(table, correction=False)
    n = int(table.sum())
    r, c = table.shape
    cramer = math.sqrt(statistic / (n * max(1, min(r, c) - 1))) if n else 0.0
    return {
        "name": name,
        "statistic": float(statistic),
        "df": int(df),
        "p_value": float(p_value),
        "effect_size": {"cramers_v": cramer},
        "min_expected": float(expected.min()),
        "limitations": limitation,
    }


def transition_independence(dataset: Dataset, lag: int = 1) -> dict[str, Any]:
    table = transition_matrix(dataset, lag)
    result = _contingency(
        f"chi2_independencia_lag{lag}",
        table,
        "Sólo pares consecutivos; detecta dependencia de orden 1 a ese retardo.",
    )
    previous, current = _pairs(dataset, lag)
    mi = float(mutual_info_score(previous, current)) if previous.size else 0.0
    result["effect_size"]["mutual_information_nats"] = mi
    result["effect_size"]["mutual_information_bits"] = mi / math.log(2)
    return result


def block_homogeneity(dataset: Dataset, blocks: int = 10) -> dict[str, Any]:
    """Cambio de régimen: ¿la distribución es la misma en bloques temporales?"""
    edges = np.linspace(0, len(dataset), blocks + 1).astype(int)
    table = np.vstack(
        [np.bincount(dataset.values[a:b], minlength=K) for a, b in pairwise(edges) if b > a]
    )
    return _contingency(
        f"chi2_homogeneidad_{blocks}_bloques",
        table,
        "Detecta cambios de distribución marginal entre bloques; no localiza el punto de cambio.",
    )


def repeat_rate(dataset: Dataset) -> dict[str, Any]:
    previous, current = _pairs(dataset, 1)
    n = int(previous.size)
    repeats = int((previous == current).sum())
    test = stats.binomtest(repeats, n, BASELINE) if n else None
    return {
        "name": "binomial_repeticiones_adyacentes",
        "statistic": float(repeats),
        "df": None,
        "p_value": float(test.pvalue) if test else 1.0,
        "effect_size": {"observed_rate": repeats / n if n else 0.0, "expected_rate": BASELINE},
        "limitations": "Contrasta la hipótesis anti-repetición (forbid_repeat).",
    }


def runs_test(values: np.ndarray, consecutive: np.ndarray | None = None) -> dict[str, Any]:
    """Wald-Wolfowitz sobre x > 9 (alto) vs x <= 9 (bajo)."""
    binary = (values > 9).astype(int)
    n1 = int(binary.sum())
    n0 = int(binary.size - n1)
    n = n0 + n1
    if n1 == 0 or n0 == 0:
        return {"name": "runs_alto_bajo", "statistic": 0.0, "df": None, "p_value": 1.0,
                "effect_size": {}, "limitations": "Serie constante."}
    runs = 1 + int(np.sum(binary[1:] != binary[:-1]))
    mean = 2.0 * n0 * n1 / n + 1.0
    variance = 2.0 * n0 * n1 * (2.0 * n0 * n1 - n) / (n * n * (n - 1.0))
    z = (runs - mean) / math.sqrt(variance)
    return {
        "name": "runs_alto_bajo",
        "statistic": float(z),
        "df": None,
        "p_value": float(2.0 * stats.norm.sf(abs(z))),
        "effect_size": {"runs": runs, "expected_runs": mean},
        "limitations": "Ignora huecos de numeración (afectan a < 2% de las adyacencias).",
    }


def ljung_box(values: np.ndarray, lags: int = 10) -> dict[str, Any]:
    x = values.astype(float) - values.mean()
    n = x.size
    denominator = float(np.dot(x, x))
    q = 0.0
    acf = []
    for k in range(1, lags + 1):
        rho = float(np.dot(x[k:], x[:-k]) / denominator)
        acf.append(rho)
        q += rho * rho / (n - k)
    q *= n * (n + 2)
    return {
        "name": f"ljung_box_{lags}_lags",
        "statistic": float(q),
        "df": lags,
        "p_value": float(stats.chi2.sf(q, lags)),
        "effect_size": {"max_abs_acf": float(max(abs(r) for r in acf)), "acf": [round(r, 5) for r in acf]},
        "limitations": "Sólo dependencia lineal sobre el valor numérico.",
    }


def ks_randomized_pit(values: np.ndarray, seed: int) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    u = (values + rng.random(values.size)) / K
    result = stats.kstest(u, "uniform")
    return {
        "name": "ks_pit_aleatorizada",
        "statistic": float(result.statistic),
        "df": None,
        "p_value": float(result.pvalue),
        "effect_size": {"d_statistic": float(result.statistic)},
        "seed": seed,
        "limitations": "KS sobre PIT aleatorizada (válida para discretos); menor potencia que chi2.",
    }


def entropy_summary(values: np.ndarray) -> dict[str, float]:
    counts = np.bincount(values, minlength=K).astype(float)
    p = counts / counts.sum()
    nz = p[p > 0]
    return {
        "shannon_bits": float(-(nz * np.log2(nz)).sum()),
        "min_entropy_bits": float(-math.log2(p.max())),
        "max_possible_bits": math.log2(K),
    }


def run_battery(dataset: Dataset, seed: int = 20261108, alpha: float = 0.01) -> dict[str, Any]:
    values = dataset.values
    tests = [
        chi_square_uniform(values),
        transition_independence(dataset, 1),
        transition_independence(dataset, 2),
        transition_independence(dataset, 3),
        block_homogeneity(dataset, 10),
        repeat_rate(dataset),
        runs_test(values),
        ljung_box(values, 10),
        ks_randomized_pit(values, seed),
    ]
    p_values = [t["p_value"] for t in tests]
    holm_adjusted = holm(p_values)
    bh_adjusted = stats.false_discovery_control(np.asarray(p_values), method="bh")
    for test, h, bh in zip(tests, holm_adjusted, bh_adjusted):
        test["p_value_holm"] = float(h)
        test["p_value_bh"] = float(bh)
        test["reject_holm"] = bool(h < alpha)
    rejected = [t["name"] for t in tests if t["reject_holm"]]
    return {
        "alpha_familywise": alpha,
        "tests": tests,
        "rejected_after_holm": rejected,
        "entropy": entropy_summary(values),
        "interpretation": (
            "Ningún test rechaza la nula i.i.d. uniforme tras Holm."
            if not rejected
            else "Se rechaza la nula en: " + ", ".join(rejected)
            + ". Un rechazo describe una desviación; no implica una ventaja explotable."
        ),
    }
