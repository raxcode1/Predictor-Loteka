"""Ensamblado del informe cuantitativo reproducible."""

from __future__ import annotations

import math
import threading
import time
from datetime import UTC, datetime
from typing import Any

import numpy as np

from . import (
    BASELINE,
    OUTPUT_MODULUS,
    PIPELINE_VERSION,
    bayes,
    goodness,
    montecarlo,
    prng,
)
from .data import Dataset
from .decision import DecisionConfig, RiskLimits, decide
from .logs import get_logger
from .models import ModelSpec, PredictionCache, default_grid, forecast_next
from .validation import apply_recalibration, walk_forward

DEFAULT_SEED = 20261108
MIN_ROWS = 500

log = get_logger("quant.report")


def build_report(
    dataset: Dataset,
    seed: int = DEFAULT_SEED,
    mc_sims: int = 1000,
    run_prng_solver: bool = False,
    prng_kwargs: dict[str, Any] | None = None,
    net_multiplier: float = 18.0,
    mode: str | None = None,
) -> dict[str, Any]:
    """Informe completo. Lanza ValueError si el histórico es insuficiente."""
    started = time.monotonic()
    if len(dataset) < MIN_ROWS:
        raise ValueError(f"Se requieren al menos {MIN_ROWS} sorteos para el informe (hay {len(dataset)}).")

    grid = default_grid()
    cache = PredictionCache(dataset)
    wf = walk_forward(dataset, grid, cache=cache)
    battery = goodness.run_battery(dataset, seed=seed)

    counts = np.bincount(dataset.values, minlength=OUTPUT_MODULUS)
    transitions = goodness.transition_matrix(dataset, 1)
    raw = wf["test_raw"]
    bayesian = {
        "frequency_posterior_alpha1": bayes.dirichlet_frequency_posterior(counts, 1.0),
        "bayes_factors": bayes.bayes_factor_sensitivity(counts, transitions),
        "top1_hit_rate_prior_sensitivity": bayes.prior_sensitivity(raw["top1_hits"], raw["n"], BASELINE),
    }

    selected = ModelSpec(
        wf["selected_model"]["family"],
        wf["selected_model"]["order"],
        wf["selected_model"]["alpha"],
        wf["selected_model"]["window"],
    )
    test_slice = slice(wf["partition"]["test"]["start_index"], wf["partition"]["test"]["end_index"])
    test_probs = apply_recalibration(cache.matrix(selected)[test_slice], wf["recalibration_lambda"])
    improvement = math.log(OUTPUT_MODULUS) - wf["test_calibrated"]["log_loss"]
    skeptical = bayes.standard_priors(BASELINE)[2]
    post_a = skeptical.a + raw["top1_hits"]
    post_b = skeptical.b + raw["n"] - raw["top1_hits"]
    simulation = {
        "null_log_loss": montecarlo.null_log_loss(test_probs, improvement, sims=mc_sims, seed=seed),
        "null_top1_hits": montecarlo.null_hits(raw["n"], raw["top1_hits"], sims=10 * mc_sims, seed=seed + 1),
        "bankroll_null_1pct": montecarlo.bankroll(
            n_bets=1000, stake_fraction=0.01, net_multiplier=net_multiplier,
            sims=mc_sims, seed=seed + 2, hit_rate=BASELINE,
        ),
        "bankroll_posterior_1pct": montecarlo.bankroll(
            n_bets=1000, stake_fraction=0.01, net_multiplier=net_multiplier,
            sims=mc_sims, seed=seed + 3, posterior=(post_a, post_b),
        ),
        "note": "Simulación de variabilidad bajo supuestos; no es evidencia causal.",
    }

    prng_report = prng.identify_public(dataset, run_solver=run_prng_solver, **(prng_kwargs or {}))

    forecast = forecast_next(dataset, selected)
    forecast = apply_recalibration(forecast, wf["recalibration_lambda"])
    order = np.argsort(-forecast, kind="stable")[:5]

    report: dict[str, Any] = {
        "pipeline_version": PIPELINE_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "seed": seed,
        "null_hypothesis": "resultados i.i.d. uniformes en 0..18 (P = 1/19)",
        "baseline": BASELINE,
        "break_even": 1.0 / (1.0 + net_multiplier),
        "net_multiplier": net_multiplier,
        "dataset": dataset.summary(),
        "hyperparameter_grid": [spec.as_dict() for spec in grid],
        "walk_forward": wf,
        "goodness_of_fit": battery,
        "bayesian": bayesian,
        "monte_carlo": simulation,
        "prng": prng_report,
        "descriptive_forecast": {
            "model": selected.name,
            "confidence": "baja",
            "note": "Ranking descriptivo; no es una predicción determinista ni una señal de apuesta.",
            "top5": [{"number": int(i), "probability": float(forecast[i])} for i in order],
        },
    }
    report["decision"] = decide(
        report, DecisionConfig(break_even=report["break_even"]), RiskLimits(), mode=mode, net_multiplier=net_multiplier
    )
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    log.info(
        "quant_report_built",
        extra={
            "rows": len(dataset),
            "sha256": dataset.sha256,
            "selected_model": selected.name,
            "status": report["decision"]["status"],
            "elapsed_seconds": report["elapsed_seconds"],
        },
    )
    return report


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    """Resumen compacto para el dashboard (sin matrices ni candidatos)."""
    wf = report["walk_forward"]
    battery = report["goodness_of_fit"]
    return {
        "pipeline_version": report["pipeline_version"],
        "generated_at": report["generated_at"],
        "seed": report["seed"],
        "null_hypothesis": report["null_hypothesis"],
        "dataset": report["dataset"],
        "partition": wf["partition"],
        "selected_model": wf["selected_model"],
        "recalibration_lambda": wf["recalibration_lambda"],
        "test": {
            key: wf["test_calibrated"][key]
            for key in (
                "n", "log_loss", "log_loss_uniform", "brier", "brier_uniform", "top1", "top3", "top5",
                "baseline_top1", "baseline_top5", "ece_top_label", "ece_one_vs_rest",
                "brier_decomposition_one_vs_rest", "reliability_top_label",
            )
        },
        "diebold_mariano": wf["diebold_mariano_calibrated"],
        "top1_binomial_test": wf["top1_binomial_test"],
        "walk_forward_blocks": wf["walk_forward_blocks"],
        "goodness_of_fit": [
            {k: t.get(k) for k in ("name", "statistic", "df", "p_value", "p_value_holm", "p_value_bh", "reject_holm", "effect_size")}
            for t in battery["tests"]
        ],
        "goodness_interpretation": battery["interpretation"],
        "entropy": battery["entropy"],
        "bayes_factors": report["bayesian"]["bayes_factors"],
        "monte_carlo": {
            "null_log_loss_p_value": report["monte_carlo"]["null_log_loss"]["p_value"],
            "null_top1_p_value": report["monte_carlo"]["null_top1_hits"]["p_value"],
            "bankroll_null_1pct": report["monte_carlo"]["bankroll_null_1pct"],
            "bankroll_posterior_1pct": report["monte_carlo"]["bankroll_posterior_1pct"],
        },
        "prng": {
            "verdict": report["prng"]["verdict"],
            "block": report["prng"]["block"],
            "hypotheses": [
                {"generator": h["generator"], "status": h["status"], "reason": h.get("reason")}
                for h in report["prng"]["hypotheses"]
            ],
        },
        "descriptive_forecast": report["descriptive_forecast"],
        "decision": report["decision"],
        "elapsed_seconds": report["elapsed_seconds"],
    }


_cache_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}


def cached_summary(dataset: Dataset, **kwargs: Any) -> dict[str, Any]:
    """Resumen cacheado por hash del dataset (una entrada viva)."""
    key = dataset.sha256
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    summary = summarize(build_report(dataset, **kwargs))
    with _cache_lock:
        _cache.clear()
        _cache[key] = summary
    return summary


def to_markdown(report: dict[str, Any]) -> str:
    wf = report["walk_forward"]
    d = report["dataset"]
    dec = report["decision"]
    lines = [
        "# Informe cuantitativo Ruleta Express",
        "",
        f"- Pipeline: `{report['pipeline_version']}` · generado {report['generated_at']} · semilla {report['seed']}",
        f"- Dataset SHA-256: `{d['sha256']}`",
        (
            f"- Periodo: sorteos {d['first_draw']}–{d['last_draw']} ({d['first_date']} → {d['last_date']}), "
            f"{d['rows']} filas, {d['missing_draws']} sorteos ausentes, "
            f"{d['consecutive_transitions']} transiciones consecutivas"
        ),
        f"- Hipótesis nula: {report['null_hypothesis']}",
        "",
        f"## Decisión: **{dec['status']}** (modo {dec['trading_mode']}, señales permitidas: {dec['signals_allowed']})",
        "",
    ]
    for check in dec["checks"]:
        mark = "✔" if check["passed"] else "✘"
        lines.append(f"- {mark} `{check['name']}`: {check['value']} (umbral {check['threshold']})")
    lines += ["", "## Particiones (cronológicas, bloqueadas)", ""]
    for name, part in wf["partition"].items():
        lines.append(f"- {name}: índices {part['start_index']}–{part['end_index']} ({part['size']}), "
                     f"sorteos {part.get('first_draw')}–{part.get('last_draw')}")
    lines += [
        "",
        "## Selección de modelo (sólo validation)",
        "",
        "| modelo | log-loss validation |",
        "|---|---|",
    ]
    for cand in sorted(wf["candidates"], key=lambda c: c["validation_log_loss"]):
        lines.append(f"| {cand['model']['name']} | {cand['validation_log_loss']:.6f} |")
    t = wf["test_calibrated"]
    dm = wf["diebold_mariano_calibrated"]
    lines += [
        "",
        f"Seleccionado: **{wf['selected_model']['name']}**, recalibración λ = {wf['recalibration_lambda']:.4f}",
        "",
        "## Test bloqueado (fuera de muestra)",
        "",
        f"- n = {t['n']}; log-loss {t['log_loss']:.6f} vs uniforme {t['log_loss_uniform']:.6f}",
        f"- Brier {t['brier']:.6f} vs uniforme {t['brier_uniform']:.6f}",
        f"- top-1 {t['top1']:.4f} (base {t['baseline_top1']:.4f}), top-5 {t['top5']:.4f} (base {t['baseline_top5']:.4f})",
        f"- ECE top-label {t['ece_top_label']:.4f}; ECE one-vs-rest {t['ece_one_vs_rest']:.5f}",
        f"- Diebold-Mariano: mejora media {dm['mean_improvement']:.6f} nats, z = {dm['statistic']:.3f}, p = {dm['p_value']:.4f}",
        f"- Binomial top-1: p = {wf['top1_binomial_test']['p_value']:.4f}",
        "",
        "### Walk-forward por bloques del test",
        "",
        "| sorteos | n | log-loss | mejora vs 1/19 | top-1 |",
        "|---|---|---|---|---|",
    ]
    for b in wf["walk_forward_blocks"]:
        lines.append(f"| {b['first_draw']}–{b['last_draw']} | {b['n']} | {b['log_loss']:.5f} | "
                     f"{b['log_loss_improvement_vs_uniform']:+.5f} | {b['top1']:.4f} |")
    lines += ["", "## Bondad de ajuste e independencia (Holm α=0.01)", "",
              "| test | estadístico | gl | p | p Holm | p BH | rechaza |", "|---|---|---|---|---|---|---|"]
    for test in report["goodness_of_fit"]["tests"]:
        lines.append(
            f"| {test['name']} | {test['statistic']:.4f} | {test['df'] if test['df'] is not None else '-'} | "
            f"{test['p_value']:.4g} | {test['p_value_holm']:.4g} | {test['p_value_bh']:.4g} | "
            f"{'sí' if test['reject_holm'] else 'no'} |"
        )
    lines += ["", report["goodness_of_fit"]["interpretation"], "", "## Bayes", ""]
    for bf in report["bayesian"]["bayes_factors"]:
        lines.append(f"- α={bf['alpha']}: log BF(frecuencias vs uniforme) = {bf['log_bf_frequency_vs_uniform']:.3f}; "
                     f"log BF(Markov-1 vs iid) = {bf['log_bf_markov1_vs_iid']:.3f}")
    for post in report["bayesian"]["top1_hit_rate_prior_sensitivity"]:
        lo, hi = post["credible_interval"]
        lines.append(f"- top-1 test, prior {post['prior']}: media {post['posterior_mean']:.4f}, "
                     f"IC95 [{lo:.4f}, {hi:.4f}], P(>1/19) = {post['prob_above_threshold']:.3f}")
    mc = report["monte_carlo"]
    lines += [
        "",
        "## Monte Carlo",
        "",
        f"- Nula, mejora de log-loss: p = {mc['null_log_loss']['p_value']:.4f} ({mc['null_log_loss']['sims']} sims)",
        f"- Nula, aciertos top-1: p = {mc['null_top1_hits']['p_value']:.4f}",
        (
            f"- Bankroll 1%/ronda bajo nula: ROI mediano {mc['bankroll_null_1pct']['roi_q50']:+.2%}, "
            f"drawdown máx. q95 {mc['bankroll_null_1pct']['max_drawdown_q95']:.2%}, "
            f"P(ruina 50%) {mc['bankroll_null_1pct']['ruin_probability']:.2%}"
        ),
        (
            "- Bankroll 1%/ronda bajo posterior escéptico: ROI mediano "
            f"{mc['bankroll_posterior_1pct']['roi_q50']:+.2%}, "
            f"P(beneficio) {mc['bankroll_posterior_1pct']['prob_profit']:.2%}"
        ),
        "",
        "## PRNG",
        "",
        f"Veredicto: **{report['prng']['verdict']}** sobre el bloque contiguo {report['prng']['block']}.",
        "",
    ]
    for h in report["prng"]["hypotheses"]:
        lines.append(f"- {h['generator']}: {h['status']} — {h.get('reason', '')}")
        if h.get("hypotheses_tested"):
            lines.append(f"  - hipótesis probadas {h['hypotheses_tested']}, resultados {h.get('solver_outcomes')}, "
                         f"cota de azar Bonferroni {h.get('bonferroni_chance_probability'):.3g}")
    lines += ["", f"> {report['prng']['disclaimer']}", "", "## Limitaciones", "",
              "- Sólo se observa el número publicado; el mapeo estado→número es desconocido.",
              "- El break-even asume pago neto 18:1; con un pago real menor la exigencia es mayor.",
              "- Los huecos de numeración se excluyen de las transiciones; no se imputan.",
              "- Ningún resultado de este informe implica que el siguiente sorteo sea predecible.", ""]
    return "\n".join(lines)
