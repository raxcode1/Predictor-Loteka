from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "api") not in sys.path:
    sys.path.append(str(ROOT / "api"))

CSV_PATH = ROOT / "Resultados_RuletaExpress.csv"


def rows_from_values(values, start: int = 1000, date: str = "01/01/2026") -> list[dict[str, str]]:
    """Filas en el formato del CSV (más reciente primero)."""
    rows = [
        {"Fecha": date, "Hora": "10:00 AM", "No_Sorteo": f"No. {start + i}", "Numero": f"{int(v):02d}"}
        for i, v in enumerate(values)
    ]
    return list(reversed(rows))


def markov_chain(n: int, seed: int, stay_out: float = 0.0, sticky_next: float = 0.0) -> np.ndarray:
    """Cadena sintética con dependencia de orden 1.

    - stay_out: probabilidad de re-sortear si sale el mismo número (anti-repetición).
    - sticky_next: probabilidad de que el siguiente sea (prev + 1) % 19.
    """
    rng = np.random.default_rng(seed)
    out = np.empty(n, dtype=np.int64)
    out[0] = rng.integers(19)
    for t in range(1, n):
        if rng.random() < sticky_next:
            out[t] = (out[t - 1] + 1) % 19
            continue
        x = rng.integers(19)
        if x == out[t - 1] and rng.random() < stay_out:
            x = rng.integers(19)
        out[t] = x
    return out


@pytest.fixture(scope="session")
def uniform_values() -> np.ndarray:
    return np.random.default_rng(12345).integers(0, 19, size=6000)


@pytest.fixture(scope="session")
def dependent_values() -> np.ndarray:
    return markov_chain(6000, seed=7, sticky_next=0.25)
