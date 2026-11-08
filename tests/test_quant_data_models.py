from __future__ import annotations

import numpy as np
import pytest

from quant.data import DataValidationError, from_frame, from_rows, from_values
from quant.models import ModelSpec, PredictionCache, forecast_next, prequential_counts
from tests.conftest import rows_from_values


# ---------------------------------------------------------------- data
def test_from_rows_orders_chronologically_and_hashes():
    rows = rows_from_values([3, 5, 7], start=10)
    dataset = from_rows(rows)
    assert dataset.draw_ids.tolist() == [10, 11, 12]
    assert dataset.values.tolist() == [3, 5, 7]
    # El hash depende del contenido, no del orden de las filas.
    assert from_rows(list(reversed(rows))).sha256 == dataset.sha256


def test_from_rows_drops_invalid_and_counts_conflicts():
    rows = [
        {"No_Sorteo": "No. 5", "Numero": "04"},
        {"No_Sorteo": "No. 5", "Numero": "09"},  # contradictorio
        {"No_Sorteo": "No. 6", "Numero": "19"},  # fuera de rango
        {"No_Sorteo": "", "Numero": "01"},
        {"No_Sorteo": "No. 8", "Numero": "x"},
        {"No_Sorteo": "No. 9", "Numero": "18"},
    ]
    dataset = from_rows(rows)
    assert dataset.values.tolist() == [4, 18]
    assert dataset.conflicting_duplicates == 1
    assert dataset.dropped_rows == 3
    assert dataset.gap_count == 3
    assert any("ausentes" in issue for issue in dataset.issues)


def test_consecutive_mask_and_contiguous_suffix():
    rows = [{"No_Sorteo": f"No. {d}", "Numero": "1"} for d in (1, 2, 3, 7, 8, 9, 10)]
    dataset = from_rows(rows)
    assert dataset.consecutive.tolist() == [False, True, True, False, True, True, True]
    assert dataset.longest_contiguous_suffix() == (3, 7)


def test_from_frame_requires_columns():
    import pandas as pd

    with pytest.raises(DataValidationError):
        from_frame(pd.DataFrame({"Fecha": ["x"]}))


def test_from_values_rejects_out_of_range():
    with pytest.raises(DataValidationError):
        from_values([0, 19])


# ---------------------------------------------------------------- models
def test_prequential_counts_have_no_look_ahead(dependent_values):
    dataset = from_values(dependent_values[:500])
    altered = dependent_values[:500].copy()
    altered[300:] = (altered[300:] + 7) % 19
    base = PredictionCache(dataset).matrix(ModelSpec("markov", 2, 1.0))
    other = PredictionCache(from_values(altered)).matrix(ModelSpec("markov", 2, 1.0))
    # Las predicciones hasta t=300 (inclusive) sólo dependen de x[<300].
    np.testing.assert_allclose(base[:301], other[:301])
    assert not np.allclose(base[301:], other[301:])


def test_markov1_matches_hand_computation():
    values = np.array([0, 1, 0, 1, 0])
    counts, valid = prequential_counts(values, from_values(values).consecutive, order=1)
    assert valid.tolist() == [False, True, True, True, True]
    # En t=4 el contexto es 1 y antes se observó 1->0 una vez (t=2).
    assert counts[4, 0] == 1 and counts[4].sum() == 1
    probs = PredictionCache(from_values(values)).matrix(ModelSpec("markov", 1, 1.0))
    assert probs[4, 0] == pytest.approx(2 / 20)
    assert probs[0].tolist() == pytest.approx([1 / 19] * 19)


def test_gaps_invalidate_markov_context():
    rows = [{"No_Sorteo": f"No. {d}", "Numero": str(v)} for d, v in ((1, 2), (2, 3), (5, 2), (6, 3))]
    dataset = from_rows(rows)
    counts, valid = prequential_counts(dataset.values, dataset.consecutive, order=1)
    # El par (3 -> 2) cruza un hueco y no es una transición.
    assert valid.tolist() == [False, True, False, True]
    assert counts[3, 3] == 1  # sólo se aprendió 2 -> 3


def test_window_forgets_old_counts():
    values = np.array([1] * 50 + [2] * 50)
    probs = PredictionCache(from_values(values)).matrix(ModelSpec("dirichlet", 0, 1.0, window=10))
    # Ventana de 10: sólo cuentan diez «2»; los «1» antiguos se olvidaron.
    assert probs[-1, 2] == pytest.approx(11 / 29)
    assert probs[-1, 1] == pytest.approx(1 / 29)


def test_forecast_next_equals_next_prequential_row(dependent_values):
    values = dependent_values[:800]
    spec = ModelSpec("markov", 1, 10.0)
    extended = from_values(np.append(values, 0))
    expected = PredictionCache(extended).matrix(spec)[-1]
    np.testing.assert_allclose(forecast_next(from_values(values), spec), expected)


def test_model_spec_validation():
    with pytest.raises(ValueError):
        ModelSpec("markov", 4)
    with pytest.raises(ValueError):
        ModelSpec("dirichlet", 1)
    with pytest.raises(ValueError):
        ModelSpec("markov", 1, alpha=0)
