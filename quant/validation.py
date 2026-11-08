"""Validación temporal sin look-ahead.

Protocolo (fijado antes de mirar el test):

1. La serie se divide en tres bloques cronológicos contiguos:
   `train` (calentamiento de conteos), `validation` (selección de modelo y
   recalibración) y `test` (bloqueado, se evalúa una sola vez).
2. Cada modelo se evalúa **prequencialmente** (expanding window): la
   predicción del sorteo t sólo usa sorteos < t.
3. El modelo se elige por log-loss en `validation`. La recalibración
   (mezcla con la uniforme, parámetro lambda) también se ajusta sólo ahí.
4. En `test` se compara el modelo seleccionado contra la uniforme 1/19 con un
   test de Diebold-Mariano unilateral con varianza HAC (Newey-West), y la
   tasa top-1 con un test binomial exacto.
5. Walk-forward: el test se divide en bloques para medir estabilidad.

Métricas: log-loss (scikit-learn), Brier multiclase, top-k, ECE (top-label y
one-vs-rest), diagrama de fiabilidad y descomposición de Murphy del Brier.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import numpy as np
from scipy import optimize, stats
from sklearn.calibration import calibration_curve
from sklearn.metrics import log_loss as sk_log_loss

from . import BASELINE, OUTPUT_MODULUS
from .data import Dataset
from .models import UNIFORM, ModelSpec, PredictionCache, default_grid

K = OUTPUT_MODULUS
LABELS = list(range(K))
EPS = 1e-12


@dataclass(frozen=True)
class Partition:
    train_end: int
    validation_end: int
    n: int

    @property
    def validation(self) -> slice:
        return slice(self.train_end, self.validation_end)

    @property
    def test(self) -> slice:
        return slice(self.validation_end, self.n)

    def as_dict(self, dataset: Dataset | None = None) -> dict[str, Any]:
        def bounds(start: int, end: int) -> dict[str, Any]:
            info: dict[str, Any] = {"start_index": start, "end_index": end, "size": end - start}
            if dataset is not None and end > start:
                info["first_draw"] = int(dataset.draw_ids[start])
                info["last_draw"] = int(dataset.draw_ids[end - 1])
            return info

        return {
            "train": bounds(0, self.train_end),
            "validation": bounds(self.train_end, self.validation_end),
            "test": bounds(self.validation_end, self.n),
        }


def make_partition(n: int, train: float = 0.6, validation: float = 0.2) -> Partition:
    if not 0 < train < 1 or not 0 < validation < 1 or train + validation >= 1:
        raise ValueError("Fracciones de partición inválidas.")
    train_end = int(n * train)
    validation_end = int(n * (train + validation))
    if train_end < 1 or validation_end - train_end < 1 or n - validation_end < 1:
        raise ValueError(f"Serie demasiado corta para particionar (n={n}).")
    return Partition(train_end, validation_end, n)


# ----------------------------------------------------------------------
# Métricas
# ----------------------------------------------------------------------
def per_round_log_loss(probabilities: np.ndarray, y: np.ndarray) -> np.ndarray:
    picked = probabilities[np.arange(y.size), y]
    return -np.log(np.clip(picked, EPS, 1.0))


def brier_multiclass(probabilities: np.ndarray, y: np.ndarray) -> float:
    onehot = np.zeros_like(probabilities)
    onehot[np.arange(y.size), y] = 1.0
    return float(np.mean(np.sum((probabilities - onehot) ** 2, axis=1)))


def top_k_hits(probabilities: np.ndarray, y: np.ndarray, k: int) -> np.ndarray:
    order = np.argsort(-probabilities, axis=1, kind="stable")[:, :k]
    return np.any(order == y[:, None], axis=1)


def _binned_calibration(
    confidence: np.ndarray, outcome: np.ndarray, bins: int
) -> tuple[float, list[dict[str, float]], dict[str, float]]:
    """ECE con bins por cuantiles + descomposición de Murphy del Brier binario."""
    n = confidence.size
    if n == 0:
        return 0.0, [], {"reliability": 0.0, "resolution": 0.0, "uncertainty": 0.0}
    edges = np.unique(np.quantile(confidence, np.linspace(0, 1, bins + 1)))
    if edges.size < 2:
        edges = np.array([confidence.min(), confidence.max() + EPS])
    index = np.clip(np.searchsorted(edges, confidence, side="right") - 1, 0, edges.size - 2)
    base_rate = float(outcome.mean())
    ece = 0.0
    reliability = 0.0
    resolution = 0.0
    diagram = []
    for b in range(edges.size - 1):
        mask = index == b
        count = int(mask.sum())
        if not count:
            continue
        mean_conf = float(confidence[mask].mean())
        freq = float(outcome[mask].mean())
        weight = count / n
        ece += weight * abs(mean_conf - freq)
        reliability += weight * (mean_conf - freq) ** 2
        resolution += weight * (freq - base_rate) ** 2
        diagram.append({"mean_predicted": mean_conf, "observed_frequency": freq, "count": count})
    decomposition = {
        "reliability": reliability,
        "resolution": resolution,
        "uncertainty": base_rate * (1.0 - base_rate),
    }
    return ece, diagram, decomposition


def evaluate(probabilities: np.ndarray, y: np.ndarray, bins: int = 10) -> dict[str, Any]:
    """Métricas completas de un bloque de predicciones prequenciales."""
    if y.size == 0:
        raise ValueError("Bloque de evaluación vacío.")
    losses = per_round_log_loss(probabilities, y)
    top1 = top_k_hits(probabilities, y, 1)
    top_conf = probabilities.max(axis=1)
    top_ece, top_diagram, _ = _binned_calibration(top_conf, top1.astype(float), bins)
    flat_conf = probabilities.ravel()
    flat_outcome = np.zeros_like(probabilities)
    flat_outcome[np.arange(y.size), y] = 1.0
    ovr_ece, _, murphy = _binned_calibration(flat_conf, flat_outcome.ravel(), bins)
    try:
        frac_pos, mean_pred = calibration_curve(
            flat_outcome.ravel(), flat_conf, n_bins=bins, strategy="quantile"
        )
        reliability_diagram = [
            {"mean_predicted": float(p), "observed_frequency": float(f)}
            for p, f in zip(mean_pred, frac_pos)
        ]
    except ValueError:
        reliability_diagram = []
    return {
        "n": int(y.size),
        "log_loss": float(sk_log_loss(y, np.clip(probabilities, EPS, 1.0), labels=LABELS)),
        "log_loss_uniform": math.log(K),
        "brier": brier_multiclass(probabilities, y),
        "brier_uniform": 1.0 - 1.0 / K,
        "top1": float(top1.mean()),
        "top1_hits": int(top1.sum()),
        "top3": float(top_k_hits(probabilities, y, 3).mean()),
        "top5": float(top_k_hits(probabilities, y, 5).mean()),
        "baseline_top1": BASELINE,
        "baseline_top3": 3 * BASELINE,
        "baseline_top5": 5 * BASELINE,
        "ece_top_label": top_ece,
        "ece_one_vs_rest": ovr_ece,
        "reliability_top_label": top_diagram,
        "reliability_one_vs_rest": reliability_diagram,
        "brier_decomposition_one_vs_rest": murphy,
        "mean_loss_per_round": losses,  # se elimina antes de serializar
    }


def _strip(metrics: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in metrics.items() if k != "mean_loss_per_round"}


def diebold_mariano(loss_model: np.ndarray, loss_reference: np.ndarray) -> dict[str, float]:
    """H0: el modelo no mejora a la referencia. Unilateral, varianza Newey-West."""
    d = loss_reference - loss_model  # > 0 si el modelo pierde menos
    n = d.size
    mean = float(d.mean())
    centered = d - mean
    lag = max(1, round(n ** (1.0 / 3.0)))
    variance = float(np.dot(centered, centered) / n)
    for k in range(1, lag + 1):
        weight = 1.0 - k / (lag + 1.0)
        variance += 2.0 * weight * float(np.dot(centered[k:], centered[:-k]) / n)
    if variance <= 0:
        # Pérdidas idénticas (p. ej. el modelo es la uniforme): sin mejora.
        return {"mean_improvement": mean, "statistic": 0.0, "p_value": 1.0 if mean <= 0 else 0.5, "lag": lag}
    statistic = mean / math.sqrt(variance / n)
    return {
        "mean_improvement": mean,
        "statistic": float(statistic),
        "p_value": float(stats.norm.sf(statistic)),
        "lag": lag,
    }


def fit_recalibration(probabilities: np.ndarray, y: np.ndarray) -> float:
    """lambda en [0,1] que minimiza log-loss de (1-l) p + l/19 en validation."""

    def objective(lam: float) -> float:
        mixed = (1.0 - lam) * probabilities + lam / K
        return float(per_round_log_loss(mixed, y).mean())

    result = optimize.minimize_scalar(objective, bounds=(0.0, 1.0), method="bounded")
    return float(np.clip(result.x, 0.0, 1.0))


def apply_recalibration(probabilities: np.ndarray, lam: float) -> np.ndarray:
    return (1.0 - lam) * probabilities + lam / K


def holm(p_values: list[float]) -> list[float]:
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [1.0] * m
    running = 0.0
    for rank, index in enumerate(order):
        value = min(1.0, (m - rank) * p_values[index])
        running = max(running, value)
        adjusted[index] = running
    return adjusted


# ----------------------------------------------------------------------
# Protocolo completo
# ----------------------------------------------------------------------
def walk_forward(
    dataset: Dataset,
    grid: list[ModelSpec] | None = None,
    train: float = 0.6,
    validation: float = 0.2,
    test_blocks: int = 5,
    cache: PredictionCache | None = None,
) -> dict[str, Any]:
    grid = list(grid or default_grid())
    if UNIFORM not in grid:
        grid.insert(0, UNIFORM)
    n = len(dataset)
    partition = make_partition(n, train, validation)
    cache = cache or PredictionCache(dataset)
    y = dataset.values
    y_val = y[partition.validation]
    y_test = y[partition.test]

    candidates: list[dict[str, Any]] = []
    matrices: dict[str, np.ndarray] = {}
    for spec in grid:
        matrix = cache.matrix(spec)
        matrices[spec.name] = matrix
        val_loss = float(per_round_log_loss(matrix[partition.validation], y_val).mean())
        candidates.append({"model": spec.as_dict(), "validation_log_loss": val_loss})

    best_index = min(range(len(grid)), key=lambda i: candidates[i]["validation_log_loss"])
    selected = grid[best_index]
    matrix = matrices[selected.name]
    lam = fit_recalibration(matrix[partition.validation], y_val) if selected != UNIFORM else 1.0

    raw_test = matrix[partition.test]
    cal_test = apply_recalibration(raw_test, lam)
    uniform_test = np.full_like(raw_test, 1.0 / K)
    uniform_losses = per_round_log_loss(uniform_test, y_test)

    raw_metrics = evaluate(raw_test, y_test)
    cal_metrics = evaluate(cal_test, y_test)
    dm_raw = diebold_mariano(raw_metrics["mean_loss_per_round"], uniform_losses)
    dm_cal = diebold_mariano(cal_metrics["mean_loss_per_round"], uniform_losses)
    top1_test = stats.binomtest(
        raw_metrics["top1_hits"], raw_metrics["n"], BASELINE, alternative="greater"
    )

    # Informativo: todos los candidatos en test con corrección de Holm.
    # NO se usa para decidir (sería selección sobre el test).
    exploratory: list[dict[str, Any]] = []
    for spec, candidate in zip(grid, candidates):
        if spec == UNIFORM:
            continue
        losses = per_round_log_loss(matrices[spec.name][partition.test], y_test)
        dm = diebold_mariano(losses, uniform_losses)
        exploratory.append({"model": spec.name, "test_log_loss": float(losses.mean()), "dm_p_value": dm["p_value"]})
    for item, adjusted in zip(exploratory, holm([e["dm_p_value"] for e in exploratory])):
        item["dm_p_value_holm"] = adjusted

    blocks = []
    edges = np.linspace(partition.validation_end, n, test_blocks + 1).astype(int)
    for start, end in pairwise(edges):
        if end <= start:
            continue
        block_y = y[start:end]
        block_p = apply_recalibration(matrix[start:end], lam)
        losses = per_round_log_loss(block_p, block_y)
        hits = top_k_hits(block_p, block_y, 1)
        blocks.append(
            {
                "first_draw": int(dataset.draw_ids[start]),
                "last_draw": int(dataset.draw_ids[end - 1]),
                "n": int(end - start),
                "log_loss": float(losses.mean()),
                "log_loss_improvement_vs_uniform": float(math.log(K) - losses.mean()),
                "top1": float(hits.mean()),
            }
        )

    return {
        "partition": partition.as_dict(dataset),
        "protocol": "prequential expanding-window; selección y recalibración sólo en validation; test bloqueado",
        "candidates": candidates,
        "selected_model": selected.as_dict(),
        "recalibration_lambda": lam,
        "test_raw": _strip(raw_metrics),
        "test_calibrated": _strip(cal_metrics),
        "diebold_mariano_raw": dm_raw,
        "diebold_mariano_calibrated": dm_cal,
        "top1_binomial_test": {
            "hits": raw_metrics["top1_hits"],
            "n": raw_metrics["n"],
            "baseline": BASELINE,
            "p_value": float(top1_test.pvalue),
        },
        "walk_forward_blocks": blocks,
        "blocks_beating_uniform": sum(1 for b in blocks if b["log_loss_improvement_vs_uniform"] > 0),
        "exploratory_all_candidates_test": exploratory,
    }
