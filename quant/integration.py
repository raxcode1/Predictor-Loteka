"""Integración del pipeline cuantitativo con los servidores existentes.

`attach(payload, rows)` añade al payload del dashboard (sin eliminar ni
renombrar campos existentes):

- `api_version`  (2),
- `quant`        resumen del informe (o None),
- `quant_error`  error explícito si el informe no se pudo calcular,
- `decision`     decisión auditable (SIN VENTAJA por defecto),
- `m4_gate`      evidencia corregida por multiplicidad de la alerta M4,
- `trading_mode` `paper` salvo configuración explícita.

Además aplica la puerta de señales a `alert`: si la evidencia no la respalda,
`fired` pasa a False (la evaluación original queda en `rule_fired`), el stake
se anula y `state` es «SIN VENTAJA». Así un cliente antiguo que sólo mire
`fired` nunca ve una señal de apuesta no respaldada.

Las dependencias científicas (scipy, scikit-learn) se importan de forma
diferida: si no están disponibles en un despliegue, la API sigue respondiendo
con la decisión segura (SIN VENTAJA, señal bloqueada) y el error explícito.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable, Mapping
from typing import Any

from . import PIPELINE_VERSION
from .logs import get_logger

API_VERSION = 2
STATUS_NO_EDGE = "SIN VENTAJA"
MODE_PAPER = "paper"
MODE_LIVE = "live"

log = get_logger("quant.integration")


def trading_mode(env: Mapping[str, str] | None = None) -> str:
    value = (env if env is not None else os.environ).get("RULETA_TRADING_MODE", MODE_PAPER)
    return MODE_LIVE if str(value).strip().lower() == MODE_LIVE else MODE_PAPER


def fallback_decision(reason: str, mode: str) -> dict[str, Any]:
    return {
        "status": STATUS_NO_EDGE,
        "trading_mode": mode,
        "signals_allowed": False,
        "recommended_stake_fraction": 0.0,
        "abstain": True,
        "pipeline_version": PIPELINE_VERSION,
        "reasons": [reason],
        "checks": [],
    }


def analysis_for_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    started = time.monotonic()
    try:
        from .data import from_rows
        from .report import cached_summary

        dataset = from_rows(rows)
        summary = cached_summary(dataset)
    except Exception as error:  # noqa: BLE001 - se devuelve como error explícito en el payload
        log.error("quant_analysis_failed", extra={"error": str(error), "error_type": type(error).__name__})
        return None, f"{type(error).__name__}: {error}"
    log.info(
        "quant_analysis_ready",
        extra={"sha256": summary["dataset"]["sha256"], "elapsed_seconds": round(time.monotonic() - started, 3)},
    )
    return summary, None


def _m4_gate(alert_stats: Mapping[str, Any] | None) -> dict[str, Any]:
    try:
        from .decision import m4_gate
    except ImportError as error:
        return {"passed": False, "cohorts": [], "reasons": [f"puerta M4 no disponible: {error}"],
                "anchor_scales_stake": False}
    return m4_gate(alert_stats)


def gate_alert(alert: Mapping[str, Any] | None, decision: Mapping[str, Any], gate: Mapping[str, Any]) -> dict[str, Any] | None:
    if alert is None:
        return None
    gated = dict(alert)
    allowed = decision.get("trading_mode") == MODE_LIVE and bool(gate.get("passed"))
    gated["rule_fired"] = bool(alert.get("fired"))
    gated["signal_allowed"] = allowed
    if allowed:
        return gated
    reasons = list(gate.get("reasons") or [])
    if decision.get("trading_mode") != MODE_LIVE:
        reasons.append("modo paper-trading")
    gated.update(
        {
            "fired": False,
            "state": STATUS_NO_EDGE,
            "signal_blocked": True,
            "block_reasons": reasons,
            "stake_applied": False,
            "stake_fraction": 0.0,
            "stake_amount": 0.0,
        }
    )
    return gated


def attach(payload: dict[str, Any], rows: Iterable[Mapping[str, Any]] | None = None,
           summary: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
    """Añade análisis, decisión y puerta de señales al payload del dashboard."""
    if summary is None and error is None:
        if rows is not None:
            summary, error = analysis_for_rows(rows)
        else:
            error = "informe cuantitativo no calculado"
    mode = trading_mode()
    if summary is not None:
        decision = dict(summary["decision"])
        # El modo se resuelve en cada petición (el informe puede estar cacheado).
        decision["trading_mode"] = mode
        if mode != MODE_LIVE:
            decision["signals_allowed"] = False
            decision["recommended_stake_fraction"] = 0.0
            decision["abstain"] = True
    else:
        decision = fallback_decision(f"informe cuantitativo no disponible: {error}", mode)
    gate = _m4_gate(payload.get("alert_stats"))
    payload["api_version"] = API_VERSION
    payload["quant"] = summary
    payload["quant_error"] = error
    payload["decision"] = decision
    payload["m4_gate"] = gate
    payload["trading_mode"] = mode
    payload["alert"] = gate_alert(payload.get("alert"), decision, gate)
    return payload
