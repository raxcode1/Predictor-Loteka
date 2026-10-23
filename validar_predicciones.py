from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

RESULT_COLUMNS = ("Resultado", "Result", "Estado")
WINNER_COLUMNS = ("Numero", "Número", "Ganador")
MARKOV_COLUMNS = ("Markov X", "Markov_X")
PROBABILITY_COLUMNS = ("ph1", "p4", "p2")
MARKOV_FIELD = "m1"
PENDING = "pendiente"


@dataclass(frozen=True)
class Play:
    row_number: int
    last_winner: int | None
    markov_prediction: int | None
    ph1: float
    p4: float
    p2: float


def _normalize(value: object) -> str:
    return str(value).strip().replace("\ufeff", "").casefold()


def _find_column(df: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    lookup = {_normalize(column): column for column in df.columns}
    for name in names:
        match = lookup.get(_normalize(name))
        if match is not None:
            return match
    return None


def _markov_columns(df: pd.DataFrame) -> list[str]:
    explicit = _find_column(df, MARKOV_COLUMNS)
    if explicit is not None:
        return [explicit]
    return [
        column
        for column in df.columns
        if _normalize(column).startswith("markov")
    ]


def read_predictions(path: str | Path) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            frame = pd.read_csv(path, encoding=encoding, sep=None, engine="python")
            frame.columns = [str(column).strip().replace("\ufeff", "") for column in frame.columns]
            return frame.reset_index(drop=True)
        except Exception as error:
            last_error = error
    raise ValueError(f"No se pudo leer el CSV: {last_error}") from last_error


def parse_probability(value: object) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return math.nan
    text = str(value).strip().replace(",", ".")
    if not text or text.casefold() in {"nan", "none", "null", "-"}:
        return math.nan
    has_percent = "%" in text
    text = text.replace("%", "").strip()
    try:
        number = float(text)
    except ValueError:
        return math.nan
    if not math.isfinite(number):
        return math.nan
    if has_percent or abs(number) > 1:
        number /= 100.0
    return round(number, 6)


def parse_integer(value: object) -> int | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    match = re.search(r"-?\d+", str(value))
    return int(match.group()) if match else None


def _last_winner(row: pd.Series, result_column: str, df: pd.DataFrame) -> int | None:
    columns = [result_column]
    for name in WINNER_COLUMNS:
        column = _find_column(df, (name,))
        if column is not None and column not in columns:
            columns.append(column)
    for column in columns:
        number = parse_integer(row[column])
        if number is not None and 0 <= number <= 99:
            return number
    return None


def load_pending_play(
    df: pd.DataFrame,
    result_column: str = "Resultado",
) -> Play:
    result_col = _find_column(df, (result_column, *RESULT_COLUMNS))
    if result_col is None:
        raise ValueError(f"Falta la columna {result_column!r}")

    pending = (
        df[result_col]
        .astype(str)
        .str.strip()
        .str.casefold()
        .eq(PENDING)
    )
    positions = df.index[pending].tolist()
    if not positions:
        raise ValueError("No hay ninguna fila con 'Pendiente'")

    current = int(positions[-1])
    if current == 0:
        raise ValueError("La fila 'Pendiente' no tiene una fila anterior")

    current_row = df.iloc[current]
    previous_row = df.iloc[current - 1]

    markov_prediction = None
    for column in _markov_columns(df):
        markov_prediction = parse_integer(current_row[column])
        if markov_prediction is not None:
            break

    probabilities: dict[str, float] = {}
    for name in PROBABILITY_COLUMNS:
        column = _find_column(df, (name,))
        probabilities[name] = (
            parse_probability(current_row[column]) if column is not None else math.nan
        )

    return Play(
        row_number=current,
        last_winner=_last_winner(previous_row, result_col, df),
        markov_prediction=markov_prediction,
        ph1=probabilities["ph1"],
        p4=probabilities["p4"],
        p2=probabilities["p2"],
    )


def directional_signal(last_winner: int | None, markov_prediction: int | None) -> bool:
    return (
        (last_winner == 16 and markov_prediction == 7)
        or (last_winner == 14 and markov_prediction == 4)
        or (last_winner == 5 and markov_prediction in {0, 10})
    )


def quantile_signal(ph1: float, p4: float, p2: float) -> bool:
    return (
        (0.0657 < ph1 <= 0.0687)
        or (p4 > 0.0822)
        or (0.0635 < p2 <= 0.0822)
    )


def evaluate_play(play: Play) -> dict:
    directional = directional_signal(play.last_winner, play.markov_prediction)
    quantile = quantile_signal(play.ph1, play.p4, play.p2)
    reasons = []
    if directional:
        reasons.append("direccional")
    if quantile:
        reasons.append("cuantil")
    return {
        "ev_valid": directional or quantile,
        "ev_directional": directional,
        "ev_quantile": quantile,
        "ev_reasons": reasons,
    }


def is_viable(play: Play) -> bool:
    return bool(evaluate_play(play)["ev_valid"])


def record_to_play(record: dict) -> Play:
    return Play(
        row_number=0,
        last_winner=parse_integer(record.get("last_number")),
        markov_prediction=parse_integer(record.get(MARKOV_FIELD)),
        ph1=parse_probability(record.get("ph1")),
        p4=parse_probability(record.get("p4")),
        p2=parse_probability(record.get("p2")),
    )


def evaluate_record(record: dict) -> dict:
    return evaluate_play(record_to_play(record))


def _json_number(value: float) -> float | None:
    return None if not math.isfinite(value) else value


def process_file(path: str | Path) -> dict:
    play = load_pending_play(read_predictions(path))
    evaluation = evaluate_play(play)
    return {
        "viable": evaluation["ev_valid"],
        "row_number": play.row_number,
        "last_winner": play.last_winner,
        "markov_prediction": play.markov_prediction,
        "ph1": _json_number(play.ph1),
        "p4": _json_number(play.p4),
        "p2": _json_number(play.p2),
        "directional_signal": evaluation["ev_directional"],
        "quantile_signal": evaluation["ev_quantile"],
        "reasons": evaluation["ev_reasons"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Valida la jugada pendiente de un CSV de predicciones."
    )
    parser.add_argument("csv", type=Path, help="Ruta del archivo CSV")
    args = parser.parse_args(argv)

    try:
        result = process_file(args.csv)
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["viable"] else 1


if __name__ == "__main__":
    sys.exit(main())
