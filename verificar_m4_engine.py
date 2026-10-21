"""Verificación del motor M4 sin dependencias de test externas.

El proyecto no declara `pytest`, así que las comprobaciones se ejecutan como un
script autónomo con `assert`. Devuelve código de salida 1 si algo falla.

    python .\\verificar_m4_engine.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from m4_engine import (
    EUROPEAN_37,
    EXPRESS_19,
    AlertEngine,
    KellyConfig,
    KellyStakeSizer,
    PayoutModel,
    StrategySpec,
    load_supervision_csv,
    normalize_record,
)
from m4_engine.features import build_features, collapse_conflicts
from m4_engine.stats import (
    binomial_survival,
    fisher_exact_two_sided,
    holm_bonferroni,
    wilson_interval,
)
from m4_engine.validation import validate_strategy

CSV_PATH = (
    Path(__file__).resolve().parent
    / "registros_supervision_probs_120 - registros_supervision_probs_120.csv"
)

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  OK   {name}")
    else:
        FAILED.append(name)
        print(f"  FALLA {name} {detail}")


def approx(value: float, expected: float, tolerance: float = 1e-9) -> bool:
    return abs(value - expected) <= tolerance


def section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def main() -> int:
    frame = load_supervision_csv(CSV_PATH)
    spec = StrategySpec()
    enriched = build_features(frame, spec)
    clean = collapse_conflicts(enriched)
    report = validate_strategy(frame, spec)

    section("1. Carga del CSV de supervisión")
    check("120 filas", len(frame) == 120, f"len={len(frame)}")
    check(
        "19 resultados distintos",
        frame["result"].nunique() == 19,
        f"n={frame['result'].nunique()}",
    )
    check("sin celdas vacías", int(frame.isna().sum().sum()) == 0)
    check(
        "probabilidades en fracción",
        0.0 <= float(frame["p_m4"].min()) and float(frame["p_m4"].max()) <= 1.0,
    )
    check(
        "último número encadenado con el resultado anterior",
        int((frame["result"].shift(1) == frame["last_number"]).sum()) == len(frame) - 1,
    )

    section("2. Reproducción de los hallazgos del PDF")
    zones = {row.zone: row for row in report.zones_pooled}
    check(
        "zona 5-7% global = 6,46%",
        approx(zones["5-7%"].accuracy, 0.0646, 0.0001),
        f"observado={zones['5-7%'].accuracy:.4%}",
    )
    check(
        "zona <5% global = 0%",
        approx(zones["<5%"].accuracy, 0.0, 1e-9),
        f"observado={zones['<5%'].accuracy:.4%}",
    )
    check(
        "zona >8% global ≈ 2,3%",
        abs(zones[">8%"].accuracy - 0.023) <= 0.012,
        f"observado={zones['>8%'].accuracy:.4%}",
    )
    check(
        "ventana M4 = 13,75%",
        approx(report.window["raw"]["accuracy"], 0.1375, 1e-9),
        f"observado={report.window['raw']['accuracy']:.4%}",
    )
    check(
        "M4 alcanza la probabilidad máxima en 6 rondas y ninguna dentro de la ventana",
        report.primary_modes[1]["tied_max_inside_window"] == 0,
    )
    check(
        "modo literal (tie) no emite alertas",
        report.primary_modes[1]["alerts"] == 0 and report.primary_modes[2]["alerts"] == 0,
    )
    boundary = enriched[enriched["model_probability"] <= 0.05]
    check(
        "el borde del 5,0% se clasifica igual en features y en la tabla",
        set(boundary["zone_model"]) <= {row.zone for row in report.zones_model}
        and int((enriched["model_probability"] <= 0.05).sum())
        == next(row.observations for row in report.zones_model if row.zone == "<5%"),
        f"filas con P(M4)<=5%: {len(boundary)}",
    )

    section("3. Integridad del log")
    check("86 grupos de predicción únicos", report.integrity.unique_groups == 86)
    check("6 grupos con etiquetas contradictorias", report.integrity.conflicting_groups == 6)
    check("35 filas descartadas por conflicto", report.integrity.conflicting_rows == 35)
    check("80 filas utilizables", report.integrity.clean_rows == 80)
    check("sorteo obsoleto detectado", report.integrity.stale_draw_ids == (248978,))

    section("4. Estadística")
    interval = wilson_interval(5, 10)
    check("Wilson contiene la estimación", interval.low < 0.5 < interval.high)
    check(
        "Wilson con 0 aciertos no excluye el 0",
        wilson_interval(0, 10).low < 1e-12,
        f"low={wilson_interval(0, 10).low}",
    )
    check(
        "cola binomial coherente",
        approx(binomial_survival(11, 80, 1 / 19), 0.0031, 0.0005),
        f"p={binomial_survival(11, 80, 1 / 19):.5f}",
    )
    check(
        "Fisher: proporciones iguales dan p=1",
        approx(fisher_exact_two_sided(10, 10, 10, 10), 1.0, 1e-9),
    )
    check(
        "Fisher: diferencia amplia da p pequeño",
        fisher_exact_two_sided(9, 1, 1, 9) < 0.01,
    )
    check(
        "Fisher: el anclaje no es significativo",
        fisher_exact_two_sided(2, 7, 6, 32) > 0.05,
        f"p={fisher_exact_two_sided(2, 7, 6, 32):.3f}",
    )
    check(
        "Holm-Bonferroni monótono",
        all(
            adjusted >= raw - 1e-12
            for raw, adjusted in zip(
                [0.01, 0.04, 0.03], holm_bonferroni([0.01, 0.04, 0.03])
            )
        ),
    )

    section("5. Módulo de Kelly")
    sizer = KellyStakeSizer(EUROPEAN_37, KellyConfig())
    without_evidence = sizer.size(successes=1, trials=3, bankroll=1000.0)
    check("sin evidencia suficiente el stake es 0", without_evidence.stake_fraction == 0.0)
    check("sin evidencia suficiente no se aplica", not without_evidence.applied)
    losing = sizer.size(successes=4, trials=100, bankroll=1000.0)
    check(
        "sin ventaja defendible el stake es 0",
        losing.stake_fraction == 0.0 and not losing.applied,
        f"stake={losing.stake_fraction:.4%}",
    )
    winning = sizer.size(successes=20, trials=60, bankroll=1000.0)
    check("con ventaja el stake es positivo", winning.stake_fraction > 0.0)
    check(
        "el stake respeta el tope de exposición",
        winning.stake_fraction <= KellyConfig().max_fraction + 1e-12,
        f"stake={winning.stake_fraction:.4%}",
    )
    check("el crecimiento esperado es positivo", winning.expected_log_growth > 0.0)
    check(
        "la tasa usada es conservadora (extremo inferior o encogido)",
        winning.rate_used <= winning.empirical_rate + 1e-12,
    )
    critical = sizer.size(successes=20, trials=60, bankroll=1000.0, confidence="CRITICO")
    check(
        "la confianza crítica escala el stake",
        critical.stake_fraction >= winning.stake_fraction,
    )
    check(
        "el equilibrio del modelo de 19 casillas es 5,26%",
        approx(EXPRESS_19.break_even, 1 / 19, 1e-12),
    )
    check(
        "la ventaja de la casa europea es 2,70%",
        approx(EUROPEAN_37.house_edge, 1 / 37, 1e-12),
    )

    section("6. Motor de alertas")
    engine = AlertEngine(spec=spec, payout=EUROPEAN_37, bankroll=1000.0)
    base_record = {
        "draw_id": 1,
        "last_number": 3,
        "numbers": {
            "M1": 3, "M2": 11, "M3": 16, "M4": 9, "M5": 0,
            "H1": 14, "H2": 5, "H3": 8, "H4": 12, "H5": 17,
        },
        "probabilities": {
            "M1": 0.0640, "M2": 0.0610, "M3": 0.0595, "M4": 0.0570, "M5": 0.0555,
            "H1": 0.0545, "H2": 0.0535, "H3": 0.0530, "H4": 0.0525, "H5": 0.0520,
        },
    }
    decision = engine.evaluate(base_record)
    check("una ronda en la ventana dispara la alerta", decision.fired)
    check("el número de la alerta es el de M4", decision.number == 9)
    check("la confianza base es ALTO", decision.confidence == "ALTO")
    check("sin ancla no hay escalada", not decision.anchor)

    anchored = dict(base_record)
    anchored["last_number"] = 7
    decision_anchor = engine.evaluate(anchored)
    check("con ancla la confianza es CRITICO", decision_anchor.confidence == "CRITICO")

    outside = dict(base_record)
    outside["probabilities"] = dict(base_record["probabilities"])
    outside["probabilities"]["M4"] = 0.0900
    decision_outside = engine.evaluate(outside)
    check("fuera de la ventana no dispara", not decision_outside.fired)
    check(
        "el motivo es la ventana de probabilidad",
        any("ventana operativa" in reason for reason in decision_outside.failures),
        str(decision_outside.failures),
    )

    wide = dict(base_record)
    wide["probabilities"] = dict(base_record["probabilities"])
    wide["probabilities"]["M5"] = 0.0100
    decision_wide = engine.evaluate(wide)
    check("un spread alto no dispara", not decision_wide.fired)
    check(
        "el motivo es la dispersión",
        any("spread" in reason for reason in decision_wide.failures),
        str(decision_wide.failures),
    )

    incomplete = engine.evaluate({"draw_id": 2, "last_number": 1, "numbers": {"M4": 4}})
    check("datos incompletos no disparan", not incomplete.fired)
    check(
        "el motivo son los datos incompletos",
        any("datos incompletos" in reason for reason in incomplete.failures),
    )

    strict_engine = AlertEngine(spec=StrategySpec(primary_mode="strict"), payout=EUROPEAN_37)
    check(
        "el modo estricto exige que M4 sea el máximo",
        not strict_engine.evaluate(base_record).fired,
    )

    section("7. Ausencia de look-ahead")
    live = AlertEngine(spec=spec, payout=EUROPEAN_37, bankroll=1000.0)
    before = live.alert_estimator.trials
    decision = live.evaluate(base_record)
    check("evaluar no toca el estimador", live.alert_estimator.trials == before)
    live.observe(decision, 9)
    check("observar el acierto actualiza el estimador", live.alert_estimator.trials == before + 1)
    check("el acierto queda registrado", live.alert_estimator.hits == 1)
    live.observe(decision, 4)
    check("el fallo queda registrado", live.alert_estimator.hits == 1)

    section("8. Coherencia de las métricas del informe")
    matrix = next(item for item in report.confusion if item.universe == "log depurado")
    alerts_clean = int(clean["alert"].sum())
    hits_clean = int(clean["hit_model"].sum())
    check(
        "VP + FP = alertas emitidas",
        matrix.true_positive + matrix.false_positive == alerts_clean,
        f"{matrix.true_positive}+{matrix.false_positive} vs {alerts_clean}",
    )
    check(
        "VP + FN = aciertos de M4 en el log depurado",
        matrix.true_positive + matrix.false_negative == hits_clean,
        f"{matrix.true_positive}+{matrix.false_negative} vs {hits_clean}",
    )
    pnl_clean = next(item for item in report.pnl if item.payout == "european37:clean")
    check("43 apuestas sobre señales válidas", pnl_clean.bets == 43)
    check(
        "PnL coherente con 6 aciertos a 35:1",
        approx(pnl_clean.total_units, 6 * 35 - 37, 1e-9),
        f"pnl={pnl_clean.total_units}",
    )
    check(
        "el ROI coincide con el PnL",
        approx(pnl_clean.roi, pnl_clean.total_units / pnl_clean.bets, 1e-12),
    )

    section("9. Normalización de registros en tiempo real")
    normalized = normalize_record(
        {
            "M4": 12,
            "prob.M4": "6,30%",
            "numbers": {"M1": 1},
            "probabilities": {"M1": 0.05},
            "último número": 7,
        }
    )
    check("número normalizado", normalized["numbers"]["M4"] == 12)
    check("probabilidad normalizada", abs(normalized["probabilities"]["M4"] - 0.063) < 1e-9)
    check("último número normalizado", normalized["last_number"] == 7)

    print()
    print(f"Comprobaciones superadas: {PASSED}")
    if FAILED:
        print(f"Comprobaciones fallidas: {len(FAILED)}")
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("Todas las comprobaciones han pasado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
