"""Módulo de stake dinámico con variante defensiva del Criterio de Kelly.

El Criterio de Kelly clásico es: `f* = (b·p − q) / b`. Aplicado sin
cautelas a una estimación de 80 observaciones con 11 aciertos produce stakes
absurdos y, sobre todo, concentra el riesgo en un estimador ruidoso.

La variante implementada aquí encadena cinco defensas:

1. `shrinkage` bayesiano de la tasa empírica hacia el punto de equilibrio del
   modelo de pago (equivale a un prior Beta con fuerza `prior_strength`).
2. Uso del extremo inferior del intervalo de confianza de Wilson en lugar del
   estimador puntual.
3. Kelly fraccional (`fraction`, 0,25 por defecto) en lugar de Kelly pleno.
4. Exigencia de ventaja mínima (`min_edge`) frente al equilibrio.
5. Tope duro de exposición por ronda (`max_fraction`) y verificación de que el
   crecimiento esperado del logaritmo de la riqueza sigue siendo positivo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import KellyConfig, PayoutModel
from .stats import wilson_interval


@dataclass(frozen=True)
class StakeDecision:
    """Resultado del dimensionamiento de una apuesta."""

    stake_fraction: float
    stake_amount: float
    full_kelly: float
    fractional_kelly: float
    empirical_rate: float
    shrunk_rate: float
    rate_lower_bound: float
    rate_used: float
    break_even: float
    edge: float
    expected_log_growth: float
    applied: bool
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "stake_fraction": self.stake_fraction,
            "stake_amount": self.stake_amount,
            "full_kelly": self.full_kelly,
            "fractional_kelly": self.fractional_kelly,
            "empirical_rate": self.empirical_rate,
            "shrunk_rate": self.shrunk_rate,
            "rate_lower_bound": self.rate_lower_bound,
            "rate_used": self.rate_used,
            "break_even": self.break_even,
            "edge": self.edge,
            "expected_log_growth": self.expected_log_growth,
            "applied": self.applied,
            "notes": list(self.notes),
        }


class KellyStakeSizer:
    """Dimensiona el stake de una señal a partir de su historial empírico."""

    def __init__(self, payout: PayoutModel, config: KellyConfig | None = None) -> None:
        self.payout = payout
        self.config = config or KellyConfig()

    def size(
        self,
        successes: int,
        trials: int,
        bankroll: float,
        confidence: str = "ALTO",
    ) -> StakeDecision:
        """Calcula el stake para una estrategia con `successes` aciertos en `trials`."""
        config = self.config
        notes: list[str] = []

        if trials <= 0:
            return self._rejected(bankroll, "no hay observaciones históricas", notes)
        if trials < config.min_observations or successes < config.min_hits:
            notes.append(
                f"muestra insuficiente ({successes}/{trials}, mínimos "
                f"{config.min_hits}/{config.min_observations})"
            )
            return self._rejected(bankroll, "; ".join(notes), notes)

        break_even = self.payout.break_even
        empirical = successes / trials
        interval = wilson_interval(successes, trials, config.confidence_level)

        prior_rate = config.prior_rate if config.prior_rate is not None else break_even
        shrunk = (successes + config.prior_strength * prior_rate) / (
            trials + config.prior_strength
        )
        lower_bound = interval.low
        rate_used = min(shrunk, lower_bound) if config.use_lower_bound else shrunk
        if config.use_lower_bound:
            notes.append(
                f"se usa el mínimo entre el estimador encogido ({shrunk:.2%}) y el "
                f"extremo inferior de Wilson ({lower_bound:.2%})"
            )

        edge = rate_used - break_even
        if edge <= config.min_edge:
            notes.append(
                f"sin ventaja defendible: {rate_used:.2%} no supera el equilibrio "
                f"{break_even:.2%}"
            )
            return self._rejected(bankroll, "; ".join(notes), notes)

        b = self.payout.net_multiplier
        full_kelly = (b * rate_used - (1.0 - rate_used)) / b
        if full_kelly <= 0.0:
            notes.append("Kelly pleno no positivo")
            return self._rejected(bankroll, "; ".join(notes), notes)

        fractional = config.fraction * full_kelly
        multiplier = (
            config.critical_multiplier
            if confidence.upper() == "CRITICO"
            else config.high_multiplier
        )
        stake_fraction = min(fractional * multiplier, config.max_fraction)
        if fractional * multiplier > config.max_fraction:
            notes.append(
                f"stake recortado por el tope de exposición "
                f"({fractional * multiplier:.2%} -> {config.max_fraction:.2%})"
            )

        growth = _expected_log_growth(rate_used, b, stake_fraction)
        if growth <= 0.0:
            notes.append("crecimiento esperado del logaritmo no positivo: stake anulado")
            return self._rejected(bankroll, "; ".join(notes), notes)

        return StakeDecision(
            stake_fraction=stake_fraction,
            stake_amount=bankroll * stake_fraction,
            full_kelly=full_kelly,
            fractional_kelly=fractional,
            empirical_rate=empirical,
            shrunk_rate=shrunk,
            rate_lower_bound=lower_bound,
            rate_used=rate_used,
            break_even=break_even,
            edge=edge,
            expected_log_growth=growth,
            applied=True,
            notes=tuple(notes),
        )

    def _rejected(self, bankroll: float, reason: str, notes: list[str]) -> StakeDecision:
        return StakeDecision(
            stake_fraction=0.0,
            stake_amount=0.0,
            full_kelly=0.0,
            fractional_kelly=0.0,
            empirical_rate=0.0,
            shrunk_rate=0.0,
            rate_lower_bound=0.0,
            rate_used=0.0,
            break_even=self.payout.break_even,
            edge=0.0,
            expected_log_growth=0.0,
            applied=False,
            notes=tuple(notes + [reason]),
        )


def _expected_log_growth(probability: float, net_multiplier: float, fraction: float) -> float:
    """Crecimiento esperado del logaritmo de la riqueza por ronda apostada."""
    if fraction <= 0.0:
        return 0.0
    if fraction >= 1.0:
        return float("-inf")
    win = math.log1p(fraction * net_multiplier)
    loss = math.log1p(-fraction)
    return probability * win + (1.0 - probability) * loss
