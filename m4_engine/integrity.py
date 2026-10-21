"""Auditoría de integridad del log de supervisión.

El CSV de supervisión no es un registro limpio: contiene predicciones
re-escritas (mismo `no. sorteo` y mismo vector M/H) con resultados
contradictorios, y bloques de filas cuyo identificador de sorteo es
anterior a todo el resto del histórico. Cualquier métrica calculada
tratando esas filas como observaciones independientes tiene un error
estándar inválido.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class IntegrityReport:
    total_rows: int
    unique_groups: int
    duplicated_rows: int
    conflicting_groups: int
    conflicting_rows: int
    clean_rows: int
    stale_draw_ids: tuple[int, ...]
    chronological_by_row: bool
    result_range: tuple[int, int]
    missing_cells: int
    notes: tuple[str, ...] = ()

    @property
    def independent_rows(self) -> int:
        """Filas que pueden contarse como observaciones independientes."""
        return self.clean_rows

    def as_dict(self) -> dict:
        return {
            "total_rows": self.total_rows,
            "unique_groups": self.unique_groups,
            "duplicated_rows": self.duplicated_rows,
            "conflicting_groups": self.conflicting_groups,
            "conflicting_rows": self.conflicting_rows,
            "clean_rows": self.clean_rows,
            "stale_draw_ids": list(self.stale_draw_ids),
            "chronological_by_row": self.chronological_by_row,
            "result_range": list(self.result_range),
            "missing_cells": self.missing_cells,
            "notes": list(self.notes),
        }


def analyze_integrity(frame: pd.DataFrame) -> IntegrityReport:
    """Evalúa la integridad del log y enumera los sesgos detectados."""
    notes: list[str] = []
    total = len(frame)

    groups = frame.groupby("group_id")["result"].nunique()
    conflicting_groups = int((groups > 1).sum())
    conflicting_rows = int(frame["label_conflict"].sum())
    clean = frame[~frame["label_conflict"]]
    clean_rows = int(len(clean.drop_duplicates(subset="group_id", keep="first")))

    duplicated_rows = int((frame["group_size"] > 1).sum())

    stale_ids: set[int] = set()
    if "draw_id" in frame.columns:
        draw_ids = frame["draw_id"].dropna().astype("Int64")
        if not draw_ids.empty:
            # Un identificador es obsoleto si reaparece en el log después de un
            # sorteo posterior: la predicción se re-escribió cuando el sistema
            # ya había avanzado.
            running_max = draw_ids.cummax()
            out_of_order = draw_ids < running_max
            stale_ids = {int(value) for value in draw_ids[out_of_order].dropna().unique()}
            if stale_ids:
                notes.append(
                    f"El identificador de sorteo {sorted(stale_ids)} reaparece después de "
                    "sorteos posteriores: son predicciones obsoletas re-escritas en el log, "
                    "no observaciones independientes."
                )

    chronological = bool(frame["draw_id"].dropna().is_monotonic_increasing)
    if not chronological:
        notes.append(
            "El orden de filas del log no es monótono en `no. sorteo`: el paso temporal "
            "debe derivarse del orden de fila, no del identificador de sorteo."
        )

    results = frame["result"].dropna().astype(int)
    result_range = (int(results.min()), int(results.max())) if not results.empty else (0, 0)

    missing_cells = int(frame.isna().sum().sum())
    if missing_cells:
        notes.append(f"Hay {missing_cells} celdas vacías en el log de supervisión.")

    if conflicting_rows:
        notes.append(
            f"{conflicting_rows} filas ({conflicting_rows / total:.1%}) pertenecen a "
            f"{conflicting_groups} grupos de predicción con resultados contradictorios: "
            "su etiqueta no es verificable y se descartan del cálculo principal."
        )

    return IntegrityReport(
        total_rows=total,
        unique_groups=int(frame["group_id"].nunique()),
        duplicated_rows=duplicated_rows,
        conflicting_groups=conflicting_groups,
        conflicting_rows=conflicting_rows,
        clean_rows=clean_rows,
        stale_draw_ids=tuple(sorted(stale_ids)),
        chronological_by_row=chronological,
        result_range=result_range,
        missing_cells=missing_cells,
        notes=tuple(notes),
    )
