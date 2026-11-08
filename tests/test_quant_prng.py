from __future__ import annotations

import random

import numpy as np
import pytest

from quant import prng
from quant.data import from_values


def test_temper_untemper_roundtrip():
    rng = random.Random(1)
    for _ in range(2000):
        y = rng.getrandbits(32)
        assert prng.untemper(prng.temper(y)) == y


def test_mt19937_reference_matches_known_vector():
    # Primera salida de genrand_int32 con init_genrand(5489) (vector de referencia).
    assert prng.MT19937(5489).next_u32() == 3499211612


def test_mt19937_recovery_positive_control_with_python_random():
    rng = random.Random(20261108)  # CPython usa MT19937
    outputs = [rng.getrandbits(32) for _ in range(624 + 100)]
    result = prng.mt19937_recover(outputs, holdout=100)
    assert result["status"] == prng.STATUS_RECOVERED
    assert result["holdout_matches"] == 100


def test_mt19937_recovery_negative_control():
    rng = random.Random(3)
    outputs = [rng.getrandbits(32) for _ in range(624)]
    outputs += [random.Random(4).getrandbits(32) for _ in range(50)]
    result = prng.mt19937_recover(outputs, holdout=50)
    assert result["status"] == prng.STATUS_NOT_RECOVERED


def test_mt19937_requires_enough_outputs():
    assert prng.mt19937_recover([1, 2, 3])["status"] == prng.STATUS_INCONCLUSIVE


def test_lcg_crack_full_recovers_parameters():
    m, a, c = 2**31 - 1, 48271, 12345
    states = [42]
    for _ in range(12):
        states.append((a * states[-1] + c) % m)
    params = prng.lcg_crack_full(states)
    assert params == {"modulus": m, "multiplier": a, "increment": c}


def test_lcg_crack_full_rejects_short_input():
    with pytest.raises(ValueError):
        prng.lcg_crack_full([1, 2, 3])


@pytest.mark.parametrize("mapping", ["mod", "range"])
def test_lcg_catalog_positive_control(mapping):
    spec = prng.LCGSpec("glibc_type0", 31, 1103515245, 12345, (16, 17))
    values = prng.lcg_sequence(spec, seed=987654321, count=40, shift=16, mapping=mapping)
    result = prng.lcg_catalog_search(
        values, fit_size=24, holdout_size=16, catalog=[spec], mappings=[mapping], max_seconds=120,
    )
    assert result["status"] == prng.STATUS_RECOVERED, result
    hit = next(c for c in result["candidates"] if c["shift"] == 16)
    assert hit["exact_holdout"] and hit["method"] == "exhaustive"
    assert 987654321 in hit["seeds"]
    assert result["bonferroni_chance_probability"] < prng.CHANCE_THRESHOLD


def test_lcg_msvc_positive_control_with_masked_output():
    spec = prng.LCG_CATALOG[2]
    values = prng.lcg_sequence(spec, seed=31337, count=40, shift=16, mapping="mod")
    result = prng.lcg_catalog_search(values, catalog=[spec], mappings=["mod"], max_seconds=120)
    assert result["status"] == prng.STATUS_RECOVERED
    # El bit 31 no se observa: hay dos semillas equivalentes que predicen lo mismo.
    assert result["candidates"][0]["predictions_agree"] is True


def test_lcg_catalog_negative_control_is_definitive_unsat():
    values = np.random.default_rng(1).integers(0, 19, size=40).tolist()
    spec = prng.LCGSpec("glibc_type0", 31, 1103515245, 12345, (16,))
    result = prng.lcg_catalog_search(values, catalog=[spec], mappings=["mod"], max_seconds=120)
    assert result["status"] == prng.STATUS_NOT_RECOVERED
    assert result["solver_outcomes"]["unsat"] == 1


def test_lcg_smt_unknown_is_not_reported_as_recovered():
    spec = prng.LCGSpec("java.util.Random", 48, 25214903917, 11, (17,))
    values = prng.lcg_sequence(spec, seed=123456789012, count=40, shift=17, mapping="mod")
    result = prng.lcg_catalog_search(values, catalog=[spec], mappings=["mod"], timeout_ms=1000)
    assert result["status"] in (prng.STATUS_INCONCLUSIVE, prng.STATUS_RECOVERED)
    if result["status"] == prng.STATUS_INCONCLUSIVE:
        assert result["solver_outcomes"]["unknown"] == 1


def test_lcg_catalog_requires_enough_values():
    assert prng.lcg_catalog_search([1] * 5)["status"] == prng.STATUS_INCONCLUSIVE


def test_lcg_catalog_respects_query_budget():
    values = np.random.default_rng(2).integers(0, 19, size=40).tolist()
    result = prng.lcg_catalog_search(values, max_queries=2, max_seconds=120)
    assert result["hypotheses_tested"] == 2
    assert result["status"] != prng.STATUS_RECOVERED


def test_identify_public_without_solver_is_inconclusive():
    dataset = from_values(np.random.default_rng(0).integers(0, 19, size=100))
    report = prng.identify_public(dataset, run_solver=False)
    assert report["recovered"] is False
    assert report["verdict"] == prng.STATUS_INCONCLUSIVE
    names = {h["generator"] for h in report["hypotheses"]}
    assert {"MT19937", "LCG (catálogo conocido)", "CSPRNG / fuente física"} <= names


def test_map_output_rejects_unknown_mapping():
    with pytest.raises(ValueError):
        prng.map_output(5, 8, "xor")
