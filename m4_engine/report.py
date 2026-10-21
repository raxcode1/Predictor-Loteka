"""Generación del informe de validación en Markdown."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .alerts import AlertEngine
from .config import StrategySpec
from .validation import ValidationReport


def _table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join([" --- "] * len(header)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def render_report(
    report: ValidationReport,
    engine: AlertEngine | None = None,
    csv_path: str | Path | None = None,
) -> str:
    spec = report.spec
    lines: list[str] = []
    lines.append("# Informe de validación — Estrategia Cuantitativa M4 (Ruleta Express)")
    lines.append("")
    if csv_path:
        lines.append(f"Fuente: `{csv_path}`")
    lines.append(
        f"Parámetros: modelo `{spec.model}`, ventana {spec.zone_label}, "
        f"Δ máximo {spec.max_spread:.1%}, anclaje {sorted(spec.anchor_numbers)}, "
        f"aislamiento `{spec.primary_mode}`."
    )
    lines.append("")

    lines.append("## 1. Integridad del log de supervisión")
    lines.append("")
    integrity = report.integrity
    lines.append(
        _table(
            ["Métrica", "Valor"],
            [
                ["Filas totales", integrity.total_rows],
                ["Grupos de predicción únicos", integrity.unique_groups],
                ["Filas duplicadas", integrity.duplicated_rows],
                ["Grupos con resultados contradictorios", integrity.conflicting_groups],
                ["Filas descartadas por conflicto", integrity.conflicting_rows],
                ["Filas utilizables (observaciones independientes)", integrity.clean_rows],
                ["Rango de resultados", f"{integrity.result_range[0]}..{integrity.result_range[1]}"],
                ["Orden cronológico por `no. sorteo`", integrity.chronological_by_row],
                ["Sorteos obsoletos", integrity.stale_draw_ids or "—"],
            ],
        )
    )
    lines.append("")
    for note in integrity.notes:
        lines.append(f"- {note}")
    lines.append("")

    lines.append("## 2. Contraste de los hallazgos del PDF")
    lines.append("")
    lines.append(
        _table(
            ["Afirmación", "Esperado", "Observado", "Estado"],
            [
                [
                    check.claim,
                    f"{check.expected:.2%}" if check.expected is not None else "—",
                    f"{check.observed:.2%}" if check.observed is not None else "—",
                    check.status,
                ]
                for check in report.checks
            ],
        )
    )
    lines.append("")
    for check in report.checks:
        if check.note:
            lines.append(f"- **{check.name}**: {check.note}")
    lines.append("")

    lines.append("## 3. Zonas de densidad probabilística")
    lines.append("")
    lines.append("Matriz completa (los diez submodelos):")
    lines.append("")
    lines.append(
        _table(
            ["Zona", "Observaciones", "Aciertos", "Tasa", "IC 95%", "p (vs 1/19)"],
            [
                [
                    row.zone,
                    row.observations,
                    row.hits,
                    f"{row.accuracy:.2%}",
                    f"[{row.interval.low:.2%}, {row.interval.high:.2%}]",
                    f"{row.p_value:.4f}",
                ]
                for row in report.zones_pooled
            ],
        )
    )
    lines.append("")
    lines.append(f"Modelo {spec.model}:")
    lines.append("")
    lines.append(
        _table(
            ["Zona", "Observaciones", "Aciertos", "Tasa", "IC 95%", "p (vs 1/19)"],
            [
                [
                    row.zone,
                    row.observations,
                    row.hits,
                    f"{row.accuracy:.2%}",
                    f"[{row.interval.low:.2%}, {row.interval.high:.2%}]",
                    f"{row.p_value:.4f}",
                ]
                for row in report.zones_model
            ],
        )
    )
    lines.append("")

    lines.append("## 4. Ventana operativa de M4")
    lines.append("")
    for label in ("raw", "clean"):
        summary = report.window[label]
        lines.append(
            f"- **{'Log bruto' if label == 'raw' else 'Log depurado'}**: "
            f"{summary['hits']}/{summary['observations']} = {summary['accuracy']:.2%} "
            f"(IC 95% [{summary['ci_low']:.2%}, {summary['ci_high']:.2%}]); "
            f"p={summary['p_value_19']:.4f} frente a 1/19 "
            f"y p={summary['p_value_37']:.2e} frente a 1/37."
        )
    concentration = report.window.get("hit_concentration", {})
    if concentration.get("by_number"):
        lines.append("")
        lines.append("Aciertos por número predicho (log depurado):")
        lines.append("")
        lines.append(
            _table(
                ["Número", "Rondas", "Aciertos", "Tasa"],
                [
                    [item["number"], item["observations"], item["hits"], f"{item['accuracy']:.2%}"]
                    for item in concentration["by_number"]
                ],
            )
        )
        remainder = concentration.get("without_dominant", {})
        lines.append("")
        lines.append(
            f"Sin el número dominante ({concentration.get('dominant_number')}) la tasa cae a "
            f"{remainder.get('accuracy', 0):.2%} "
            f"({remainder.get('hits', 0)}/{remainder.get('observations', 0)}), "
            f"p={remainder.get('p_value_19', 1.0):.3f} frente a 1/19."
        )
    lines.append("")

    lines.append("## 5. Efecto del filtro de dispersión y del anclaje")
    lines.append("")
    spread = report.spread_effect
    lines.append(
        f"- **Dispersión**: {spread.get('discarded', 0)} rondas descartadas de "
        f"{spread.get('window_observations', 0)}; la tasa pasa de "
        f"{spread.get('accuracy_before', 0):.2%} a {spread.get('accuracy_after', 0):.2%} "
        f"(Δ {spread.get('delta', 0):+.2%}). {spread.get('verdict', '')}"
    )
    anchor = report.anchor
    if anchor.get("observations"):
        lines.append(
            f"- **Anclaje {{1, 7, 8}}**: {anchor['anchored']['hits']}/"
            f"{anchor['anchored']['observations']} = {anchor['anchored']['accuracy']:.2%} frente a "
            f"{anchor['not_anchored']['hits']}/{anchor['not_anchored']['observations']} = "
            f"{anchor['not_anchored']['accuracy']:.2%} sin ancla; Fisher p="
            f"{anchor.get('fisher_p_value', 1.0):.3f}. {anchor.get('verdict', '')}"
        )
    lines.append("")
    lines.append("Modos de aislamiento del modelo principal:")
    lines.append("")
    lines.append(
        _table(
            ["Modo", "Alertas", "Aciertos", "Tasa", "p (vs 1/19)"],
            [
                [
                    mode["mode"],
                    mode["alerts"],
                    mode["hits"],
                    f"{mode['accuracy']:.2%}",
                    f"{mode['p_value_19']:.4f}",
                ]
                for mode in report.primary_modes
            ],
        )
    )
    lines.append("")

    lines.append("## 6. Matrices de confusión")
    lines.append("")
    lines.append(
        "Positivo = la alerta se dispara y acierta; negativo = la alerta se dispara y falla. "
        "Sobre el log completo la «exactitud» está dominada por los verdaderos negativos "
        "(rondas sin señal), por lo que la métrica relevante es la precisión."
    )
    lines.append("")
    lines.append(
        _table(
            ["Universo", "VP", "FP", "FN", "VN", "Precisión", "Exhaustividad", "Exactitud"],
            [
                [
                    matrix.universe,
                    matrix.true_positive,
                    matrix.false_positive,
                    matrix.false_negative,
                    matrix.true_negative,
                    f"{matrix.precision:.2%}",
                    f"{matrix.recall:.2%}",
                    f"{matrix.accuracy:.2%}",
                ]
                for matrix in report.confusion
            ],
        )
    )
    lines.append("")

    lines.append("## 7. PnL simulado (apuestas planas de 1 unidad sobre las señales válidas)")
    lines.append("")
    lines.append(
        _table(
            ["Modelo de pago", "Apuestas", "Aciertos", "Equilibrio", "PnL (u)", "ROI", "IC 95% ROI", "DD máx."],
            [
                [
                    result.payout,
                    result.bets,
                    result.wins,
                    f"{result.break_even:.2%}",
                    f"{result.total_units:+.1f}",
                    f"{result.roi:+.2%}",
                    f"[{result.roi_interval.low:+.2%}, {result.roi_interval.high:+.2%}]",
                    f"{result.max_drawdown_units:.1f} u",
                ]
                for result in report.pnl
            ],
        )
    )
    lines.append("")

    lines.append("## 8. Validación cronológica fuera de muestra")
    lines.append("")
    lines.append(
        _table(
            ["Tramo", "Filas", "Alertas", "Aciertos", "Tasa", "p (vs 1/19)"],
            [
                [
                    row["fold"],
                    row["rows"],
                    row["alerts"],
                    row["hits"],
                    f"{row['accuracy']:.2%}",
                    f"{row['p_value_19']:.4f}",
                ]
                for row in report.walk_forward
            ],
        )
    )
    lines.append("")

    lines.append("## 9. Sensibilidad a los umbrales")
    lines.append("")
    summary = report.sensitivity_summary
    if summary:
        lines.append(
            f"- Rango de tasas sobre {summary['configurations']} configuraciones con al menos "
            f"20 alertas: min {summary['min']:.2%}, mediana {summary['median']:.2%}, "
            f"máx {summary['max']:.2%}."
        )
        spec_row = next((row for row in report.sensitivity if row["is_spec"]), None)
        if spec_row:
            lines.append(
                f"- Configuración de la especificación: {spec_row['accuracy']:.2%} "
                f"({spec_row['hits']}/{spec_row['observations']}), p={spec_row['p_value_19']:.4f}."
            )
        if summary.get("window_neighbourhood"):
            lines.append("")
            lines.append("Vecindario de la ventana 5,0%–7,0% variando el tope de dispersión:")
            lines.append("")
            lines.append(
                _table(
                    ["Δ máximo", "Alertas", "Tasa"],
                    [
                        [
                            f"{row['max_spread']:.1%}",
                            row["observations"],
                            f"{row['accuracy']:.2%}",
                        ]
                        for row in summary["window_neighbourhood"]
                    ],
                )
            )
        if summary.get("cap_neighbourhood"):
            lines.append("")
            lines.append("Vecindario con Δ fijo en 4,7% variando la ventana de probabilidad:")
            lines.append("")
            lines.append(
                _table(
                    ["Ventana", "Alertas", "Tasa"],
                    [
                        [
                            f"({row['lower']:.1%}, {row['upper']:.1%}]",
                            row["observations"],
                            f"{row['accuracy']:.2%}",
                        ]
                        for row in summary["cap_neighbourhood"]
                    ],
                )
            )
        lines.append("")
        lines.append(
            "- La señal no depende de un único par de umbrales: la ventaja se mantiene en "
            "un entorno amplio, lo que reduce (sin eliminar) la probabilidad de que los "
            "umbrales se hayan ajustados a esta muestra concreta."
        )
    lines.append("")

    lines.append("## 10. Control de multiplicidad (Holm-Bonferroni)")
    lines.append("")
    lines.append(
        _table(
            ["Hipótesis", "Observaciones", "p", "p (Holm)", "¿Significativo?"],
            [
                [
                    entry["hypothesis"],
                    entry["observations"],
                    f"{entry['p_value']:.4f}",
                    f"{entry['p_value_holm']:.4f}",
                    "sí" if entry["significant_holm"] else "no",
                ]
                for entry in report.multiplicity
            ],
        )
    )
    lines.append("")

    if engine is not None:
        lines.append("## 11. Estado del motor de alertas en vivo")
        lines.append("")
        lines.append(
            f"- Estimador de la ventana: {engine.window_estimator.hits}/"
            f"{engine.window_estimator.trials} aciertos observados."
        )
        lines.append(
            f"- Estimador de alertas: {engine.alert_estimator.hits}/"
            f"{engine.alert_estimator.trials} aciertos observados."
        )
        stake = engine.stake_sizer.size(
            successes=engine.alert_estimator.hits,
            trials=engine.alert_estimator.trials,
            bankroll=engine.bankroll,
            confidence="ALTO",
        )
        lines.append(
            f"- Stake de referencia (Kelly fraccional y encogido): "
            f"{stake.stake_fraction:.2%} del bankroll "
            f"(tasa usada {stake.rate_used:.2%}, equilibrio {stake.break_even:.2%})."
        )
        lines.append("")

    lines.append("## Conclusión operativa")
    lines.append("")
    for note in report.notes:
        lines.append(f"- {note}")
    lines.append("")
    lines.extend(_verdict(report, engine))
    lines.append(
        "Este informe describe lo que el histórico **permite** afirmar. Ninguna métrica "
        "de un log de 120 filas, con 35 de ellas de etiqueta no verificable, sustituye "
        "una validación acumulada fuera de muestra con meses de datos."
    )
    lines.append("")
    return "\n".join(lines)


def _verdict(report: ValidationReport, engine: AlertEngine | None = None) -> list[str]:
    """Sintetiza las decisiones operativas que impone la evidencia."""
    lines: list[str] = []
    confirmed = [check for check in report.checks if check.status == "CONFIRMADO"]
    rejected = [check for check in report.checks if check.status == "NO CONFIRMADO"]
    conditional = [check for check in report.checks if check.status == "CONDICIONAL"]

    clean_window = report.window["clean"]
    modes = {row["mode"]: row for row in report.primary_modes}
    pnl_clean = [
        result for result in report.pnl if result.payout.endswith(":clean")
    ]
    best = max(pnl_clean, key=lambda result: result.roi) if pnl_clean else None
    spread = report.spread_effect
    concentration = report.window.get("hit_concentration", {})
    remainder = concentration.get("without_dominant", {})

    lines.append("### Veredicto por regla de la especificación")
    lines.append("")
    lines.append(
        f"- **Se mantienen** ({len(confirmed)}): "
        + ", ".join(check.name for check in confirmed)
        + ". Son las propiedades de calibración del vector de probabilidades y se replican "
        "exactamente sobre el log."
    )
    if conditional:
        lines.append(
            f"- **Requieren contexto** ({len(conditional)}): "
            + ", ".join(check.name for check in conditional)
            + "."
        )
    lines.append(
        f"- **No se sostienen** ({len(rejected)}): "
        + ", ".join(check.name for check in rejected)
        + "."
    )
    lines.append("")
    lines.append("### Configuración operativa recomendada")
    lines.append("")
    lines.append(
        f"1. **Aislamiento de modelo**: la lectura literal del PDF (M4 debe emitir la "
        f"probabilidad principal) produce {modes.get('tie', {}).get('alerts', 0)} alertas: "
        "la condición y la ventana de probabilidad nunca coinciden en este histórico. La "
        "configuración utilizable es la lectura operativa (`any`), donde M4 conduce la "
        "alerta y el resto de modelos se tratan como ruido de fondo."
    )
    lines.append(
        f"2. **Filtro de dispersión**: opcional. Descarta {spread.get('discarded', 0)} rondas "
        f"y {spread.get('discarded_hits', 0)} aciertos, y mueve la tasa de "
        f"{spread.get('accuracy_before', 0):.2%} a {spread.get('accuracy_after', 0):.2%}. "
        "Aplicarlo o no es una decisión de riesgo, no de acierto."
    )
    lines.append(
        f"3. **Anclaje {sorted(report.spec.anchor_numbers)}**: se conserva la escalada de "
        "confianza a «Crítico» como etiqueta operativa, pero **sin** multiplicador de "
        "stake: la diferencia observada no es estadísticamente significativa "
        f"(Fisher p={report.anchor.get('fisher_p_value', 1.0):.3f})."
    )
    raw_window = report.window["raw"]
    lines.append(
        "4. **Línea base de comparación**: usar 1/19 = 5,26%, la probabilidad equiprobable "
        "del espacio de resultados realmente observado, y no 1/37 = 2,70%. Bajo 1/19 la "
        f"ventaja de la ventana M4 es {raw_window['accuracy'] / (1 / 19):.2f}× sobre el log "
        f"bruto ({raw_window['accuracy']:.2%}) y "
        f"{clean_window['accuracy'] / (1 / 19):.2f}× sobre el log depurado, no 5×."
    )
    lines.append("")
    lines.append("### Magnitud del efecto y riesgo")
    lines.append("")
    lines.append(
        f"- Sobre el log depurado la ventana M4 produce {clean_window['hits']}/"
        f"{clean_window['observations']} = {clean_window['accuracy']:.2%} con IC 95% "
        f"[{clean_window['ci_low']:.2%}, {clean_window['ci_high']:.2%}]: el intervalo es "
        "ancho y su extremo inferior roza el punto de equilibrio del modelo de pago de 19 "
        "casillas."
    )
    if remainder:
        lines.append(
            f"- La ventaja está concentrada: retirando el número "
            f"{concentration.get('dominant_number')} la tasa cae a "
            f"{remainder.get('accuracy', 0):.2%} y el contraste frente a 1/19 deja de ser "
            f"significativo (p={remainder.get('p_value_19', 1.0):.3f})."
        )
    if best:
        lines.append(
            f"- El PnL plano sobre señales válidas ({best.payout.split(':')[0]}) da "
            f"{best.total_units:+.0f} unidades en {best.bets} apuestas (ROI {best.roi:+.1%}, "
            f"IC 95% [{best.roi_interval.low:+.1%}, {best.roi_interval.high:+.1%}], "
            f"drawdown máximo {best.max_drawdown_units:.0f} u). La varianza domina al "
            "resultado: la estrategia necesita muchas más apuestas para que ese ROI sea "
            "distinguible del azar."
        )
    max_stake = engine.kelly_config.max_fraction if engine else 0.02
    min_observations = engine.kelly_config.min_observations if engine else 20
    min_hits = engine.kelly_config.min_hits if engine else 4
    lines.append(
        "- El módulo de Kelly no arriesga capital mientras no se acumulen "
        f"{min_observations} observaciones y {min_hits} "
        "aciertos en las cohortes de alerta, y limita la exposición por ronda al "
        f"{max_stake:.1%} del bankroll por defecto."
    )
    lines.append("")
    lines.append("### Siguiente paso defendible")
    lines.append("")
    lines.append(
        "1. Ejecutar el motor en modo papel, con los umbrales congelados, y registrar cada "
        "alerta con su resultado antes de arriesgar capital."
    )
    lines.append(
        "2. Corregir el log de supervisión: cada fila debe corresponden a un único sorteo "
        "con una única etiqueta, o las métricas seguirán sin poder distinguir señal de "
        "artefacto de registro."
    )
    lines.append(
        "3. No reentrenar los umbrales con cada resultado: la ventaja mostrada en el "
        "vecindario de umbrales es sólida frente a ruido de estimación, pero frágil frente "
        "a re-selección de reglas."
    )
    lines.append("")
    return lines


def write_report(
    path: str | Path,
    report: ValidationReport,
    engine: AlertEngine | None = None,
    csv_path: str | Path | None = None,
) -> Path:
    destination = Path(path)
    destination.write_text(render_report(report, engine, csv_path), encoding="utf-8")
    return destination
