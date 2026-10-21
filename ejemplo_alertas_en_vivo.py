"""Demostración del motor de alertas M4 en tiempo real.

El script recorre el log de supervisión como si las rondas llegaran en vivo:
evalúa cada ronda **antes** de conocer su resultado, cierra el ciclo cuando el
resultado llega y muestra el stake dimensionado por el módulo de Kelly.

Ejecución:

    python .\\ejemplo_alertas_en_vivo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    # La consola de Windows usa cp1252: se fuerza UTF-8 y, si algún carácter
    # no es representable, se sustituye en lugar de abortar la ejecución.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from m4_engine import (
    EUROPEAN_37,
    EXPRESS_19,
    AlertEngine,
    KellyConfig,
    StrategySpec,
    load_supervision_csv,
)
from m4_engine.features import build_features, collapse_conflicts

CSV_PATH = (
    Path(__file__).resolve().parent
    / "registros_supervision_probs_120 - registros_supervision_probs_120.csv"
)


def print_header(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def main() -> None:
    spec = StrategySpec(primary_mode="any")
    frame = load_supervision_csv(CSV_PATH)
    clean = collapse_conflicts(build_features(frame, spec))

    for payout in (EUROPEAN_37, EXPRESS_19):
        print_header(f"Motor de alertas M4 · modelo de pago {payout.name}")
        engine = AlertEngine(
            spec=spec,
            payout=payout,
            kelly_config=KellyConfig(),
            bankroll=1000.0,
        )

        emitted = 0
        for _, row in clean.iterrows():
            record = {
                "draw_id": row["draw_id"],
                "last_number": row["last_number"],
                "numbers": {
                    model: row[f"n_{model.lower()}"]
                    for model in spec.models_in_matrix
                },
                "probabilities": {
                    model: row[f"p_{model.lower()}"]
                    for model in spec.models_in_matrix
                },
            }
            decision = engine.evaluate(record)
            if decision.fired:
                emitted += 1
                stake = decision.stake
                stake_text = (
                    f"stake={stake.stake_fraction:.2%} ({stake.stake_amount:.2f} u) "
                    f"p_usada={stake.rate_used:.2%} equilibrio={stake.break_even:.2%}"
                    if stake and stake.applied
                    else "stake=0 (sin evidencia suficiente)"
                )
                print(
                    f"sorteo {decision.diagnostics.get('draw_id')}: número "
                    f"{decision.number} P(M4)={decision.probability:.2%} "
                    f"Δ={decision.spread:.2%} confianza={decision.confidence} "
                    f"ancla={decision.anchor} -> {stake_text}"
                )
            engine.observe(decision, row["result"])

        print()
        print(f"Alertas emitidas: {emitted}")
        print(
            f"Estimador de la ventana: {engine.window_estimator.hits}/"
            f"{engine.window_estimator.trials}"
        )
        print(
            f"Estimador de alertas: {engine.alert_estimator.hits}/"
            f"{engine.alert_estimator.trials}"
        )
        hits = sum(1 for entry in engine.history if entry["fired"] and entry["hit"])
        alerts = sum(1 for entry in engine.history if entry["fired"])
        print(f"Aciertos sobre alertas: {hits}/{alerts}")

    print_header("Ronda en vivo (registro suelto, sin CSV)")
    engine = AlertEngine(
        spec=spec,
        payout=EUROPEAN_37,
        kelly_config=KellyConfig(),
        bankroll=1000.0,
    )
    engine.backfill(clean)
    live_record = {
        "draw_id": 249408,
        "last_number": 7,
        "numbers": {
            "M1": 3, "M2": 11, "M3": 16, "M4": 9, "M5": 0,
            "H1": 14, "H2": 5, "H3": 8, "H4": 12, "H5": 17,
        },
        "probabilities": {
            "M1": 0.0640, "M2": 0.0610, "M3": 0.0595, "M4": 0.0570, "M5": 0.0555,
            "H1": 0.0545, "H2": 0.0535, "H3": 0.0530, "H4": 0.0525, "H5": 0.0520,
        },
    }
    decision = engine.evaluate(live_record)
    print(f"dispara={decision.fired} confianza={decision.confidence} "
          f"número={decision.number} P(M4)={decision.probability:.2%} "
          f"Δ={decision.spread:.2%} ancla={decision.anchor}")
    if decision.fired and decision.stake:
        print(
            f"stake={decision.stake.stake_fraction:.2%} "
            f"({decision.stake.stake_amount:.2f} u) "
            f"tasa usada={decision.stake.rate_used:.2%} "
            f"equilibrio={decision.stake.break_even:.2%}"
        )
    else:
        for failure in decision.failures:
            print(f"motivo de rechazo: {failure}")

    print_header("Ronda con datos incompletos")
    decision = engine.evaluate({"draw_id": 249409, "last_number": 2, "numbers": {"M4": 4}})
    print(f"dispara={decision.fired} motivo={decision.failures[0]}")


if __name__ == "__main__":
    main()
