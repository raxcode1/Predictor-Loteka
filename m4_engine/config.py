"""Parámetros de la estrategia cuantitativa M4.

Todos los umbrales del PDF están centralizados aquí y son configurables, de
modo que cualquier variación se pueda medir (análisis de sensibilidad) sin
tocar el motor de alertas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

MODEL_ORDER: tuple[str, ...] = ("M1", "M2", "M3", "M4", "M5", "H1", "H2", "H3", "H4", "H5")
PRIMARY_MODEL: str = "M4"
ANCHOR_NUMBERS: frozenset[int] = frozenset({1, 7, 8})

PRIMARY_MODES: tuple[str, ...] = ("any", "tie", "strict")


@dataclass(frozen=True)
class PayoutModel:
    """Estructura de pago de una apuesta a un número plano.

    `pockets` es el número de casillas del generador (37 en la ruleta europea
    que asume el PDF, 19 en el espacio observado 0..18) y `net_multiplier`
    es el multiplicador de ganancia neta por unidad apostada (35:1 sobre 37
    casillas, 18:1 sobre 19 casillas justas).
    """

    name: str
    pockets: int
    net_multiplier: float

    @property
    def break_even(self) -> float:
        """Probabilidad de acierto mínima para no perder dinero."""
        return 1.0 / (1.0 + self.net_multiplier)

    @property
    def house_edge(self) -> float:
        """Ventaja de la casa: 1 - (retorno esperado / apuesta)."""
        return 1.0 - (self.net_multiplier + 1.0) / self.pockets

    def profit(self, hit: bool) -> float:
        """Retorno neto por unidad apostada según el resultado."""
        return self.net_multiplier if hit else -1.0


#: Hipótesis del PDF: ruleta europea de una sola bola, 37 casillas, 35:1.
EUROPEAN_37 = PayoutModel("european37", pockets=37, net_multiplier=35.0)

#: Hipótesis coherente con el espacio de resultados observado (0..18).
EXPRESS_19 = PayoutModel("express19", pockets=19, net_multiplier=18.0)

#: Pago real de Loteka: 180 en total por cada 10 apostados (17:1 neto).
LOTEKA_180 = PayoutModel("loteka180", pockets=19, net_multiplier=17.0)

PAYOUT_MODELS: dict[str, PayoutModel] = {
    EUROPEAN_37.name: EUROPEAN_37,
    EXPRESS_19.name: EXPRESS_19,
    LOTEKA_180.name: LOTEKA_180,
}


@dataclass(frozen=True)
class StrategySpec:
    """Reglas booleanas del motor de alertas.

    La lectura literal del PDF exige que «el submodelo que emite la probabilidad
    principal sea estrictamente M4». `primary_mode` permite materializar las
    tres lecturas posibles:

    - ``any``: la alerta la conduce M4 y el resto de modelos se consideran ruido
      de fondo. Es la lectura que reproduce el 13,75% del documento.
    - ``tie``: M4 debe alcanzar la probabilidad máxima de la ronda (empate
      permitido).
    - ``strict``: M4 debe ser el único emisor de la probabilidad máxima.

    La validación empírica demuestra que ``tie`` y ``strict`` son vacíos en
    este histórico, por lo que el modo por defecto es ``any`` y el informe
    deja constancia de la incompatibilidad.
    """

    model: str = PRIMARY_MODEL
    probability_min: float = 0.05
    probability_max: float = 0.07
    max_spread: float = 0.047
    anchor_numbers: frozenset[int] = ANCHOR_NUMBERS
    primary_mode: str = "any"
    models_in_matrix: tuple[str, ...] = MODEL_ORDER

    def __post_init__(self) -> None:
        if self.primary_mode not in PRIMARY_MODES:
            raise ValueError(
                f"primary_mode debe ser uno de {PRIMARY_MODES}, no {self.primary_mode!r}."
            )
        if not 0.0 < self.probability_min < self.probability_max < 1.0:
            raise ValueError("Los umbrales de probabilidad deben cumplir 0 < min < max < 1.")
        if not 0.0 < self.max_spread < 1.0:
            raise ValueError("max_spread debe estar entre 0 y 1.")
        if self.model not in self.models_in_matrix:
            raise ValueError(f"El modelo {self.model!r} no forma parte de la matriz.")
        unknown = [model for model in self.models_in_matrix if model not in MODEL_ORDER]
        if unknown:
            raise ValueError(
                f"Los modelos {unknown} no existen en la matriz del CSV de supervisión "
                f"({list(MODEL_ORDER)})."
            )

    @property
    def zone_label(self) -> str:
        return f"({self.probability_min:.1%}, {self.probability_max:.1%}]"

    def with_primary_mode(self, mode: str) -> "StrategySpec":
        from dataclasses import replace

        return replace(self, primary_mode=mode)


@dataclass(frozen=True)
class KellyConfig:
    """Parámetros del módulo de stake dinámico.

    `fraction` es el factor de Kelly fraccional (0,25 = cuarto de Kelly).
    `prior_strength` controla el shrinkage bayesiano de la tasa de acierto
    empírica hacia el punto de equilibrio del modelo de pago: sin él, una
    muestra pequeña y afortunada genera stakes desproporcionados.
    `max_fraction` limita la exposición por ronda.
    """

    fraction: float = 0.25
    max_fraction: float = 0.02
    prior_strength: float = 30.0
    prior_rate: float | None = None
    min_edge: float = 0.0
    critical_multiplier: float = 1.5
    high_multiplier: float = 1.0
    use_lower_bound: bool = True
    confidence_level: float = 0.95
    min_observations: int = 20
    min_hits: int = 4

    def __post_init__(self) -> None:
        if not 0.0 < self.fraction <= 1.0:
            raise ValueError("fraction debe estar en (0, 1].")
        if not 0.0 < self.max_fraction <= 1.0:
            raise ValueError("max_fraction debe estar en (0, 1].")
        if self.prior_strength < 0.0:
            raise ValueError("prior_strength no puede ser negativo.")
        if not 0.0 < self.confidence_level < 1.0:
            raise ValueError("confidence_level debe estar en (0, 1).")
