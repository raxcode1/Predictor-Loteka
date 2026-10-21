"""Configuración operativa del monitor en vivo y de la alerta M4.

Este archivo es el único punto de edición para los parámetros de operación del
monitor: qué modelo de pago se usa, cuánto capital se toma como referencia,
qué ventana de probabilidad se vigila y cuántas rondas se guardan.

Los umbrales por defecto reproducen la «Especificación de Estrategia
Cuantitativa» con la corrección que impose el espacio real de resultados
(0..18): la línea base de acierto es 1/19 = 5,26% y no 1/37 = 2,70%.
"""

from __future__ import annotations

from m4_engine.config import EUROPEAN_37, EXPRESS_19, StrategySpec

#: Capital de referencia, en unidades, para dimensionar el stake del alert engine.
BANCO_INICIAL = 1000.0

#: Modelo de pago del stake: 'express19' (0..18, 18:1) o 'european37' (PDF).
MODELO_PAGO = "express19"

#: Lectura del aislamiento de modelo: 'any' | 'tie' | 'strict'.
MODO_PRIMARIO = "any"

#: Ventana operativa de probabilidad de M4 (5,0% < P(M4) <= 7,0%).
LIMITE_INFERIOR = 0.05
LIMITE_SUPERIOR = 0.07

#: Dispersión máxima admitida entre el modelo más y el menos probable.
DISPERSION_MAXIMA = 0.047

#: Números de anclaje (factor acelerador de la especificación).
NUMEROS_ANCLA = (1, 7, 8)

#: Rondas retroanalizadas la primera vez que se crea el registro.
RONDAS_BACKFILL = 300

#: Rondas que se conservan en el registro (siempre las más recientes).
RONDAS_GUARDADAS = 300

#: Archivo local donde se guarda el registro.
ARCHIVO_REGISTRO = "registro_live.json"

#: Stake: fracción de Kelly, tope de exposición y mínimos de evidencia.
FRACCION_KELLY = 0.25
TOPE_EXPOSICION = 0.02
OBSERVACIONES_MINIMAS = 20
ACIERTO_MINIMO = 4

#: Modelos de pago disponibles para el dimensionamiento del stake.
MODELOS_PAGO = {
    "express19": EXPRESS_19,
    "european37": EUROPEAN_37,
}


def modelo_pago():
    """Devuelve el `PayoutModel` configurado para el stake."""
    return MODELOS_PAGO[MODELO_PAGO]


def spec() -> StrategySpec:
    """Devuelve la `StrategySpec` operativa del monitor en vivo."""
    return StrategySpec(
        probability_min=LIMITE_INFERIOR,
        probability_max=LIMITE_SUPERIOR,
        max_spread=DISPERSION_MAXIMA,
        anchor_numbers=frozenset(NUMEROS_ANCLA),
        primary_mode=MODO_PRIMARIO,
    )
