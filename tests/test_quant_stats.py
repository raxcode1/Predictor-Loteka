from __future__ import annotations

import math

import numpy as np
import pytest

from quant import BASELINE, bayes, goodness, montecarlo
from quant.data import from_values
from quant.validation import (
    apply_recalibration,
    brier_multiclass,
    diebold_mariano,
    evaluate,
    fit_recalibration,
    holm,
    make_partition,
    walk_forward,
)
from tests.conftest import markov_chain


# ---------------------------------------------------------------- bayes
def test_beta_binomial_posterior_parameters():
    post = bayes.beta_binomial_posterior(10, 100, bayes.BetaPrior("u", 1, 1))
    assert post["posterior_a"] == 11 and post["posterior_b"] == 91
    assert post["posterior_mean"] == pytest.approx(11 / 102)
    lo, hi = post["credible_interval"]
    assert lo < 11 / 102 < hi
    assert post["prob_above_threshold"] > 0.95  # 10% >> 5.26%


def test_prior_sensitivity_skeptical_is_more_conservative():
    rows = bayes.prior_sensitivity(12, 150)
    uniform, _, skeptical = rows
    assert skeptical["prob_above_threshold"] < uniform["prob_above_threshold"]


def test_beta_binomial_rejects_invalid_counts():
    with pytest.raises(ValueError):
        bayes.beta_binomial_posterior(5, 3, bayes.BetaPrior("u", 1, 1))


def test_bayes_factors_detect_bias_and_dependence(uniform_values, dependent_values):
    flat = np.bincount(uniform_values, minlength=19)
    biased = flat.copy()
    biased[0] += 2000
    assert bayes.log_bayes_factor_frequency(flat) < 0  # favorece la uniforme
    assert bayes.log_bayes_factor_frequency(biased) > 10
    iid_t = goodness.transition_matrix(from_values(uniform_values))
    dep_t = goodness.transition_matrix(from_values(dependent_values))
    assert bayes.log_bayes_factor_markov1(iid_t) < 0
    assert bayes.log_bayes_factor_markov1(dep_t) > 50


def test_dirichlet_frequency_posterior_shapes():
    rows = bayes.dirichlet_frequency_posterior(np.full(19, 100))
    assert len(rows) == 19
    assert sum(r["posterior_mean"] for r in rows) == pytest.approx(1.0)


# ---------------------------------------------------------------- goodness
def test_battery_does_not_reject_iid_uniform(uniform_values):
    result = goodness.run_battery(from_values(uniform_values), seed=1)
    assert result["rejected_after_holm"] == []
    assert len(result["tests"]) == 9
    for test in result["tests"]:
        assert 0.0 <= test["p_value"] <= 1.0
        assert test["p_value_holm"] >= test["p_value"]


def test_battery_detects_anti_repetition():
    values = markov_chain(6000, seed=3, stay_out=1.0)
    result = goodness.run_battery(from_values(values), seed=1)
    assert "binomial_repeticiones_adyacentes" in result["rejected_after_holm"]


def test_ks_pit_is_deterministic_by_seed(uniform_values):
    a = goodness.ks_randomized_pit(uniform_values, seed=5)
    b = goodness.ks_randomized_pit(uniform_values, seed=5)
    assert a == b


def test_chi_square_effect_size_and_df(uniform_values):
    result = goodness.chi_square_uniform(uniform_values)
    assert result["df"] == 18
    assert result["min_expected"] >= 5
    assert result["effect_size"]["cohen_w"] < 0.1


def test_holm_adjustment():
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])


# ---------------------------------------------------------------- validation
def test_uniform_metrics_reference_values():
    y = np.arange(19).repeat(3)
    probs = np.full((y.size, 19), 1 / 19)
    metrics = evaluate(probs, y)
    assert metrics["log_loss"] == pytest.approx(math.log(19))
    assert brier_multiclass(probs, y) == pytest.approx(1 - 1 / 19)
    assert metrics["ece_top_label"] >= 0


def test_partition_is_chronological():
    part = make_partition(100)
    assert (part.train_end, part.validation_end, part.n) == (60, 80, 100)
    with pytest.raises(ValueError):
        make_partition(2)


def test_diebold_mariano_identical_losses():
    losses = np.ones(100)
    assert diebold_mariano(losses, losses)["p_value"] == 1.0


def test_recalibration_shrinks_noise_model_toward_uniform(uniform_values):
    rng = np.random.default_rng(0)
    noisy = rng.dirichlet(np.ones(19), size=uniform_values.size)
    lam = fit_recalibration(noisy, uniform_values)
    assert lam > 0.8
    mixed = apply_recalibration(noisy, lam)
    np.testing.assert_allclose(mixed.sum(axis=1), 1.0)


def test_walk_forward_finds_no_edge_on_iid(uniform_values):
    wf = walk_forward(from_values(uniform_values))
    assert wf["diebold_mariano_calibrated"]["p_value"] > 0.01
    assert wf["partition"]["test"]["size"] == 1200


def test_walk_forward_detects_real_dependence(dependent_values):
    wf = walk_forward(from_values(dependent_values))
    assert wf["selected_model"]["family"] == "markov"
    assert wf["diebold_mariano_calibrated"]["p_value"] < 1e-6
    assert wf["blocks_beating_uniform"] == 5
    assert wf["test_raw"]["top1"] > 0.2


# ---------------------------------------------------------------- monte carlo
def test_monte_carlo_is_deterministic_by_seed():
    a = montecarlo.bankroll(200, 0.01, 18.0, sims=300, seed=9, hit_rate=BASELINE)
    b = montecarlo.bankroll(200, 0.01, 18.0, sims=300, seed=9, hit_rate=BASELINE)
    assert a == b
    assert 0.0 <= a["ruin_probability"] <= 1.0
    assert 0.0 <= a["max_drawdown_median"] <= 1.0


def test_monte_carlo_null_hits_p_value_range():
    result = montecarlo.null_hits(1000, 53, sims=2000, seed=1)
    assert 0.2 < result["p_value"] < 0.8
    assert montecarlo.null_hits(1000, 120, sims=2000, seed=1)["p_value"] < 0.001


def test_monte_carlo_null_log_loss_uniform_predictions():
    probs = np.full((500, 19), 1 / 19)
    result = montecarlo.null_log_loss(probs, observed_improvement=0.0, sims=50, seed=2)
    assert result["null_mean"] == pytest.approx(0.0, abs=1e-12)


def test_bankroll_requires_exactly_one_rate_source():
    with pytest.raises(ValueError):
        montecarlo.bankroll(10, 0.01, 18.0)
    with pytest.raises(ValueError):
        montecarlo.bankroll(10, 0.01, 18.0, hit_rate=0.1, posterior=(1, 1))
