"""Ingeniería de variables sobre el log de supervisión.

Cada columna derivada tiene un significado auditable:

- ``p_max`` / ``p_min`` / ``spread``: análisis de dispersión (Δ = Pmax − Pmin).
- ``primary_model`` / ``primary_models``: qué submodelo emite la probabilidad
  principal de la ronda y qué submodelos la empatan.
- ``zone_m4``: zona de densidad probabilística de M4 (ruido, convergencia,
  borde o trampa estadística).
- ``anchor``: factor acelerador por anclaje en {1, 7, 8}.
- ``group_id`` / ``label_conflict``: detección de registros de predicción
  re-escritos con etiquetas de resultado contradictorias.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import StrategySpec

TOLERANCE = 1e-9

#: Frontera superior de la "trampa estadística" (> 8,0%) según el PDF.
OVERFIT_BOUNDARY = 0.08


def zone_labels(spec: StrategySpec) -> tuple[str, str, str, str]:
    """Etiquetas de las cuatro zonas de probabilidad, derivadas de la spec."""
    low = spec.probability_min * 100.0
    high = spec.probability_max * 100.0
    overfit = OVERFIT_BOUNDARY * 100.0
    return (
        f"<{low:.0f}%",
        f"{low:.0f}-{high:.0f}%",
        f"{high:.0f}-{overfit:.0f}%",
        f">{overfit:.0f}%",
    )


def zone_of(probability: float, spec: StrategySpec) -> str:
    """Clasifica una probabilidad en las zonas del PDF.

    La tabla de zonas del documento usa «< 5,0%» y «5,0% – 7,0%», es decir,
    el borde inferior es inclusivo en la banda de convergencia. El núcleo
    operativo, en cambio, exige 5,0% < P(M4) ≤ 7,0%. Ambas convenciones
    conviven en el motor: esta función alimenta la tabla de zonas y la
    ventana de alertas usa la desigualdad estricta del núcleo.
    """
    noise, convergence, border, trap = zone_labels(spec)
    if probability <= spec.probability_min:
        return noise
    if probability <= spec.probability_max:
        return convergence
    if probability <= OVERFIT_BOUNDARY:
        return border
    return trap


def model_columns(spec: StrategySpec) -> list[str]:
    return [f"p_{model.lower()}" for model in spec.models_in_matrix]


def number_columns(spec: StrategySpec) -> list[str]:
    return [f"n_{model.lower()}" for model in spec.models_in_matrix]


def build_features(frame: pd.DataFrame, spec: StrategySpec) -> pd.DataFrame:
    """Devuelve una copia del `DataFrame` con las columnas derivadas."""
    enriched = frame.copy()
    probabilities = enriched[model_columns(spec)].to_numpy(dtype=float)
    numbers = enriched[number_columns(spec)].to_numpy(dtype=float)

    with np.errstate(invalid="ignore"):
        p_max = np.nanmax(probabilities, axis=1)
        p_min = np.nanmin(probabilities, axis=1)
    enriched["p_max"] = p_max
    enriched["p_min"] = p_min
    enriched["spread"] = p_max - p_min

    argmax = np.nanargmax(probabilities, axis=1)
    enriched["primary_model"] = [spec.models_in_matrix[index] for index in argmax]
    enriched["primary_number"] = numbers[np.arange(len(enriched)), argmax]

    model_key = spec.model.lower()
    p_model = enriched[f"p_{model_key}"].to_numpy(dtype=float)
    n_model = enriched[f"n_{model_key}"].to_numpy(dtype=float)
    enriched["model_probability"] = p_model
    enriched["model_number"] = n_model
    enriched["m4_tied_max"] = np.isclose(p_model, p_max, atol=TOLERANCE)
    enriched["m4_sole_max"] = enriched["m4_tied_max"] & (
        np.sum(np.isclose(probabilities, p_max[:, None], atol=TOLERANCE), axis=1) == 1
    )

    enriched["zone_model"] = [
        zone_of(value, spec) for value in enriched["model_probability"].to_numpy(dtype=float)
    ]
    enriched["in_window"] = (p_model > spec.probability_min) & (p_model <= spec.probability_max)
    enriched["spread_ok"] = enriched["spread"] <= spec.max_spread
    enriched["anchor"] = enriched["last_number"].isin(spec.anchor_numbers)
    enriched["hit_model"] = n_model == enriched["result"].to_numpy(dtype=float)

    if spec.primary_mode == "any":
        primary_ok = pd.Series(True, index=enriched.index)
    elif spec.primary_mode == "tie":
        primary_ok = enriched["m4_tied_max"]
    else:
        primary_ok = enriched["m4_sole_max"]
    enriched["primary_ok"] = primary_ok
    enriched["alert"] = enriched["in_window"] & enriched["spread_ok"] & primary_ok

    enriched["hit_primary"] = enriched["primary_number"] == enriched["result"].to_numpy(dtype=float)
    enriched["signature"] = _signatures(enriched, spec)
    enriched["group_id"] = (
        enriched["draw_id"].astype("string") + "|" + enriched["signature"]
    )
    group_stats = enriched.groupby("group_id")["result"].nunique()
    enriched["label_conflict"] = enriched["group_id"].map(group_stats) > 1
    enriched["group_size"] = enriched["group_id"].map(enriched.groupby("group_id").size())
    return enriched


def _signatures(frame: pd.DataFrame, spec: StrategySpec) -> pd.Series:
    columns = ["last_number"] + number_columns(spec) + model_columns(spec)
    return frame[columns].fillna("NA").astype(str).agg("|".join, axis=1)


def collapse_conflicts(frame: pd.DataFrame) -> pd.DataFrame:
    """Elimina filas cuyo grupo de predicción tiene etiquetas contradictorias.

    Un mismo registro de predicción re-escrito con varios resultados no tiene
    etiqueta verificable: se descarta en lugar de contarlo como acierto o
    fallo, porque ambas lecturas sesgan la estimación.
    """
    clean = frame[~frame["label_conflict"]].copy()
    return clean.drop_duplicates(subset="group_id", keep="first").reset_index(drop=True)
