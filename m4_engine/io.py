"""Lectura del CSV de supervisión y normalización de registros en tiempo real.

El CSV de supervisión tiene un cabecera poco convencional: cada submodelo ocupa
dos columnas (`M1`, `prob.`, `M2`, `prob.`, ...) y los nombres de columna se
repiten. El parser es dirigido por la cabecera, no por posiciones fijas, para
que una reordenación de columnas no rompa la carga silenciosamente.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .config import MODEL_ORDER

PROBABILITY_TOKENS = {"prob", "probs", "probabilidad", "p", "probability"}
NUMBER_TOKENS = {"numero", "n", "number"}
LAST_NUMBER_TOKENS = {"ultimo numero", "ultimo", "last number", "last"}
RESULT_TOKENS = {"resultado", "result", "actual"}
DRAW_TOKENS = {"no. sorteo", "no sorteo", "sorteo", "draw"}
INDEX_TOKENS = {"#", "id", "indice"}


def _normalize_token(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return text.strip().lower().rstrip(".").strip()


def parse_header(header: list[str]) -> dict[str, Any]:
    """Interpreta la cabecera del CSV de supervisión.

    Devuelve un diccionario con:
        ``positions``  nombre lógico -> índice de columna
        ``probability_of`` modelo -> índice de su columna de probabilidad
        ``number_of`` modelo -> índice de su columna de número
    """
    tokens = [_normalize_token(name) for name in header]
    positions: dict[str, Any] = {}
    number_of: dict[str, int] = {}
    probability_of: dict[str, int] = {}

    for index, token in enumerate(tokens):
        if token in INDEX_TOKENS and "row_id" not in positions:
            positions["row_id"] = index
            continue
        if token in DRAW_TOKENS and "draw_id" not in positions:
            positions["draw_id"] = index
            continue
        if token in LAST_NUMBER_TOKENS and "last_number" not in positions:
            positions["last_number"] = index
            continue
        if token in RESULT_TOKENS and "result" not in positions:
            positions["result"] = index
            continue
        if token in PROBABILITY_TOKENS:
            continue
        if token in NUMBER_TOKENS:
            continue
        if token.upper() in MODEL_ORDER:
            number_of[token.upper()] = index
            if index + 1 < len(header) and tokens[index + 1] in PROBABILITY_TOKENS:
                probability_of[token.upper()] = index + 1
            continue

    missing = [model for model in MODEL_ORDER if model not in number_of]
    if missing:
        raise ValueError(
            f"La cabecera no expone los submodelos {missing}. "
            f"Columnas recibidas: {list(header)}"
        )
    missing_probabilities = [model for model in MODEL_ORDER if model not in probability_of]
    if missing_probabilities:
        raise ValueError(
            f"La cabecera no expone la columna de probabilidad de {missing_probabilities}."
        )
    for required in ("row_id", "draw_id", "last_number", "result"):
        if required not in positions:
            raise ValueError(f"La cabecera no expone la columna obligatoria {required!r}.")

    positions["number_of"] = number_of
    positions["probability_of"] = probability_of
    return positions


def _to_float(series: pd.Series) -> pd.Series:
    text = (
        series.astype("string")
        .str.strip()
        .str.replace("%", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    return pd.to_numeric(text, errors="coerce")


def load_supervision_csv(path: str | Path) -> pd.DataFrame:
    """Carga el CSV de supervisión en un `DataFrame` normalizado.

    Columnas resultantes:
        ``row_id``, ``draw_id``, ``last_number``, ``result``,
        ``n_<modelo>`` y ``p_<modelo>`` (probabilidad en fracción 0..1).
    """
    path = Path(path)
    raw = pd.read_csv(path, header=None, dtype=str, encoding="utf-8-sig")
    raw = raw.dropna(how="all")
    if raw.empty:
        raise ValueError(f"El archivo {path} no contiene filas de datos.")

    header = [str(value) for value in raw.iloc[0].tolist()]
    positions = parse_header(header)
    body = raw.iloc[1:].reset_index(drop=True)

    frame = pd.DataFrame()
    frame["row_id"] = pd.to_numeric(body.iloc[:, positions["row_id"]], errors="coerce")
    frame["draw_id"] = pd.to_numeric(body.iloc[:, positions["draw_id"]], errors="coerce")
    frame["last_number"] = pd.to_numeric(body.iloc[:, positions["last_number"]], errors="coerce")
    frame["result"] = pd.to_numeric(body.iloc[:, positions["result"]], errors="coerce")

    for model in MODEL_ORDER:
        number_index = positions["number_of"][model]
        probability_index = positions["probability_of"][model]
        key = model.lower()
        frame[f"n_{key}"] = pd.to_numeric(body.iloc[:, number_index], errors="coerce")
        frame[f"p_{key}"] = _to_float(body.iloc[:, probability_index]) / 100.0

    frame["row_id"] = frame["row_id"].astype("Int64")
    frame["draw_id"] = frame["draw_id"].astype("Int64")
    for column in ("last_number", "result"):
        frame[column] = frame[column].astype("Int64")
    for model in MODEL_ORDER:
        key = model.lower()
        frame[f"n_{key}"] = frame[f"n_{key}"].astype("Int64")
        frame[f"p_{key}"] = frame[f"p_{key}"].astype(float)

    frame.insert(0, "source_row", range(2, len(frame) + 2))
    return frame


def _normalize_key(key: Any) -> str:
    return str(key).strip().lower().replace(".", "_").replace(" ", "_").replace("%", "")


def _lookup_ci(container: Mapping[str, Any] | None, candidates: tuple[str, ...]) -> Any:
    """Busca una clave ignorando mayúsculas, puntos y espacios."""
    if not isinstance(container, Mapping):
        return None
    normalized = {_normalize_key(key): value for key, value in container.items()}
    for candidate in candidates:
        value = normalized.get(_normalize_key(candidate))
        if value is not None:
            return value
    return None


def _probability_lookup(record: Mapping[str, Any], model: str) -> float | None:
    key = model.lower()
    candidates = (
        f"p_{key}",
        f"prob_{key}",
        f"{key}_prob",
        f"prob {key}",
    )
    value = _lookup_ci(record, candidates)
    if value is not None:
        return _coerce_probability(value)
    for container in (record.get("probabilities"), record.get("probs")):
        value = _lookup_ci(container, (model, key, f"p_{key}", f"prob_{key}"))
        if value is not None:
            return _coerce_probability(value)
    return None


def _number_lookup(record: Mapping[str, Any], model: str) -> int | None:
    key = model.lower()
    candidates = (f"n_{key}", model, key, f"num_{key}", f"numero_{key}")
    value = _lookup_ci(record, candidates)
    if value is not None:
        return int(float(value))
    for container in (record.get("numbers"), record.get("numeros")):
        value = _lookup_ci(container, (model, key, f"n_{key}"))
        if value is not None:
            return int(float(value))
    return None


def _coerce_probability(value: Any) -> float:
    if isinstance(value, str):
        text = value.strip().replace("%", "").replace(",", ".")
        number = float(text)
        return number / 100.0 if "%" in value else number
    number = float(value)
    return number / 100.0 if number > 1.0 else number


def normalize_record(record: Mapping[str, Any] | pd.Series) -> dict[str, Any]:
    """Normaliza un registro de ronda (CSV, JSON o Serie) a un diccionario estable.

    Acepta varias formas de entrada:

    - ``{"M1": 3, "M4": 7, "p_M1": 0.09, ...}``
    - ``{"numbers": {...}, "probabilities": {...}}``
    - Una fila del `DataFrame` de supervisión.
    """
    if isinstance(record, pd.Series):
        record = record.to_dict()
    if not isinstance(record, Mapping):
        raise TypeError("El registro debe ser un mapping o una Serie de pandas.")

    def _lookup(tokens: tuple[str, ...], *containers: Any) -> Any:
        for container in containers:
            value = _lookup_ci(container, tokens)
            if value is not None:
                return value
        return None

    numbers: dict[str, int] = {}
    probabilities: dict[str, float] = {}
    for model in MODEL_ORDER:
        number = _number_lookup(record, model)
        probability = _probability_lookup(record, model)
        if number is not None:
            numbers[model] = number
        if probability is not None:
            probabilities[model] = probability

    last_number = _lookup(
        ("last_number", "ultimo_numero", "último número", "último numero", "last"),
        record,
    )
    result = _lookup(("result", "resultado"), record)
    draw_id = _lookup(("draw_id", "no. sorteo", "sorteo", "draw"), record)
    row_id = _lookup(("row_id", "id", "#"), record)

    normalized: dict[str, Any] = {
        "row_id": int(float(row_id)) if row_id is not None else None,
        "draw_id": int(float(draw_id)) if draw_id is not None else None,
        "last_number": int(float(last_number)) if last_number is not None else None,
        "result": int(float(result)) if result is not None else None,
        "numbers": numbers,
        "probabilities": probabilities,
    }
    for model, value in numbers.items():
        normalized[f"n_{model.lower()}"] = value
    for model, value in probabilities.items():
        normalized[f"p_{model.lower()}"] = value
    return normalized
