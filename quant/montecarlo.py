"""Simulaciones Monte Carlo deterministas por semilla.

Las simulaciones cuantifican variabilidad; no son evidencia causal:

- `null_log_loss`: distribución de la mejora de log-loss frente a 1/19 cuando
  los resultados son i.i.d. uniformes, condicional a las predicciones del test.
- `null_hits`: distribución de aciertos top-1 bajo la nula (contraste del test
  binomial exacto).
- `bankroll`: trayectorias de capital para una tasa de acierto fija (nula) o
  muestreada del posterior Beta (incertidumbre posterior): ROI, drawdown máximo
  y probabilidad de ruina.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from . import BASELINE, OUTPUT_MODULUS

K = OUTPUT_MODULUS


def null_log_loss(
    probabilities: np.ndarray, observed_improvement: float, sims: int = 1000, seed: int = 0, chunk: int = 100
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    n = probabilities.shape[0]
    log_p = np.log(np.clip(probabilities, 1e-12, 1.0))
    improvements = np.empty(sims)
    rows = np.arange(n)
    done = 0
    while done < sims:
        size = min(chunk, sims - done)
        y = rng.integers(0, K, size=(size, n))
        losses = -log_p[rows[None, :], y].mean(axis=1)
        improvements[done : done + size] = math.log(K) - losses
        done += size
    p_value = (1 + int(np.sum(improvements >= observed_improvement))) / (sims + 1)
    return {
        "sims": sims,
        "seed": seed,
        "observed_improvement": observed_improvement,
        "null_mean": float(improvements.mean()),
        "null_quantiles": {
            "q05": float(np.quantile(improvements, 0.05)),
            "q50": float(np.quantile(improvements, 0.5)),
            "q95": float(np.quantile(improvements, 0.95)),
        },
        "p_value": p_value,
    }


def null_hits(n: int, observed_hits: int, sims: int = 10000, seed: int = 0) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    hits = rng.binomial(n, BASELINE, size=sims)
    return {
        "sims": sims,
        "seed": seed,
        "n": n,
        "observed_hits": observed_hits,
        "null_mean": float(hits.mean()),
        "null_q95": float(np.quantile(hits, 0.95)),
        "p_value": (1 + int(np.sum(hits >= observed_hits))) / (sims + 1),
    }


def bankroll(
    n_bets: int,
    stake_fraction: float,
    net_multiplier: float,
    sims: int = 2000,
    seed: int = 0,
    hit_rate: float | None = None,
    posterior: tuple[float, float] | None = None,
    ruin_level: float = 0.5,
) -> dict[str, Any]:
    """Capital relativo (inicio = 1) apostando `stake_fraction` en cada ronda."""
    if (hit_rate is None) == (posterior is None):
        raise ValueError("Indica hit_rate o posterior (a, b), no ambos.")
    if not 0.0 <= stake_fraction < 1.0:
        raise ValueError("stake_fraction debe estar en [0, 1).")
    rng = np.random.default_rng(seed)
    if posterior is not None:
        rates = rng.beta(posterior[0], posterior[1], size=sims)
    else:
        assert hit_rate is not None
        rates = np.full(sims, float(hit_rate))
    wins = rng.random((sims, n_bets)) < rates[:, None]
    growth = np.where(wins, 1.0 + stake_fraction * net_multiplier, 1.0 - stake_fraction)
    paths = np.cumprod(growth, axis=1)
    peaks = np.maximum.accumulate(np.concatenate([np.ones((sims, 1)), paths], axis=1), axis=1)[:, 1:]
    drawdown = 1.0 - paths / peaks
    final = paths[:, -1] if n_bets else np.ones(sims)
    ruined = np.any(paths <= ruin_level, axis=1) if n_bets else np.zeros(sims, dtype=bool)
    return {
        "sims": sims,
        "seed": seed,
        "n_bets": n_bets,
        "stake_fraction": stake_fraction,
        "net_multiplier": net_multiplier,
        "hit_rate": hit_rate,
        "posterior": list(posterior) if posterior else None,
        "roi_mean": float(final.mean() - 1.0),
        "roi_q05": float(np.quantile(final, 0.05) - 1.0),
        "roi_q50": float(np.quantile(final, 0.5) - 1.0),
        "roi_q95": float(np.quantile(final, 0.95) - 1.0),
        "max_drawdown_median": float(np.median(drawdown.max(axis=1))) if n_bets else 0.0,
        "max_drawdown_q95": float(np.quantile(drawdown.max(axis=1), 0.95)) if n_bets else 0.0,
        "ruin_level": ruin_level,
        "ruin_probability": float(ruined.mean()),
        "prob_profit": float(np.mean(final > 1.0)),
    }
