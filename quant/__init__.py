"""Pipeline cuantitativo auditable para Ruleta Express.

Separación de responsabilidades:

- `data`        ingesta y normalización (CSV/filas -> serie cronológica).
- `models`      modelos predictivos secuenciales (uniforme, Dirichlet, Markov).
- `bayes`       posteriores Beta-Binomial / Dirichlet-Multinomial y Bayes factors.
- `validation`  walk-forward temporal sin look-ahead, métricas y calibración.
- `goodness`    batería de bondad de ajuste / independencia + multiplicidad.
- `montecarlo`  simulaciones con semilla bajo la nula y bajo el posterior.
- `prng`        laboratorio de identificación LCG / MT19937 (datos públicos).
- `decision`    reglas de abstención («SIN VENTAJA») y modo paper-trading.
- `report`      ensamblado del informe reproducible (JSON / Markdown).

Hipótesis nula operativa: los resultados son i.i.d. uniformes en 0..18. Si la
evidencia fuera de muestra no la rechaza de forma robusta, el sistema responde
«SIN VENTAJA» y no emite señales de apuesta.
"""

from __future__ import annotations

PIPELINE_VERSION = "quant-1.0.0"
OUTPUT_MODULUS = 19
BASELINE = 1.0 / OUTPUT_MODULUS

__all__ = ["BASELINE", "OUTPUT_MODULUS", "PIPELINE_VERSION"]
