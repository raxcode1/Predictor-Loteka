"""Validación empírica de la estrategia cuantitativa M4.

El módulo reproduce los hallazgos del PDF sobre el CSV de supervisión y, sobre
todo, los somete a las pruebas que un sistema de producción exige: integridad
del log, contraste contra la línea base correcta, intervalos de confianza,
validación cronológica fuera de muestra, sensibilidad a los umbrales y control
de multiplicidad.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
import pandas as pd

from .config import EUROPEAN_37, EXPRESS_19, PayoutModel, StrategySpec
from .features import build_features, collapse_conflicts, zone_labels, zone_of
from .integrity import IntegrityReport, analyze_integrity
from .stats import (
    Interval,
    binomial_survival,
    bootstrap_interval,
    fisher_exact_two_sided,
    holm_bonferroni,
    proportion_difference_test,
    wilson_interval,
)

CONFIRMED = "CONFIRMADO"
NOT_CONFIRMED = "NO CONFIRMADO"
CONDITIONAL = "CONDICIONAL"


@dataclass(frozen=True)
class ClaimCheck:
    """Contraste entre una afirmación del PDF y lo que muestran los datos."""

    name: str
    claim: str
    expected: float | None
    observed: float | None
    tolerance: float
    status: str
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "claim": self.claim,
            "expected": self.expected,
            "observed": self.observed,
            "tolerance": self.tolerance,
            "status": self.status,
            "note": self.note,
        }


@dataclass(frozen=True)
class ZoneRow:
    zone: str
    observations: int
    hits: int
    accuracy: float
    interval: Interval
    p_value: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "zone": self.zone,
            "observations": self.observations,
            "hits": self.hits,
            "accuracy": self.accuracy,
            "ci_low": self.interval.low,
            "ci_high": self.interval.high,
            "p_value": self.p_value,
        }


@dataclass(frozen=True)
class ConfusionMatrix:
    universe: str
    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int

    @property
    def total(self) -> int:
        return (
            self.true_positive
            + self.false_positive
            + self.false_negative
            + self.true_negative
        )

    @property
    def precision(self) -> float:
        predicted = self.true_positive + self.false_positive
        return self.true_positive / predicted if predicted else 0.0

    @property
    def recall(self) -> float:
        actual = self.true_positive + self.false_negative
        return self.true_positive / actual if actual else 0.0

    @property
    def accuracy(self) -> float:
        return self.true_positive / self.total if self.total else 0.0

    @property
    def specificity(self) -> float:
        negative = self.true_negative + self.false_negative
        return self.true_negative / negative if negative else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "universe": self.universe,
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
            "true_negative": self.true_negative,
            "total": self.total,
            "precision": self.precision,
            "recall": self.recall,
            "accuracy": self.accuracy,
            "specificity": self.specificity,
        }


@dataclass(frozen=True)
class PnlResult:
    payout: str
    bets: int
    wins: int
    net_multiplier: float
    break_even: float
    total_units: float
    roi: float
    roi_interval: Interval
    max_drawdown_units: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "payout": self.payout,
            "bets": self.bets,
            "wins": self.wins,
            "net_multiplier": self.net_multiplier,
            "break_even": self.break_even,
            "total_units": self.total_units,
            "roi": self.roi,
            "roi_ci_low": self.roi_interval.low,
            "roi_ci_high": self.roi_interval.high,
            "max_drawdown_units": self.max_drawdown_units,
        }


@dataclass
class ValidationReport:
    """Resultado completo de la validación empírica."""

    spec: StrategySpec
    integrity: IntegrityReport
    checks: list[ClaimCheck] = field(default_factory=list)
    zones_pooled: list[ZoneRow] = field(default_factory=list)
    zones_model: list[ZoneRow] = field(default_factory=list)
    window: dict[str, Any] = field(default_factory=dict)
    spread_effect: dict[str, Any] = field(default_factory=dict)
    anchor: dict[str, Any] = field(default_factory=dict)
    confusion: list[ConfusionMatrix] = field(default_factory=list)
    pnl: list[PnlResult] = field(default_factory=list)
    walk_forward: list[dict[str, Any]] = field(default_factory=list)
    sensitivity: list[dict[str, Any]] = field(default_factory=list)
    primary_modes: list[dict[str, Any]] = field(default_factory=list)
    multiplicity: list[dict[str, Any]] = field(default_factory=list)
    sensitivity_summary: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "spec": {
                "model": self.spec.model,
                "probability_window": [self.spec.probability_min, self.spec.probability_max],
                "max_spread": self.spec.max_spread,
                "anchor_numbers": sorted(self.spec.anchor_numbers),
                "primary_mode": self.spec.primary_mode,
            },
            "integrity": self.integrity.as_dict(),
            "checks": [check.as_dict() for check in self.checks],
            "zones_pooled": [row.as_dict() for row in self.zones_pooled],
            "zones_model": [row.as_dict() for row in self.zones_model],
            "window": self.window,
            "spread_effect": self.spread_effect,
            "anchor": self.anchor,
            "confusion": [matrix.as_dict() for matrix in self.confusion],
            "pnl": [result.as_dict() for result in self.pnl],
            "walk_forward": self.walk_forward,
            "sensitivity": self.sensitivity,
            "primary_modes": self.primary_modes,
            "multiplicity": self.multiplicity,
            "sensitivity_summary": self.sensitivity_summary,
            "notes": self.notes,
        }


# ----------------------------------------------------------------------
# Utilidades internas
# ----------------------------------------------------------------------
def _pooled_observations(frame: pd.DataFrame, spec: StrategySpec) -> pd.DataFrame:
    """Apila las predicciones de los diez submodelos en una tabla larga."""
    records: list[pd.DataFrame] = []
    for model in spec.models_in_matrix:
        key = model.lower()
        records.append(
            pd.DataFrame(
                {
                    "model": model,
                    "probability": frame[f"p_{key}"].to_numpy(dtype=float),
                    "hit": (
                        frame[f"n_{key}"].to_numpy(dtype=float)
                        == frame["result"].to_numpy(dtype=float)
                    ),
                }
            )
        )
    return pd.concat(records, ignore_index=True)


def _zone_table(observations: pd.DataFrame, spec: StrategySpec) -> list[ZoneRow]:
    zones = observations["probability"].map(lambda value: zone_of(value, spec))
    rows: list[ZoneRow] = []
    for zone in zone_labels(spec):
        selection = observations[zones == zone]
        hits = int(selection["hit"].sum())
        total = int(len(selection))
        accuracy = hits / total if total else 0.0
        interval = wilson_interval(hits, total)
        p_value = binomial_survival(hits, total, 1.0 / 19.0)
        rows.append(
            ZoneRow(
                zone=zone,
                observations=total,
                hits=hits,
                accuracy=accuracy,
                interval=interval,
                p_value=p_value,
            )
        )
    return rows


def _confusion(frame: pd.DataFrame, spec: StrategySpec, universe: str) -> ConfusionMatrix:
    alert = frame["alert"].to_numpy(dtype=bool)
    hit = frame["hit_model"].to_numpy(dtype=bool)
    return ConfusionMatrix(
        universe=universe,
        true_positive=int((alert & hit).sum()),
        false_positive=int((alert & ~hit).sum()),
        false_negative=int((~alert & hit).sum()),
        true_negative=int((~alert & ~hit).sum()),
    )


def _pnl(hits: np.ndarray, payout: PayoutModel) -> PnlResult:
    count = int(hits.size)
    wins = int(hits.sum())
    if count == 0:
        return PnlResult(
            payout=payout.name,
            bets=0,
            wins=0,
            net_multiplier=payout.net_multiplier,
            break_even=payout.break_even,
            total_units=0.0,
            roi=0.0,
            roi_interval=Interval(0.0, 0.0),
            max_drawdown_units=0.0,
        )
    units = np.where(hits > 0, payout.net_multiplier, -1.0)
    equity = np.cumsum(units)
    running_max = np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:]
    drawdown = float(np.max(running_max - equity))
    total = float(units.sum())

    def roi(sample: np.ndarray) -> float:
        if sample.size == 0:
            return 0.0
        return float(np.mean(np.where(sample > 0, payout.net_multiplier, -1.0)))

    interval = bootstrap_interval(hits.astype(float), roi, iterations=4000)
    return PnlResult(
        payout=payout.name,
        bets=count,
        wins=wins,
        net_multiplier=payout.net_multiplier,
        break_even=payout.break_even,
        total_units=total,
        roi=total / count,
        roi_interval=interval,
        max_drawdown_units=drawdown,
    )


# ----------------------------------------------------------------------
# Validación principal
# ----------------------------------------------------------------------
def validate_strategy(
    frame: pd.DataFrame,
    spec: StrategySpec | None = None,
    payouts: Sequence[PayoutModel] | None = None,
    sensitivity_grid: bool = True,
) -> ValidationReport:
    """Ejecuta la batería completa de validación sobre el log de supervisión."""
    spec = spec or StrategySpec()
    payouts = list(payouts or (EUROPEAN_37, EXPRESS_19))

    enriched = build_features(frame, spec)
    clean = collapse_conflicts(enriched)
    integrity = analyze_integrity(enriched)

    report = ValidationReport(spec=spec, integrity=integrity)
    report.notes.extend(integrity.notes)

    pooled = _pooled_observations(enriched, spec)
    report.zones_pooled = _zone_table(pooled, spec)
    report.zones_model = _zone_table(
        pd.DataFrame(
            {
                "model": spec.model,
                "probability": enriched["model_probability"].to_numpy(dtype=float),
                "hit": enriched["hit_model"].to_numpy(dtype=bool),
            }
        ),
        spec,
    )

    # -------------------------------------------------- ventana operativa
    window_raw = enriched[enriched["in_window"]]
    window_clean = clean[clean["in_window"]]
    report.window = {
        "raw": _window_summary(window_raw),
        "clean": _window_summary(window_clean),
        "hit_concentration": _concentration(window_clean),
    }

    # -------------------------------------------------- efecto del spread
    report.spread_effect = _spread_effect(enriched, spec)

    # -------------------------------------------------- factor de anclaje
    report.anchor = _anchor_effect(window_clean, spec)

    # -------------------------------------------------- modos de aislamiento
    report.primary_modes = _primary_modes(enriched, spec)

    # -------------------------------------------------- matrices de confusión
    report.confusion = [
        _confusion(enriched, spec, "log completo"),
        _confusion(window_raw, spec, "ventana M4 (log completo)"),
        _confusion(clean, spec, "log depurado"),
        _confusion(window_clean, spec, "ventana M4 (log depurado)"),
    ]

    # -------------------------------------------------- PnL sobre señales válidas
    report.pnl = []
    alert_raw = enriched[enriched["alert"]]
    alert_clean = clean[clean["alert"]]
    for label, subset in (("raw", alert_raw), ("clean", alert_clean)):
        for payout in payouts:
            result = _pnl(subset["hit_model"].to_numpy(dtype=bool), payout)
            report.pnl.append(
                PnlResult(
                    payout=f"{payout.name}:{label}",
                    bets=result.bets,
                    wins=result.wins,
                    net_multiplier=result.net_multiplier,
                    break_even=result.break_even,
                    total_units=result.total_units,
                    roi=result.roi,
                    roi_interval=result.roi_interval,
                    max_drawdown_units=result.max_drawdown_units,
                )
            )

    # -------------------------------------------------- validación cronológica
    report.walk_forward = _walk_forward(clean, spec, folds=4)

    # -------------------------------------------------- sensibilidad
    if sensitivity_grid:
        report.sensitivity = _sensitivity(enriched, spec)
        report.sensitivity_summary = _sensitivity_summary(report.sensitivity, spec)

    # -------------------------------------------------- multiplicidad
    report.multiplicity = _multiplicity(report)

    # -------------------------------------------------- contrastes del PDF
    report.checks = _claim_checks(report, enriched, clean, spec)

    return report


def _window_summary(subset: pd.DataFrame) -> dict[str, Any]:
    total = int(len(subset))
    hits = int(subset["hit_model"].sum())
    accuracy = hits / total if total else 0.0
    interval = wilson_interval(hits, total)
    return {
        "observations": total,
        "hits": hits,
        "accuracy": accuracy,
        "ci_low": interval.low,
        "ci_high": interval.high,
        "p_value_19": binomial_survival(hits, total, 1.0 / 19.0),
        "p_value_37": binomial_survival(hits, total, 1.0 / 37.0),
        "break_even_19": 1.0 / 19.0,
        "break_even_37": 1.0 / 37.0,
    }


def _concentration(subset: pd.DataFrame) -> dict[str, Any]:
    """Concentración de aciertos por número predicho."""
    if subset.empty:
        return {"by_number": [], "without_dominant": {}}
    grouped = subset.groupby("model_number")["hit_model"].agg(["size", "sum"])
    by_number = [
        {
            "number": int(number),
            "observations": int(row["size"]),
            "hits": int(row["sum"]),
            "accuracy": float(row["sum"] / row["size"]),
        }
        for number, row in grouped.iterrows()
    ]
    by_number.sort(key=lambda item: (-item["accuracy"], -item["observations"]))
    if not by_number:
        return {"by_number": [], "without_dominant": {}}
    dominant = by_number[0]
    remainder = subset[subset["model_number"] != dominant["number"]]
    remainder_hits = int(remainder["hit_model"].sum())
    remainder_total = int(len(remainder))
    return {
        "by_number": by_number,
        "dominant_number": dominant["number"],
        "without_dominant": {
            "observations": remainder_total,
            "hits": remainder_hits,
            "accuracy": remainder_hits / remainder_total if remainder_total else 0.0,
            "p_value_19": binomial_survival(remainder_hits, remainder_total, 1.0 / 19.0),
        },
    }


def _spread_effect(enriched: pd.DataFrame, spec: StrategySpec) -> dict[str, Any]:
    window = enriched[enriched["in_window"]]
    kept = window[window["spread_ok"]]
    discarded = window[~window["spread_ok"]]
    kept_hits = int(kept["hit_model"].sum())
    discarded_hits = int(discarded["hit_model"].sum())
    kept_total = int(len(kept))
    discarded_total = int(len(discarded))
    before = window["hit_model"].mean() if len(window) else 0.0
    after = kept["hit_model"].mean() if kept_total else 0.0
    return {
        "window_observations": int(len(window)),
        "discarded": discarded_total,
        "discarded_hits": discarded_hits,
        "kept": kept_total,
        "kept_hits": kept_hits,
        "accuracy_before": before,
        "accuracy_after": after,
        "delta": after - before,
        "verdict": (
            "el filtro de dispersión no aporta valor predictivo: elimina aciertos "
            "y reduce la tasa observada"
            if kept_total and after < before
            else "el filtro de dispersión es neutro o favorable en este histórico"
        ),
    }


def _anchor_effect(window: pd.DataFrame, spec: StrategySpec) -> dict[str, Any]:
    if window.empty:
        return {"observations": 0}
    anchored = window[window["anchor"]]
    plain = window[~window["anchor"]]
    a = int(anchored["hit_model"].sum())
    b = int(len(anchored)) - a
    c = int(plain["hit_model"].sum())
    d = int(len(plain)) - c
    difference = proportion_difference_test(a, a + b, c, c + d)
    return {
        "observations": int(len(window)),
        "anchored": {
            "observations": a + b,
            "hits": a,
            "accuracy": a / (a + b) if (a + b) else 0.0,
            "interval": wilson_interval(a, a + b).as_tuple(),
        },
        "not_anchored": {
            "observations": c + d,
            "hits": c,
            "accuracy": c / (c + d) if (c + d) else 0.0,
            "interval": wilson_interval(c, c + d).as_tuple(),
        },
        "difference": difference,
        "fisher_p_value": fisher_exact_two_sided(a, b, c, d),
        "verdict": (
            "no hay evidencia estadística suficiente para escalar el stake: "
            "la diferencia no es significativa"
            if fisher_exact_two_sided(a, b, c, d) > 0.05
            else "la diferencia de ancla es estadísticamente significativa"
        ),
    }


def _primary_modes(enriched: pd.DataFrame, spec: StrategySpec) -> list[dict[str, Any]]:
    modes: list[dict[str, Any]] = []
    for mode in ("any", "tie", "strict"):
        if mode == "any":
            mask = enriched["in_window"] & enriched["spread_ok"]
        elif mode == "tie":
            mask = enriched["in_window"] & enriched["spread_ok"] & enriched["m4_tied_max"]
        else:
            mask = enriched["in_window"] & enriched["spread_ok"] & enriched["m4_sole_max"]
        subset = enriched[mask]
        hits = int(subset["hit_model"].sum())
        modes.append(
            {
                "mode": mode,
                "alerts": int(len(subset)),
                "hits": hits,
                "accuracy": hits / len(subset) if len(subset) else 0.0,
                "p_value_19": binomial_survival(hits, int(len(subset)), 1.0 / 19.0),
                "tied_max_rows": int(enriched["m4_tied_max"].sum()),
                "tied_max_inside_window": int(
                    (enriched["m4_tied_max"] & enriched["in_window"]).sum()
                ),
            }
        )
    return modes


def _walk_forward(clean: pd.DataFrame, spec: StrategySpec, folds: int = 4) -> list[dict[str, Any]]:
    """Validación cronológica con umbrales fijos (sin reentrenamiento)."""
    if clean.empty:
        return []
    size = len(clean) // folds
    results: list[dict[str, Any]] = []
    for index in range(folds):
        start = index * size
        stop = len(clean) if index == folds - 1 else (index + 1) * size
        subset = clean.iloc[start:stop]
        alerts = subset[subset["alert"]]
        hits = int(alerts["hit_model"].sum())
        total = int(len(alerts))
        results.append(
            {
                "fold": index + 1,
                "rows": int(len(subset)),
                "alerts": total,
                "hits": hits,
                "accuracy": hits / total if total else 0.0,
                "p_value_19": binomial_survival(hits, total, 1.0 / 19.0),
            }
        )
    half = len(clean) // 2
    for label, subset in (
        ("in_sample", clean.iloc[:half]),
        ("out_of_sample", clean.iloc[half:]),
    ):
        alerts = subset[subset["alert"]]
        hits = int(alerts["hit_model"].sum())
        total = int(len(alerts))
        results.append(
            {
                "fold": label,
                "rows": int(len(subset)),
                "alerts": total,
                "hits": hits,
                "accuracy": hits / total if total else 0.0,
                "p_value_19": binomial_survival(hits, total, 1.0 / 19.0),
            }
        )
    return results


def _sensitivity(enriched: pd.DataFrame, spec: StrategySpec) -> list[dict[str, Any]]:
    """Mapa de sensibilidad de los umbrales: meseta o filo de navaja."""
    rows: list[dict[str, Any]] = []
    lowers = (0.045, 0.050, 0.055)
    uppers = (0.065, 0.070, 0.075, 0.080)
    caps = (0.030, 0.047, 0.060, 1.0)
    for lower in lowers:
        for upper in uppers:
            for cap in caps:
                selection = enriched[
                    (enriched["model_probability"] > lower)
                    & (enriched["model_probability"] <= upper)
                    & (enriched["spread"] <= cap)
                ]
                hits = int(selection["hit_model"].sum())
                total = int(len(selection))
                rows.append(
                    {
                        "lower": lower,
                        "upper": upper,
                        "max_spread": cap,
                        "observations": total,
                        "hits": hits,
                        "accuracy": hits / total if total else 0.0,
                        "p_value_19": binomial_survival(hits, total, 1.0 / 19.0),
                        "is_spec": (
                            abs(lower - spec.probability_min) < 1e-9
                            and abs(upper - spec.probability_max) < 1e-9
                            and abs(cap - spec.max_spread) < 1e-9
                        ),
                    }
                )
    return rows


def _sensitivity_summary(rows: list[dict[str, Any]], spec: StrategySpec) -> dict[str, Any]:
    """Resume el vecindario de umbrales alrededor de la configuración de la spec."""
    usable = [row for row in rows if row["observations"] >= 20]
    accuracies = [row["accuracy"] for row in usable]
    window_rows = [
        row
        for row in usable
        if abs(row["lower"] - spec.probability_min) < 1e-9
        and abs(row["upper"] - spec.probability_max) < 1e-9
    ]
    cap_rows = [
        row
        for row in usable
        if abs(row["max_spread"] - spec.max_spread) < 1e-9
    ]
    return {
        "configurations": len(usable),
        "min": min(accuracies) if accuracies else 0.0,
        "median": sorted(accuracies)[len(accuracies) // 2] if accuracies else 0.0,
        "max": max(accuracies) if accuracies else 0.0,
        "window_neighbourhood": [
            {
                "max_spread": row["max_spread"],
                "observations": row["observations"],
                "accuracy": row["accuracy"],
            }
            for row in window_rows
        ],
        "cap_neighbourhood": [
            {
                "lower": row["lower"],
                "upper": row["upper"],
                "observations": row["observations"],
                "accuracy": row["accuracy"],
            }
            for row in cap_rows
        ],
    }


def _multiplicity(report: ValidationReport) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for row in report.zones_pooled:
        entries.append(
            {
                "hypothesis": f"zona {row.zone} de la matriz completa",
                "p_value": row.p_value,
                "observations": row.observations,
            }
        )
    entries.append(
        {
            "hypothesis": f"ventana operativa de {report.spec.model}",
            "p_value": report.window["raw"]["p_value_19"],
            "observations": report.window["raw"]["observations"],
        }
    )
    entries.append(
        {
            "hypothesis": "efecto acelerador del anclaje",
            "p_value": report.anchor.get("fisher_p_value", 1.0),
            "observations": report.anchor.get("observations", 0),
        }
    )
    adjusted = holm_bonferroni([entry["p_value"] for entry in entries])
    for entry, value in zip(entries, adjusted):
        entry["p_value_holm"] = value
        entry["significant_holm"] = value < 0.05
    return entries


def _claim_checks(
    report: ValidationReport,
    enriched: pd.DataFrame,
    clean: pd.DataFrame,
    spec: StrategySpec,
) -> list[ClaimCheck]:
    """Contraste literal de las afirmaciones del PDF contra los datos."""
    checks: list[ClaimCheck] = []
    zones = {row.zone: row for row in report.zones_pooled}

    low = zones.get("<5%")
    if low:
        checks.append(
            ClaimCheck(
                name="zona de ruido",
                claim="P<5,0% ⇒ tasa de acierto ~0,0% (descartable)",
                expected=0.0,
                observed=low.accuracy,
                tolerance=0.02,
                status=CONFIRMED if low.accuracy <= 0.02 else NOT_CONFIRMED,
                note=f"{low.hits}/{low.observations} aciertos en la zona de ruido.",
            )
        )

    convergence = zones.get("5-7%")
    if convergence:
        checks.append(
            ClaimCheck(
                name="zona de convergencia global",
                claim="5,0%–7,0% ⇒ 6,46% de acierto global (alta confianza)",
                expected=0.0646,
                observed=convergence.accuracy,
                tolerance=0.005,
                status=(
                    CONFIRMED
                    if abs(convergence.accuracy - 0.0646) <= 0.005
                    else NOT_CONFIRMED
                ),
                note=f"Replicado sobre {convergence.observations} observaciones de la matriz completa.",
            )
        )

    trap = zones.get(">8%")
    if trap:
        checks.append(
            ClaimCheck(
                name="trampa estadística",
                claim="P>8,0% ⇒ ~2,3% de acierto (falso positivo por sobreajuste)",
                expected=0.023,
                observed=trap.accuracy,
                tolerance=0.012,
                status=CONFIRMED if abs(trap.accuracy - 0.023) <= 0.012 else NOT_CONFIRMED,
                note=f"{trap.hits}/{trap.observations} aciertos por encima del 8%.",
            )
        )

    raw_window = report.window["raw"]
    checks.append(
        ClaimCheck(
            name="tasa óptima de M4",
            claim="5,0%<P(M4)≤7,0% ⇒ 13,75% de acierto",
            expected=0.1375,
            observed=raw_window["accuracy"],
            tolerance=0.005,
            status=(
                CONFIRMED if abs(raw_window["accuracy"] - 0.1375) <= 0.005 else NOT_CONFIRMED
            ),
            note=(
                f"Replicado exactamente sobre el log bruto ({raw_window['hits']}/"
                f"{raw_window['observations']}); en el log depurado la tasa es "
                f"{report.window['clean']['accuracy']:.2%} "
                f"({report.window['clean']['hits']}/{report.window['clean']['observations']})."
            ),
        )
    )

    modes = {row["mode"]: row for row in report.primary_modes}
    tie_mode = modes.get("tie", {})
    strict_mode = modes.get("strict", {})
    checks.append(
        ClaimCheck(
            name="aislamiento de modelo",
            claim="la probabilidad principal de la ronda debe emitirla M4",
            expected=1.0,
            observed=float(tie_mode.get("tied_max_inside_window", 0)),
            tolerance=0.0,
            status=(
                NOT_CONFIRMED
                if tie_mode.get("tied_max_inside_window", 0) == 0
                else CONDITIONAL
            ),
            note=(
                "M4 alcanza la probabilidad máxima en "
                f"{tie_mode.get('tied_max_rows', 0)} rondas, pero en ninguna de ellas "
                "P(M4) cae dentro de la ventana 5,0%–7,0%: la intersección literal de "
                "las dos condiciones es vacía y el motor estricto no emite alertas "
                f"(tie={tie_mode.get('alerts', 0)}, strict={strict_mode.get('alerts', 0)})."
            ),
        )
    )

    spread = report.spread_effect
    checks.append(
        ClaimCheck(
            name="filtro de dispersión",
            claim="Δ = Pmax − Pmin ≤ 4,7% o la ronda se descarta",
            expected=None,
            observed=spread.get("accuracy_after"),
            tolerance=0.0,
            status=CONDITIONAL,
            note=(
                f"El filtro descarta {spread.get('discarded', 0)} de "
                f"{spread.get('window_observations', 0)} rondas de la ventana y con ello "
                f"{spread.get('discarded_hits', 0)} aciertos: la tasa baja de "
                f"{spread.get('accuracy_before', 0):.2%} a {spread.get('accuracy_after', 0):.2%}."
            ),
        )
    )

    anchor = report.anchor
    fisher_p = anchor.get("fisher_p_value", 1.0)
    checks.append(
        ClaimCheck(
            name="acelerador de anclaje",
            claim="último número en {1, 7, 8} ⇒ confianza «Crítico» y mayor stake",
            expected=None,
            observed=anchor.get("anchored", {}).get("accuracy"),
            tolerance=0.0,
            status=CONDITIONAL if fisher_p < 0.05 else NOT_CONFIRMED,
            note=(
                f"{anchor.get('anchored', {}).get('hits', 0)}/"
                f"{anchor.get('anchored', {}).get('observations', 0)} aciertos anclados frente a "
                f"{anchor.get('not_anchored', {}).get('hits', 0)}/"
                f"{anchor.get('not_anchored', {}).get('observations', 0)} sin ancla; "
                f"Fisher p={fisher_p:.3f} "
                + (
                    "(diferencia significativa, pero sobre una cohorte mínima)."
                    if fisher_p < 0.05
                    else "(no significativo: no justifica escalar el stake)."
                )
            ),
        )
    )

    results = enriched["result"].dropna().astype(int)
    distinct = int(results.nunique())
    checks.append(
        ClaimCheck(
            name="ventaja base de la casa",
            claim="1/37 ≈ 2,70% es la probabilidad natural de un pleno",
            expected=1.0 / 37.0,
            observed=1.0 / distinct if distinct else float("nan"),
            tolerance=0.0,
            status=NOT_CONFIRMED,
            note=(
                f"El log contiene {distinct} resultados distintos (0..{distinct - 1}); la línea "
                f"base equiprobable del espacio observado es 1/{distinct} = "
                f"{1.0 / distinct:.2%}, no 2,70%. La ventaja real del sistema frente a su "
                "propio nulo es menor que la multiplicada por cinco del PDF."
            ),
        )
    )

    concentration = report.window.get("hit_concentration", {})
    remainder = concentration.get("without_dominant", {})
    if remainder:
        checks.append(
            ClaimCheck(
                name="concentración de aciertos",
                claim="la ventaja de M4 es general, no un artefacto de un número",
                expected=None,
                observed=remainder.get("accuracy"),
                tolerance=0.0,
                status=(
                    NOT_CONFIRMED
                    if remainder.get("p_value_19", 1.0) > 0.05
                    else CONDITIONAL
                ),
                note=(
                    f"El número {concentration.get('dominant_number')} concentra "
                    f"{concentration['by_number'][0]['hits']}/"
                    f"{concentration['by_number'][0]['observations']} aciertos; sin él la tasa cae "
                    f"a {remainder.get('accuracy', 0):.2%} "
                    f"({remainder.get('hits', 0)}/{remainder.get('observations', 0)}), "
                    f"p={remainder.get('p_value_19', 1.0):.3f} frente a 1/19."
                ),
            )
        )

    return checks
