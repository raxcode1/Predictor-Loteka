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

#: Pago real de Loteka: 180 pesos en total por cada 10 apostados (incluye la
#: apuesta), es decir, 17 a 1 neto. Break-even = 10 / 180 = 5,56 %.
NET_MULTIPLIER = 17.0
BREAK_EVEN = 1.0 / (1.0 + NET_MULTIPLIER)
#: Apuesta mínima admitida por Loteka (pesos).
MIN_BET = 10.0

__all__ = ["BASELINE", "BREAK_EVEN", "MIN_BET", "NET_MULTIPLIER", "OUTPUT_MODULUS", "PIPELINE_VERSION"]
