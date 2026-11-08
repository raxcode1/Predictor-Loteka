"""Inferencia bayesiana explícita (prior, verosimilitud, posterior).

- Beta-Binomial para tasas de acierto (p. ej. top-1 de un modelo):
  prior Beta(a, b), verosimilitud Binomial(n, p), posterior Beta(a+h, b+n-h).
- Dirichlet-Multinomial para frecuencias de los 19 números.
- Bayes factors con verosimilitud marginal cerrada:
  * Dirichlet-Multinomial frente a uniforme fija (¿frecuencias sesgadas?),
  * Markov-1 Dirichlet frente a i.i.d. Dirichlet (¿dependencia de orden 1?).

Se publica la sensibilidad a priors (uniforme, Jeffreys y escéptico centrado en
1/19) para que ninguna conclusión dependa de una elección arbitraria.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import stats
from scipy.special import gammaln

from . import BASELINE, OUTPUT_MODULUS

K = OUTPUT_MODULUS


@dataclass(frozen=True)
class BetaPrior:
    name: str
    a: float
    b: float


def standard_priors(center: float = BASELINE, skeptical_strength: float = 200.0) -> list[BetaPrior]:
    """Priors reportados: uniforme, Jeffreys y escéptico (masa en la nula)."""
    return [
        BetaPrior("uniforme Beta(1,1)", 1.0, 1.0),
        BetaPrior("Jeffreys Beta(0.5,0.5)", 0.5, 0.5),
        BetaPrior(
            f"escéptico Beta centrado en {center:.4f} (n0={skeptical_strength:g})",
            center * skeptical_strength,
            (1.0 - center) * skeptical_strength,
        ),
    ]


def beta_binomial_posterior(
    hits: int,
    trials: int,
    prior: BetaPrior,
    threshold: float = BASELINE,
    credibility: float = 0.95,
) -> dict[str, Any]:
    """Posterior de una tasa de acierto y P(tasa > umbral)."""
    if trials < 0 or hits < 0 or hits > trials:
        raise ValueError("Se requiere 0 <= hits <= trials.")
    a = prior.a + hits
    b = prior.b + trials - hits
    posterior = stats.beta(a, b)
    tail = (1.0 - credibility) / 2.0
    return {
        "prior": prior.name,
        "prior_a": prior.a,
        "prior_b": prior.b,
        "hits": int(hits),
        "trials": int(trials),
        "posterior_a": a,
        "posterior_b": b,
        "posterior_mean": float(posterior.mean()),
        "credible_interval": [float(posterior.ppf(tail)), float(posterior.ppf(1 - tail))],
        "credibility": credibility,
        "threshold": threshold,
        "prob_above_threshold": float(posterior.sf(threshold)),
    }


def prior_sensitivity(
    hits: int, trials: int, threshold: float = BASELINE, credibility: float = 0.95
) -> list[dict[str, Any]]:
    return [
        beta_binomial_posterior(hits, trials, prior, threshold, credibility)
        for prior in standard_priors(threshold)
    ]


def dirichlet_frequency_posterior(
    counts: np.ndarray, alpha: float = 1.0, credibility: float = 0.95
) -> list[dict[str, Any]]:
    """Marginales Beta del posterior Dirichlet(alpha + counts) por número."""
    counts = np.asarray(counts, dtype=float)
    total = counts.sum() + K * alpha
    tail = (1.0 - credibility) / 2.0
    rows = []
    for number in range(K):
        a = alpha + counts[number]
        b = total - a
        marginal = stats.beta(a, b)
        rows.append(
            {
                "number": number,
                "count": int(counts[number]),
                "posterior_mean": float(a / total),
                "credible_interval": [float(marginal.ppf(tail)), float(marginal.ppf(1 - tail))],
                "prob_above_baseline": float(marginal.sf(BASELINE)),
            }
        )
    return rows


def log_marginal_dirichlet_multinomial(counts: np.ndarray, alpha: float) -> float:
    """log P(secuencia | Dirichlet(alpha)) para una secuencia con esos conteos."""
    counts = np.asarray(counts, dtype=float)
    n = counts.sum()
    k = counts.size
    return float(
        gammaln(k * alpha)
        - gammaln(n + k * alpha)
        + np.sum(gammaln(counts + alpha) - gammaln(alpha))
    )


def log_bayes_factor_frequency(counts: np.ndarray, alpha: float = 1.0) -> float:
    """log BF(Dirichlet vs uniforme fija). > 0 favorece frecuencias sesgadas."""
    counts = np.asarray(counts, dtype=float)
    log_uniform = -counts.sum() * np.log(K)
    return log_marginal_dirichlet_multinomial(counts, alpha) - log_uniform


def log_bayes_factor_markov1(transitions: np.ndarray, alpha: float = 1.0) -> float:
    """log BF(Markov-1 vs i.i.d.), ambos con priors Dirichlet(alpha).

    Ambos modelos se evalúan sobre los mismos símbolos destino (transiciones
    consecutivas), de modo que la comparación es justa.
    """
    transitions = np.asarray(transitions, dtype=float)
    markov = sum(log_marginal_dirichlet_multinomial(row, alpha) for row in transitions)
    iid = log_marginal_dirichlet_multinomial(transitions.sum(axis=0), alpha)
    return float(markov - iid)


def bayes_factor_sensitivity(
    counts: np.ndarray, transitions: np.ndarray, alphas: tuple[float, ...] = (0.5, 1.0, 10.0)
) -> list[dict[str, float]]:
    return [
        {
            "alpha": alpha,
            "log_bf_frequency_vs_uniform": log_bayes_factor_frequency(counts, alpha),
            "log_bf_markov1_vs_iid": log_bayes_factor_markov1(transitions, alpha),
        }
        for alpha in alphas
    ]
