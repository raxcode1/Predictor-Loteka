"""Ingesta y normalización del histórico de Ruleta Express.

Convierte las filas del CSV (`Fecha`, `Hora`, `No_Sorteo`, `Numero`), que se
guardan del más reciente al más antiguo, en una serie cronológica ordenada por
número de sorteo. Registra explícitamente:

- filas descartadas (etiqueta o número inválido, fuera de 0..18),
- duplicados con valores contradictorios,
- huecos en la numeración (sorteos no publicados o no capturados).

Los huecos importan: una transición `x[t-1] -> x[t]` sólo es válida si ambos
sorteos son consecutivos. Tratar un hueco como adyacencia contamina Markov,
los tests de independencia y la recuperación de estado PRNG.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import OUTPUT_MODULUS

EXPECTED_COLUMNS = ("Fecha", "Hora", "No_Sorteo", "Numero")


class DataValidationError(ValueError):
    """El histórico no cumple el esquema mínimo requerido."""


@dataclass(frozen=True)
class Dataset:
    """Serie cronológica normalizada y su procedencia."""

    draw_ids: np.ndarray  # int64, estrictamente creciente
    values: np.ndarray  # int64 en 0..OUTPUT_MODULUS-1
    dates: tuple[str, ...]
    times: tuple[str, ...]
    sha256: str
    dropped_rows: int = 0
    conflicting_duplicates: int = 0
    issues: tuple[str, ...] = field(default_factory=tuple)

    def __len__(self) -> int:
        return int(self.values.size)

    @property
    def consecutive(self) -> np.ndarray:
        """`consecutive[t]` es True si el sorteo t sigue inmediatamente a t-1."""
        mask = np.zeros(self.values.size, dtype=bool)
        if self.values.size > 1:
            mask[1:] = np.diff(self.draw_ids) == 1
        return mask

    @property
    def gap_count(self) -> int:
        """Número de sorteos ausentes dentro del rango observado."""
        if self.draw_ids.size < 2:
            return 0
        span = int(self.draw_ids[-1] - self.draw_ids[0] + 1)
        return span - int(self.draw_ids.size)

    def longest_contiguous_suffix(self) -> tuple[int, int]:
        """Índices `[start, end)` del bloque final sin huecos."""
        n = self.values.size
        if n == 0:
            return 0, 0
        consecutive = self.consecutive
        start = n - 1
        while start > 0 and consecutive[start]:
            start -= 1
        return start, n

    def summary(self) -> dict[str, Any]:
        return {
            "rows": len(self),
            "first_draw": int(self.draw_ids[0]) if len(self) else None,
            "last_draw": int(self.draw_ids[-1]) if len(self) else None,
            "first_date": self.dates[0] if self.dates else None,
            "last_date": self.dates[-1] if self.dates else None,
            "last_time": self.times[-1] if self.times else None,
            "missing_draws": self.gap_count,
            "consecutive_transitions": int(self.consecutive.sum()),
            "dropped_rows": self.dropped_rows,
            "conflicting_duplicates": self.conflicting_duplicates,
            "sha256": self.sha256,
            "issues": list(self.issues),
        }


def _parse_draw(label: Any) -> int | None:
    text = str(label or "").strip()
    if not text:
        return None
    try:
        return int(text.rsplit(" ", 1)[-1])
    except ValueError:
        return None


def _parse_value(raw: Any) -> int | None:
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None
    try:
        value = int(float(text))
    except ValueError:
        return None
    if not 0 <= value < OUTPUT_MODULUS:
        return None
    return value


def dataset_hash(draw_ids: np.ndarray, values: np.ndarray) -> str:
    """Hash estable del contenido normalizado (independiente del orden del CSV)."""
    digest = hashlib.sha256()
    digest.update(np.ascontiguousarray(draw_ids, dtype="<i8").tobytes())
    digest.update(np.ascontiguousarray(values, dtype="<i8").tobytes())
    return digest.hexdigest()


def from_rows(rows: Iterable[Mapping[str, Any]]) -> Dataset:
    """Normaliza filas del CSV/API en una serie cronológica."""
    by_draw: dict[int, tuple[int, str, str]] = {}
    dropped = 0
    conflicts = 0
    seen_any = False
    for row in rows:
        seen_any = True
        draw = _parse_draw(row.get("No_Sorteo"))
        value = _parse_value(row.get("Numero"))
        if draw is None or value is None:
            dropped += 1
            continue
        previous = by_draw.get(draw)
        if previous is not None and previous[0] != value:
            # Se conserva la primera aparición (la más reciente en el CSV) y se
            # cuenta el conflicto para que sea visible en el informe.
            conflicts += 1
            continue
        if previous is None:
            by_draw[draw] = (
                value,
                str(row.get("Fecha", "") or "").strip(),
                str(row.get("Hora", "") or "").strip(),
            )

    issues: list[str] = []
    if not seen_any:
        issues.append("histórico vacío")
    if dropped:
        issues.append(f"{dropped} filas descartadas por etiqueta o número inválido")
    if conflicts:
        issues.append(f"{conflicts} duplicados con valores contradictorios")

    ordered = sorted(by_draw)
    draw_ids = np.asarray(ordered, dtype=np.int64)
    values = np.asarray([by_draw[d][0] for d in ordered], dtype=np.int64)
    dates = tuple(by_draw[d][1] for d in ordered)
    times = tuple(by_draw[d][2] for d in ordered)
    dataset = Dataset(
        draw_ids=draw_ids,
        values=values,
        dates=dates,
        times=times,
        sha256=dataset_hash(draw_ids, values),
        dropped_rows=dropped,
        conflicting_duplicates=conflicts,
        issues=tuple(issues),
    )
    if dataset.gap_count:
        dataset = Dataset(
            draw_ids=dataset.draw_ids,
            values=dataset.values,
            dates=dataset.dates,
            times=dataset.times,
            sha256=dataset.sha256,
            dropped_rows=dropped,
            conflicting_duplicates=conflicts,
            issues=dataset.issues
            + (f"{dataset.gap_count} sorteos ausentes en la numeración",),
        )
    return dataset


def from_frame(frame: pd.DataFrame) -> Dataset:
    missing = [column for column in ("No_Sorteo", "Numero") if column not in frame.columns]
    if missing:
        raise DataValidationError(f"Faltan columnas requeridas en el CSV: {missing}")
    return from_rows(frame.fillna("").astype(str).to_dict(orient="records"))


def load_csv(path: Path | str) -> Dataset:
    """Lee el CSV oficial del proyecto (UTF-8 con o sin BOM)."""
    frame = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    return from_frame(frame)


def from_values(values: Iterable[int], start_draw: int = 1) -> Dataset:
    """Construye un Dataset sintético contiguo (tests y simulaciones)."""
    array = np.asarray(list(values), dtype=np.int64)
    if array.size and (array.min() < 0 or array.max() >= OUTPUT_MODULUS):
        raise DataValidationError("Valores fuera de 0..18.")
    draw_ids = np.arange(start_draw, start_draw + array.size, dtype=np.int64)
    return Dataset(
        draw_ids=draw_ids,
        values=array,
        dates=tuple("" for _ in range(array.size)),
        times=tuple("" for _ in range(array.size)),
        sha256=dataset_hash(draw_ids, array),
    )
