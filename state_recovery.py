from __future__ import annotations

import math
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd
from z3 import BitVec, BitVecVal, LShR, Solver, URem, sat, unknown


CSV_PATH = Path(__file__).with_name("Resultados_RuletaExpress.csv")
OUTPUT_COLUMN = "Numero"
OBSERVED_COUNT = 50
VERIFICATION_COUNT = 10
OUTPUT_MODULUS = 19
EXPECTED_REPEAT_RATE = 1 / OUTPUT_MODULUS


@dataclass(frozen=True)
class LCG:
    name: str
    modulus: int
    multiplier: int
    increment: int
    maximum_shift: int


LCGS = (
    LCG(
        name="glibc",
        modulus=2**31,
        multiplier=1103515245,
        increment=12345,
        maximum_shift=30,
    ),
    LCG(
        name="java.util.Random",
        modulus=2**48,
        multiplier=25214903917,
        increment=11,
        maximum_shift=47,
    ),
)


def load_outputs(path: Path) -> list[int]:
    frame = pd.read_csv(path, usecols=[OUTPUT_COLUMN], nrows=OBSERVED_COUNT + VERIFICATION_COUNT)
    if len(frame) < OBSERVED_COUNT:
        raise ValueError(
            f"Se requieren al menos {OBSERVED_COUNT} filas, pero solo hay {len(frame)}."
        )

    values = (
        pd.to_numeric(frame[OUTPUT_COLUMN], errors="raise")
        .astype("int64")
        .tolist()
    )
    invalid = [value for value in values if not 0 <= value < OUTPUT_MODULUS]
    if invalid:
        raise ValueError(
            f"Se encontraron valores fuera del rango 0..{OUTPUT_MODULUS - 1}: {invalid[:5]}"
        )
    # El archivo está ordenado de más reciente a más antiguo.
    # Se invierte para que S_0 corresponda al primer resultado cronológico.
    return list(reversed(values))


def report_sequence_diagnostics(path: Path) -> None:
    frame = pd.read_csv(path, usecols=[OUTPUT_COLUMN])
    values = pd.to_numeric(frame[OUTPUT_COLUMN], errors="raise").astype("int64")
    repeats = int((values.iloc[1:].to_numpy() == values.iloc[:-1].to_numpy()).sum())
    transitions = max(len(values) - 1, 0)

    if transitions == 0:
        return

    observed_rate = repeats / transitions
    expected_repeats = transitions * EXPECTED_REPEAT_RATE
    print(
        "Diagnóstico: "
        f"{repeats}/{transitions} repeticiones adyacentes "
        f"({observed_rate:.2%}); "
        f"esperadas bajo independencia: aproximadamente "
        f"{expected_repeats:.0f} ({EXPECTED_REPEAT_RATE:.2%}).",
        flush=True,
    )

    if observed_rate < EXPECTED_REPEAT_RATE / 2:
        print(
            "Advertencia: la secuencia parece aplicar una regla de "
            "anti-repetición o postprocesamiento; una predicción basada "
            "solo en un LCG no sería válida.",
            flush=True,
        )


def transition_counts(values: list[int]) -> list[list[int]]:
    counts = [[0 for _ in range(OUTPUT_MODULUS)] for _ in range(OUTPUT_MODULUS)]
    for previous, current in zip(values, values[1:]):
        counts[previous][current] += 1
    return counts


def backtest_transition_model(values: list[int], alpha: float = 1.0) -> dict:
    """Evaluate one-step transition predictions without using future values."""
    counts = [[0 for _ in range(OUTPUT_MODULUS)] for _ in range(OUTPUT_MODULUS)]
    correct = 0
    total = 0
    log_loss = 0.0

    for index in range(1, len(values)):
        previous = values[index - 1]
        actual = values[index]
        row_total = sum(counts[previous])
        denominator = row_total + alpha * OUTPUT_MODULUS
        probabilities = [
            (counts[previous][candidate] + alpha) / denominator
            for candidate in range(OUTPUT_MODULUS)
        ]
        prediction = max(range(OUTPUT_MODULUS), key=probabilities.__getitem__)
        correct += int(prediction == actual)
        log_loss -= math.log(probabilities[actual])
        total += 1
        counts[previous][actual] += 1

    return {
        "accuracy": correct / total if total else 0.0,
        "log_loss": log_loss / total if total else 0.0,
    }


def rank_next_values(values: list[int], limit: int = 5) -> list[tuple[int, float]]:
    """Rank candidates using observed transitions and a recency-weighted prior."""
    counts = transition_counts(values)
    previous = values[-1]
    transition_row = counts[previous]
    transition_total = sum(transition_row)

    recent = values[-min(500, len(values)) :]
    recent_counts = pd.Series(recent).value_counts().to_dict()
    scores = []

    for candidate in range(OUTPUT_MODULUS):
        transition_probability = (
            (transition_row[candidate] + 1)
            / (transition_total + OUTPUT_MODULUS)
        )
        recent_probability = (
            (recent_counts.get(candidate, 0) + 1)
            / (len(recent) + OUTPUT_MODULUS)
        )
        # Transition history gets more weight because it conditions on the
        # most recent observed result; the prior reduces zero-frequency bias.
        score = 0.75 * transition_probability + 0.25 * recent_probability
        scores.append((candidate, score))

    total_score = sum(score for _, score in scores)
    return [
        (candidate, score / total_score)
        for candidate, score in sorted(scores, key=lambda item: item[1], reverse=True)[:limit]
    ]


def markov_probabilities(
    history: list[int],
    order: int,
    alpha: float = 1.0,
    window: int | None = None,
    forbid_repeat: bool = False,
) -> list[float]:
    if len(history) <= order:
        return [1 / OUTPUT_MODULUS] * OUTPUT_MODULUS

    data = history[-window:] if window is not None else history
    context = tuple(data[-order:])
    counts = [0] * OUTPUT_MODULUS
    context_total = 0

    for index in range(order, len(data)):
        if tuple(data[index - order:index]) == context:
            counts[data[index]] += 1
            context_total += 1

    denominator = context_total + alpha * OUTPUT_MODULUS
    return [
        (count + alpha) / denominator
        for count in counts
    ]
    if forbid_repeat and context:
        probabilities[context[-1]] = 0.0
        total = sum(probabilities)
        if total:
            probabilities = [probability / total for probability in probabilities]


def frequency_probabilities(
    history: list[int],
    alpha: float = 1.0,
    window: int | None = None,
) -> list[float]:
    data = history[-window:] if window is not None else history
    counts = [0] * OUTPUT_MODULUS
    for value in data:
        counts[value] += 1
    denominator = len(data) + alpha * OUTPUT_MODULUS
    return [(count + alpha) / denominator for count in counts]


def blended_probabilities(
    history: list[int],
    order: int,
    alpha: float,
    window: int | None,
    frequency_weight: float,
    forbid_repeat: bool,
) -> list[float]:
    markov = markov_probabilities(
        history,
        order,
        alpha,
        window,
        forbid_repeat,
    )
    frequency = frequency_probabilities(history, alpha, window)
    probabilities = [
        (1 - frequency_weight) * markov[index]
        + frequency_weight * frequency[index]
        for index in range(OUTPUT_MODULUS)
    ]
    total = sum(probabilities)
    return [probability / total for probability in probabilities]


def evaluate_markov_model(
    values: list[int],
    order: int,
    alpha: float,
    window: int | None,
    forbid_repeat: bool = False,
    frequency_weight: float = 0.0,
    holdout_fraction: float = 0.10,
) -> dict:
    split = max(order + 1, int(len(values) * (1 - holdout_fraction)))
    log_loss = 0.0
    total = 0
    hits = [0, 0, 0]
    step = max(1, (len(values) - split) // 10)

    for index in range(split, len(values), step):
        probabilities = blended_probabilities(
            values[:index],
            order,
            alpha,
            window,
            frequency_weight,
            forbid_repeat,
        )
        ranking = sorted(
            range(OUTPUT_MODULUS),
            key=probabilities.__getitem__,
            reverse=True,
        )
        log_loss -= math.log(probabilities[values[index]])
        for position, cutoff in enumerate((1, 3, 5)):
            hits[position] += int(values[index] in ranking[:cutoff])
        total += 1

    return {
        "log_loss": log_loss / total if total else float("inf"),
        "top1": hits[0] / total if total else 0.0,
        "top3": hits[1] / total if total else 0.0,
        "top5": hits[2] / total if total else 0.0,
    }


def select_markov_model(values: list[int]) -> tuple[dict, list[tuple[int, float]]]:
    candidates = []
    for order in range(1, 3):
        for alpha in (0.5, 1.0):
            for window in (None, 1000):
                for forbid_repeat in (False, True):
                    for frequency_weight in (0.0, 0.25, 0.5, 0.75, 1.0):
                        metrics = evaluate_markov_model(
                        values,
                        order,
                        alpha,
                        window,
                        forbid_repeat,
                        frequency_weight,
                        )
                        candidates.append(
                            {
                                "order": order,
                                "alpha": alpha,
                                "window": window,
                                "forbid_repeat": forbid_repeat,
                                "frequency_weight": frequency_weight,
                                **metrics,
                            }
                        )

    best = min(candidates, key=lambda candidate: candidate["log_loss"])
    probabilities = blended_probabilities(
        values,
        best["order"],
        best["alpha"],
        best["window"],
        best["frequency_weight"],
        best["forbid_repeat"],
    )
    ranked = sorted(
        enumerate(probabilities),
        key=lambda item: item[1],
        reverse=True,
    )[:5]
    return best, ranked


def report_black_box_forecast(path: Path) -> None:
    frame = pd.read_csv(path, usecols=[OUTPUT_COLUMN])
    values = pd.to_numeric(frame[OUTPUT_COLUMN], errors="raise").astype("int64").tolist()
    values = list(reversed(values))
    if len(values) < 2:
        raise ValueError("Se necesitan al menos dos resultados para analizar transiciones.")

    backtest = backtest_transition_model(values)
    forecast = rank_next_values(values)
    selected_model, selected_forecast = select_markov_model(values)
    baseline = 1 / OUTPUT_MODULUS

    print(
        f"Backtest transición: accuracy={backtest['accuracy']:.2%}, "
        f"baseline_uniforme={baseline:.2%}, "
        f"log_loss={backtest['log_loss']:.4f}",
        flush=True,
    )
    print(f"Último resultado cronológico: {values[-1]}", flush=True)
    print("Ranking heurístico para el siguiente resultado:", flush=True)
    for rank, (candidate, probability) in enumerate(forecast, start=1):
        print(f"{rank}. {candidate} ({probability:.2%})", flush=True)
    print(
        "Modelo Markov seleccionado por holdout: "
        f"orden={selected_model['order']}, "
        f"alpha={selected_model['alpha']}, "
        f"ventana={selected_model['window']}, "
        f"anti_repeticion={selected_model['forbid_repeat']}, "
        f"peso_frecuencia={selected_model['frequency_weight']}, "
        f"log_loss={selected_model['log_loss']:.4f}, "
        f"top1={selected_model['top1']:.2%}, "
        f"top3={selected_model['top3']:.2%}, "
        f"top5={selected_model['top5']:.2%}",
        flush=True,
    )
    print("Ranking Markov seleccionado:", flush=True)
    for rank, (candidate, probability) in enumerate(selected_forecast, start=1):
        print(f"{rank}. {candidate} ({probability:.2%})", flush=True)
    if selected_model["top1"] <= baseline:
        print(
            "El backtest top-1 no supera la línea base uniforme; "
            "no se observa ventaja predictiva estable.",
            flush=True,
        )

    if backtest["accuracy"] <= baseline:
        print(
            "La validación no supera la línea base uniforme; el ranking "
            "no debe interpretarse como una predicción determinista.",
            flush=True,
        )


def mapped_state(state, shift: int):
    return URem(LShR(state, shift), BitVecVal(OUTPUT_MODULUS, state.size()))


def solve_candidate(outputs: list[int], generator: LCG, shift: int):
    state_count = OBSERVED_COUNT
    width = generator.modulus.bit_length() - 1
    states = [
        BitVec(f"{generator.name.replace('.', '_')}_S_{index}", width)
        for index in range(state_count)
    ]
    solver = Solver()
    solver.set(timeout=5000)
    for index in range(state_count - 1):
        solver.add(
            states[index + 1]
            == generator.multiplier * states[index] + generator.increment
        )

    for index, observed in enumerate(outputs[:OBSERVED_COUNT]):
        solver.add(mapped_state(states[index], shift) == observed)

    status = solver.check()
    if status == unknown:
        return "unknown"
    if status != sat:
        return None

    model = solver.model()
    seed = model.eval(states[0]).as_long()
    next_state = seed
    for _ in range(OBSERVED_COUNT):
        next_state = (
            generator.multiplier * next_state + generator.increment
        ) % generator.modulus
    predicted = []
    for _ in range(VERIFICATION_COUNT):
        predicted.append((next_state >> shift) % OUTPUT_MODULUS)
        next_state = (
            generator.multiplier * next_state + generator.increment
        ) % generator.modulus
    return seed, predicted


def verify_candidate(
    predicted: Iterable[int],
    outputs: list[int],
) -> tuple[bool, list[tuple[int, int, int]]]:
    expected = outputs[OBSERVED_COUNT : OBSERVED_COUNT + VERIFICATION_COUNT]
    mismatches = [
        (OBSERVED_COUNT + offset + 1, actual, forecast)
        for offset, (actual, forecast) in enumerate(zip(expected, predicted))
        if actual != forecast
    ]
    return len(mismatches) == 0, mismatches


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state-recovery",
        action="store_true",
        help="Ejecutar además la búsqueda SMT de semillas LCG.",
    )
    args = parser.parse_args()

    report_sequence_diagnostics(CSV_PATH)
    report_black_box_forecast(CSV_PATH)
    if not args.state_recovery:
        return

    outputs = load_outputs(CSV_PATH)
    candidates = []
    unknown_candidates = []

    for generator in LCGS:
        for shift in range(generator.maximum_shift + 1):
            print(
                f"Probando generador={generator.name}, shift={shift}",
                flush=True,
            )
            result = solve_candidate(outputs, generator, shift)
            if result == "unknown":
                unknown_candidates.append((generator.name, shift))
                continue
            if result is None:
                continue

            seed, predicted = result
            verified, mismatches = verify_candidate(predicted, outputs)
            candidate = {
                "generator": generator.name,
                "shift": shift,
                "seed": seed,
                "predicted": predicted,
                "verified": verified,
                "mismatches": mismatches,
            }
            candidates.append(candidate)

            if verified:
                print("Modelo verificado")
                print(f"generador={generator.name}")
                print(f"shift={shift}")
                print(f"S_0={seed}")
                print(f"salidas_51_60={predicted}")
                return

    if not candidates and unknown_candidates:
        raise RuntimeError(
            "Z3 agotó el tiempo en todas las combinaciones no satisfechas; "
            f"casos desconocidos: {unknown_candidates}"
        )

    if not candidates:
        raise RuntimeError(
            "No se encontró ninguna semilla que satisfaga las 50 observaciones. "
            "El CSV puede no provenir de uno de los LCG configurados o el "
            "mapeo/orden temporal puede ser diferente."
        )

    print("Se encontraron modelos para las observaciones, pero ninguno verificó 51..60.")
    for candidate in candidates:
        print(
            f"generador={candidate['generator']} "
            f"shift={candidate['shift']} "
            f"S_0={candidate['seed']} "
            f"predicciones={candidate['predicted']} "
            f"mismatches={candidate['mismatches']}"
        )


if __name__ == "__main__":
    main()
