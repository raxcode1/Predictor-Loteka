"""Reglas de decisión: abstención por defecto y paper-trading.

El estado por defecto es «SIN VENTAJA». Sólo se pasa a «VENTAJA_CANDIDATA»
si TODAS las comprobaciones fuera de muestra se cumplen. Incluso entonces el
sistema permanece en modo `paper` salvo que el operador active explícitamente
`RULETA_TRADING_MODE=live`; sólo en ese caso `signals_allowed` puede ser True.

Las alertas M4 tienen su propia puerta: sus cohortes (ventana, alerta,
anclaje) se contrastan con test binomial unilateral corregido por Holm y con
un posterior Beta escéptico. El anclaje {1,7,8} nunca escala el stake.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from scipy import stats

from . import BASELINE, PIPELINE_VERSION
from .bayes import BetaPrior, beta_binomial_posterior
from .integration import MODE_LIVE, MODE_PAPER, STATUS_NO_EDGE, trading_mode
from .validation import holm

STATUS_CANDIDATE = "VENTAJA_CANDIDATA"

__all__ = [
    "MODE_LIVE",
    "MODE_PAPER",
    "STATUS_CANDIDATE",
    "STATUS_NO_EDGE",
    "DecisionConfig",
    "RiskLimits",
    "decide",
    "kelly_fraction",
    "m4_gate",
    "trading_mode",
]


@dataclass(frozen=True)
class DecisionConfig:
    alpha: float = 0.01
    min_test_rounds: int = 1000
    min_blocks_beating_uniform: int = 4
    max_ece_top_label: float = 0.02
    min_posterior_prob: float = 0.95
    skeptical_prior_strength: float = 200.0
    break_even: float = BASELINE
    min_alerts: int = 200


@dataclass(frozen=True)
class RiskLimits:
    """Límites duros aplicados a cualquier stake (también en papel)."""

    max_stake_fraction: float = 0.01
    max_daily_loss_fraction: float = 0.05
    max_bets_per_day: int = 20
    kelly_fraction: float = 0.25

    def allows(self, daily_pnl_fraction: float = 0.0, bets_today: int = 0) -> tuple[bool, str | None]:
        if daily_pnl_fraction <= -self.max_daily_loss_fraction:
            return False, f"límite de pérdida diaria alcanzado ({daily_pnl_fraction:.2%})"
        if bets_today >= self.max_bets_per_day:
            return False, f"límite de {self.max_bets_per_day} apuestas diarias alcanzado"
        return True, None


def _check(name: str, passed: bool, value: Any, threshold: Any, detail: str = "") -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "value": value, "threshold": threshold, "detail": detail}


def _skeptical(config: DecisionConfig) -> BetaPrior:
    strength = config.skeptical_prior_strength
    return BetaPrior(
        f"escéptico n0={strength:g}",
        config.break_even * strength,
        (1.0 - config.break_even) * strength,
    )


def kelly_fraction(rate: float, net_multiplier: float, limits: RiskLimits) -> float:
    full = (net_multiplier * rate - (1.0 - rate)) / net_multiplier
    if full <= 0:
        return 0.0
    return min(limits.kelly_fraction * full, limits.max_stake_fraction)


def decide(
    report: Mapping[str, Any],
    config: DecisionConfig | None = None,
    limits: RiskLimits | None = None,
    mode: str | None = None,
    net_multiplier: float = 18.0,
) -> dict[str, Any]:
    """Decisión auditable a partir del informe cuantitativo."""
    config = config or DecisionConfig()
    limits = limits or RiskLimits()
    mode = mode or trading_mode()
    wf = report.get("walk_forward") or {}
    checks: list[dict[str, Any]] = []

    selected = (wf.get("selected_model") or {}).get("name", "uniform")
    test = wf.get("test_calibrated") or {}
    n_test = int(test.get("n", 0))
    checks.append(_check("muestra_test_minima", n_test >= config.min_test_rounds, n_test, config.min_test_rounds))
    checks.append(
        _check(
            "modelo_no_uniforme",
            selected != "uniform",
            selected,
            "≠ uniform",
            "la validación eligió la propia hipótesis nula" if selected == "uniform" else "",
        )
    )
    dm = wf.get("diebold_mariano_calibrated") or {}
    dm_p = float(dm.get("p_value", 1.0))
    checks.append(_check("log_loss_test_vs_uniforme", dm_p < config.alpha, dm_p, f"p < {config.alpha}", "Diebold-Mariano HAC unilateral"))
    blocks = int(wf.get("blocks_beating_uniform", 0))
    checks.append(
        _check("estabilidad_walk_forward", blocks >= config.min_blocks_beating_uniform, blocks, f">= {config.min_blocks_beating_uniform} bloques")
    )
    ece = float(test.get("ece_top_label", 1.0))
    checks.append(_check("calibracion_ece", ece <= config.max_ece_top_label, ece, f"<= {config.max_ece_top_label}"))

    raw = wf.get("test_raw") or {}
    hits = int(raw.get("top1_hits", 0))
    trials = int(raw.get("n", 0))
    posterior = beta_binomial_posterior(hits, trials, _skeptical(config), config.break_even) if trials else None
    post_prob = posterior["prob_above_threshold"] if posterior else 0.0
    lower = posterior["credible_interval"][0] if posterior else 0.0
    checks.append(
        _check("posterior_top1_sobre_break_even", post_prob >= config.min_posterior_prob, post_prob, f">= {config.min_posterior_prob}")
    )
    checks.append(_check("limite_inferior_credible", lower > config.break_even, lower, f"> {config.break_even:.4f}"))

    passed = all(c["passed"] for c in checks)
    status = STATUS_CANDIDATE if passed else STATUS_NO_EDGE
    stake = kelly_fraction(lower, net_multiplier, limits) if passed else 0.0
    allowed, risk_reason = limits.allows()
    signals_allowed = passed and mode == MODE_LIVE and allowed and stake > 0
    reasons = [f"{c['name']}: {c['value']} (umbral {c['threshold']})" for c in checks if not c["passed"]]
    if passed and mode != MODE_LIVE:
        reasons.append("modo paper-trading: no se emiten señales reales")
    if risk_reason:
        reasons.append(risk_reason)

    dataset = report.get("dataset") or {}
    return {
        "status": status,
        "trading_mode": mode,
        "signals_allowed": bool(signals_allowed),
        "recommended_stake_fraction": stake if signals_allowed else 0.0,
        "paper_stake_fraction": stake,
        "model": selected,
        "pipeline_version": PIPELINE_VERSION,
        "data_sha256": dataset.get("sha256"),
        "data_last_draw": dataset.get("last_draw"),
        "generated_at": report.get("generated_at"),
        "sample_size": n_test,
        "p_value": dm_p,
        "posterior_prob_above_break_even": post_prob,
        "credible_interval": posterior["credible_interval"] if posterior else None,
        "checks": checks,
        "reasons": reasons,
        "abstain": not signals_allowed,
        "risk_limits": asdict(limits),
        "config": asdict(config),
    }


def m4_gate(
    alert_stats: Mapping[str, Any] | None,
    config: DecisionConfig | None = None,
) -> dict[str, Any]:
    """Puerta de evidencia para la alerta M4 (cohortes corregidas por Holm)."""
    config = config or DecisionConfig()
    stats_in = dict(alert_stats or {})
    cohorts = [
        ("ventana", int(stats_in.get("window_hits", 0)), int(stats_in.get("window_rounds", 0))),
        ("alerta", int(stats_in.get("alert_hits", 0)), int(stats_in.get("alerts", 0))),
        ("anclaje", int(stats_in.get("anchor_hits", 0)), int(stats_in.get("anchor_alerts", 0))),
    ]
    p_values = [
        float(stats.binomtest(h, n, config.break_even, alternative="greater").pvalue) if n else 1.0
        for _, h, n in cohorts
    ]
    adjusted = holm(p_values)
    rows: list[dict[str, Any]] = []
    for (name, h, n), p, adj in zip(cohorts, p_values, adjusted):
        post = beta_binomial_posterior(h, n, _skeptical(config), config.break_even) if n else None
        rows.append(
            {
                "cohort": name,
                "hits": h,
                "trials": n,
                "rate": h / n if n else 0.0,
                "p_value": p,
                "p_value_holm": adj,
                "posterior_lower": post["credible_interval"][0] if post else 0.0,
                "posterior_prob_above_break_even": post["prob_above_threshold"] if post else 0.0,
            }
        )
    alert = rows[1]
    passed = (
        alert["trials"] >= config.min_alerts
        and alert["p_value_holm"] < config.alpha
        and alert["posterior_lower"] > config.break_even
    )
    reasons = []
    if alert["trials"] < config.min_alerts:
        reasons.append(f"alertas resueltas {alert['trials']} < {config.min_alerts}")
    if alert["p_value_holm"] >= config.alpha:
        reasons.append(f"p(Holm)={alert['p_value_holm']:.3g} >= {config.alpha}")
    if alert["posterior_lower"] <= config.break_even:
        reasons.append(
            f"límite inferior posterior {alert['posterior_lower']:.2%} <= break-even {config.break_even:.2%}"
        )
    return {"passed": passed, "cohorts": rows, "reasons": reasons, "anchor_scales_stake": False}
