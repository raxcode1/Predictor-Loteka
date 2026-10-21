"""Motor cuantitativo M4 para Ruleta Express.

El paquete implementa la «Especificación de Estrategia Cuantitativa» (PDF) como
código verificable:

- `config`: parámetros de la estrategia, modelos de pago y configuración de Kelly.
- `io`: lectura tolerante del CSV de supervisión y normalización de registros
  en tiempo real.
- `features`: ingeniería de variables (spread, zona, ancla, modelo primario).
- `integrity`: auditoría de integridad del log de supervisión.
- `stats`: utilidades estadísticas sin dependencias externas (scipy).
- `validation`: validación empírica de los hallazgos del PDF.
- `alerts`: motor de alertas de alta ventaja (OOP) con cierre de ciclo.
- `kelly`: módulo de stake dinámico con variante defensiva del Criterio de Kelly.
- `report`: generación del informe en Markdown.
"""

from .config import (
    ANCHOR_NUMBERS,
    EUROPEAN_37,
    EXPRESS_19,
    MODEL_ORDER,
    KellyConfig,
    PayoutModel,
    StrategySpec,
)
from .alerts import AlertDecision, AlertEngine
from .kelly import KellyStakeSizer, StakeDecision
from .io import load_supervision_csv, normalize_record
from .validation import ValidationReport, validate_strategy

__all__ = [
    "ANCHOR_NUMBERS",
    "EUROPEAN_37",
    "EXPRESS_19",
    "MODEL_ORDER",
    "AlertDecision",
    "AlertEngine",
    "KellyConfig",
    "KellyStakeSizer",
    "PayoutModel",
    "StakeDecision",
    "StrategySpec",
    "ValidationReport",
    "load_supervision_csv",
    "normalize_record",
    "validate_strategy",
]
