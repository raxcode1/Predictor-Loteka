"""Laboratorio de identificación de PRNG (auditoría black-box, datos públicos).

Alcance y ética: este módulo sólo trabaja sobre resultados ya publicados o
sobre secuencias sintéticas de laboratorio. No usa semillas reales,
credenciales ni endpoints privados. No asume que Loteka use ninguno de los
generadores probados.

Hipótesis documentadas:

- **MT19937**: recuperable si se observan 624 salidas consecutivas de 32 bits
  (untemper + reconstrucción del estado). Con salidas 0..18 (≈4,25 bits por
  sorteo) y un mapeo desconocido (módulo, rechazo, escalado) la recuperación
  lineal no es aplicable: el resultado es NO_CONCLUYENTE.
- **LCG (parámetros desconocidos)**: recuperable con salidas completas del
  estado (GCD de determinantes). No aplicable a salidas reducidas a 0..18.
- **LCG (catálogo conocido)**: glibc TYPE_0, java.util.Random y MSVC, con
  mapeos `mod` ((s >> shift) % 19) y `range` (((s >> shift) * 19) >> bits),
  buscados con Z3 bajo límites de tiempo y número de consultas.
- **CSPRNG / fuente física**: no recuperable por construcción; es la hipótesis
  compatible con la ausencia de señal en la batería estadística.

Criterio de «RECUPERADO» (todos obligatorios):
1. el candidato se ajusta sobre un bloque de entrenamiento contiguo,
2. predice **exactamente** un bloque reservado contiguo que no usó,
3. todas las soluciones alternativas enumeradas predicen lo mismo,
4. la probabilidad de acertar el bloque por azar, corregida por el número de
   hipótesis probadas (Bonferroni), es < 1e-6.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from . import OUTPUT_MODULUS
from .data import Dataset

K = OUTPUT_MODULUS
MASK32 = 0xFFFFFFFF

STATUS_RECOVERED = "RECUPERADO"
STATUS_CANDIDATE = "CANDIDATO_NO_VALIDADO"
STATUS_NOT_RECOVERED = "NO_RECUPERADO"
STATUS_INCONCLUSIVE = "NO_CONCLUYENTE"
STATUS_NOT_APPLICABLE = "NO_APLICABLE"

CHANCE_THRESHOLD = 1e-6


# ----------------------------------------------------------------------
# MT19937
# ----------------------------------------------------------------------
class MT19937:
    """Implementación de referencia de MT19937 (32 bits)."""

    N = 624
    M = 397

    def __init__(self, seed: int | None = None, state: Sequence[int] | None = None, index: int = 624) -> None:
        if state is not None:
            if len(state) != self.N:
                raise ValueError("El estado MT19937 tiene 624 palabras.")
            self.mt = [int(w) & MASK32 for w in state]
            self.index = index
            return
        self.mt = [0] * self.N
        self.mt[0] = (seed or 0) & MASK32
        for i in range(1, self.N):
            prev = self.mt[i - 1]
            self.mt[i] = (1812433253 * (prev ^ (prev >> 30)) + i) & MASK32
        self.index = self.N

    def _twist(self) -> None:
        mt = self.mt
        for i in range(self.N):
            y = (mt[i] & 0x80000000) | (mt[(i + 1) % self.N] & 0x7FFFFFFF)
            value = mt[(i + self.M) % self.N] ^ (y >> 1)
            if y & 1:
                value ^= 0x9908B0DF
            mt[i] = value
        self.index = 0

    def next_u32(self) -> int:
        if self.index >= self.N:
            self._twist()
        y = self.mt[self.index]
        self.index += 1
        return temper(y)


def temper(y: int) -> int:
    y ^= y >> 11
    y ^= (y << 7) & 0x9D2C5680
    y ^= (y << 15) & 0xEFC60000
    y ^= y >> 18
    return y & MASK32


def _undo_right(value: int, shift: int) -> int:
    result = value
    for _ in range(32 // shift + 1):
        result = value ^ (result >> shift)
    return result & MASK32


def _undo_left(value: int, shift: int, mask: int) -> int:
    result = value
    for _ in range(32 // shift + 1):
        result = value ^ ((result << shift) & mask)
    return result & MASK32


def untemper(y: int) -> int:
    y = _undo_right(y, 18)
    y = _undo_left(y, 15, 0xEFC60000)
    y = _undo_left(y, 7, 0x9D2C5680)
    y = _undo_right(y, 11)
    return y & MASK32


def mt19937_clone(outputs: Sequence[int]) -> MT19937:
    """Reconstruye el estado a partir de 624 salidas consecutivas de 32 bits."""
    if len(outputs) < MT19937.N:
        raise ValueError("Se requieren 624 salidas consecutivas de 32 bits.")
    state = [untemper(int(o) & MASK32) for o in outputs[: MT19937.N]]
    return MT19937(state=state, index=MT19937.N)


def mt19937_recover(outputs: Sequence[int], holdout: int = 100) -> dict[str, Any]:
    """Clona con 624 salidas y valida sobre `holdout` salidas reservadas."""
    if len(outputs) < MT19937.N + holdout:
        return {
            "generator": "MT19937",
            "status": STATUS_INCONCLUSIVE,
            "reason": f"se requieren {MT19937.N + holdout} salidas de 32 bits, hay {len(outputs)}",
        }
    clone = mt19937_clone(outputs)
    reserved = [int(o) & MASK32 for o in outputs[MT19937.N : MT19937.N + holdout]]
    predicted = [clone.next_u32() for _ in range(holdout)]
    matches = sum(p == r for p, r in zip(predicted, reserved))
    exact = matches == holdout
    return {
        "generator": "MT19937",
        "status": STATUS_RECOVERED if exact else STATUS_NOT_RECOVERED,
        "fit_outputs": MT19937.N,
        "holdout_outputs": holdout,
        "holdout_matches": matches,
        "chance_probability": 2.0 ** (-32 * holdout),
        "reason": "predice exactamente el bloque reservado" if exact else "el bloque reservado no coincide",
    }


# ----------------------------------------------------------------------
# LCG
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class LCGSpec:
    name: str
    modulus_bits: int
    multiplier: int
    increment: int
    shifts: tuple[int, ...]
    output_bits: int | None = None  # None = todos los bits por encima de shift

    @property
    def modulus(self) -> int:
        return 1 << self.modulus_bits

    def step(self, state: int) -> int:
        return (self.multiplier * state + self.increment) % self.modulus

    def raw_output(self, state: int, shift: int) -> int:
        out = state >> shift
        bits = self.bits_for(shift)
        return out & ((1 << bits) - 1)

    def bits_for(self, shift: int) -> int:
        return self.output_bits if self.output_bits is not None else self.modulus_bits - shift


LCG_CATALOG: tuple[LCGSpec, ...] = (
    LCGSpec("glibc_type0", 31, 1103515245, 12345, tuple(range(27))),
    LCGSpec("java.util.Random", 48, 25214903917, 11, tuple(range(44))),
    LCGSpec("msvc_rand", 32, 214013, 2531011, (16,), output_bits=15),
)

MAPPINGS = ("mod", "range")


def map_output(raw: int, bits: int, mapping: str) -> int:
    if mapping == "mod":
        return raw % K
    if mapping == "range":
        return (raw * K) >> bits
    raise ValueError(f"Mapeo desconocido: {mapping}")


def lcg_sequence(spec: LCGSpec, seed: int, count: int, shift: int, mapping: str) -> list[int]:
    """Genera `count` salidas mapeadas a 0..18 (positivos de control/tests)."""
    state = seed % spec.modulus
    bits = spec.bits_for(shift)
    out = []
    for _ in range(count):
        out.append(map_output(spec.raw_output(state, shift), bits, mapping))
        state = spec.step(state)
    return out


def lcg_crack_full(states: Sequence[int]) -> dict[str, int]:
    """Recupera (m, a, c) de un LCG con salidas completas del estado."""
    if len(states) < 6:
        raise ValueError("Se requieren al menos 6 estados consecutivos.")
    s = [int(v) for v in states]
    t = [s[i + 1] - s[i] for i in range(len(s) - 1)]
    zeros = [t[i + 2] * t[i] - t[i + 1] * t[i + 1] for i in range(len(t) - 2)]
    modulus = 0
    for z in zeros:
        modulus = math.gcd(modulus, abs(z))
    if modulus <= max(s):
        raise ValueError("No se pudo determinar un módulo consistente.")
    multiplier = None
    for i in range(len(t) - 1):
        if math.gcd(t[i] % modulus, modulus) == 1:
            multiplier = (t[i + 1] * pow(t[i], -1, modulus)) % modulus
            break
    if multiplier is None:
        raise ValueError("Diferencias no invertibles: parámetros no identificables.")
    increment = (s[1] - multiplier * s[0]) % modulus
    for a, b in pairwise(s):
        if (multiplier * a + increment) % modulus != b:
            raise ValueError("Los parámetros recuperados no reproducen la secuencia.")
    return {"modulus": modulus, "multiplier": multiplier, "increment": increment}


EXHAUSTIVE_MAX_BITS = 32
_CHUNK = 1 << 22


def _map_array(raw: Any, bits: int, mapping: str) -> Any:
    if mapping == "mod":
        return raw % K
    return (raw * K) >> bits


def _exhaustive_lcg(
    spec: LCGSpec,
    shift: int,
    mapping: str,
    fit: Sequence[int],
    max_solutions: int,
    deadline: float,
) -> dict[str, Any]:
    """Búsqueda exacta para estados de <= 32 bits (resultado sat/unsat definitivo).

    El estado se descompone en `G = s >> shift` (parte alta) y `L` (bits bajos).
    La primera observación sólo depende de G, así que se filtra G antes de
    combinarlo con todos los L: el coste es ~2^w / 19 candidatos en lugar de 2^w.
    """
    import numpy as np

    width = spec.modulus_bits
    bits = spec.bits_for(shift)
    high_bits = width - shift
    state_mask = np.uint64((1 << width) - 1)
    out_mask = np.uint64((1 << bits) - 1)
    a = np.uint64(spec.multiplier)
    c = np.uint64(spec.increment)
    low_values = np.arange(1 << shift, dtype=np.uint64)
    first = int(fit[0])
    solutions: list[int] = []

    def survivors(seeds: Any) -> Any:
        state = seeds
        keep = np.ones(seeds.size, dtype=bool)
        for observed in fit[1:]:
            state = (a * state + c) & state_mask
            raw = (state >> np.uint64(shift)) & out_mask
            keep &= _map_array(raw, bits, mapping) == np.uint64(observed)
            if not keep.any():
                return seeds[:0]
            seeds, state, keep = seeds[keep], state[keep], np.ones(int(keep.sum()), dtype=bool)
        return seeds

    # Salidas crudas compatibles con la primera observación, generadas directamente.
    if mapping == "mod":
        raw_first = np.arange(first, 1 << bits, K, dtype=np.uint64)
    else:
        low = -(-(first << bits) // K)
        high = min(-(-((first + 1) << bits) // K), 1 << bits)
        raw_first = np.arange(low, high, dtype=np.uint64)
    # Bits altos no observados (p. ej. MSVC descarta el bit 31).
    free_high = np.arange(1 << max(0, high_bits - bits), dtype=np.uint64) << np.uint64(bits)
    g_all_size = raw_first.size * free_high.size
    step = max(1, _CHUNK // max(1, low_values.size))
    for g_start in range(0, g_all_size, step):
        if time.monotonic() > deadline:
            return {"status": "unknown", "seeds": solutions, "method": "exhaustive"}
        flat = np.arange(g_start, min(g_start + step, g_all_size), dtype=np.uint64)
        g = raw_first[flat // np.uint64(free_high.size)] | free_high[flat % np.uint64(free_high.size)]
        seeds = ((g[:, None] << np.uint64(shift)) | low_values[None, :]).ravel()
        for seed in survivors(seeds).tolist():
            solutions.append(int(seed))
            if len(solutions) >= max_solutions:
                return {"status": "sat", "seeds": solutions, "method": "exhaustive"}
    return {"status": "sat" if solutions else "unsat", "seeds": solutions, "method": "exhaustive"}


def _solve_lcg(
    spec: LCGSpec,
    shift: int,
    mapping: str,
    fit: Sequence[int],
    timeout_ms: int,
    max_solutions: int,
    deadline: float | None = None,
) -> dict[str, Any]:
    if spec.modulus_bits <= EXHAUSTIVE_MAX_BITS:
        return _exhaustive_lcg(
            spec, shift, mapping, fit, max_solutions,
            deadline if deadline is not None else time.monotonic() + timeout_ms / 1000.0,
        )
    import z3  # import diferido: z3 sólo es necesario para estados > 32 bits

    width = spec.modulus_bits
    bits = spec.bits_for(shift)
    seed = z3.BitVec("s0", width)
    solver = z3.Solver()
    solver.set(timeout=timeout_ms)
    state = seed
    mask = z3.BitVecVal((1 << bits) - 1, width)
    for index, observed in enumerate(fit):
        raw = z3.LShR(state, shift) & mask
        if mapping == "mod":
            # raw = 19 q + o con q acotado: evita URem (muy costoso en bit-blasting).
            q = z3.BitVec(f"q{index}", width)
            solver.add(z3.ULE(q, ((1 << bits) - 1) // K), raw == q * K + observed)
        else:
            # floor(raw * 19 / 2^bits) == o  <=>  raw en un intervalo (lineal).
            low = -(-(observed << bits) // K)
            high = -(-((observed + 1) << bits) // K)
            solver.add(z3.UGE(raw, low), z3.ULT(raw, high))
        state = spec.multiplier * state + spec.increment
    solutions: list[int] = []
    status = "unsat"
    while len(solutions) < max_solutions:
        result = solver.check()
        if result == z3.unknown:
            status = "unknown" if not solutions else "sat"
            break
        if result != z3.sat:
            status = "sat" if solutions else "unsat"
            break
        value = solver.model().eval(seed, model_completion=True).as_long()
        solutions.append(value)
        solver.add(seed != value)
        status = "sat"
    return {"status": status, "seeds": solutions, "method": "smt"}


def lcg_catalog_search(
    values: Sequence[int],
    fit_size: int = 24,
    holdout_size: int = 16,
    timeout_ms: int = 2000,
    max_seconds: float = 120.0,
    max_queries: int = 400,
    max_solutions: int = 4,
    catalog: Sequence[LCGSpec] = LCG_CATALOG,
    mappings: Sequence[str] = MAPPINGS,
) -> dict[str, Any]:
    """Búsqueda SMT acotada sobre el catálogo LCG con bloque reservado."""
    values = [int(v) for v in values]
    if len(values) < fit_size + holdout_size:
        return {
            "status": STATUS_INCONCLUSIVE,
            "reason": f"se requieren {fit_size + holdout_size} sorteos contiguos, hay {len(values)}",
            "hypotheses_tested": 0,
        }
    fit = values[:fit_size]
    reserved = values[fit_size : fit_size + holdout_size]
    started = time.monotonic()
    tested = 0
    outcome = {"sat": 0, "unsat": 0, "unknown": 0}
    candidates: list[dict[str, Any]] = []
    stopped_reason = None
    for spec in catalog:
        for mapping in mappings:
            for shift in spec.shifts:
                if mapping == "range" and spec.bits_for(shift) < 5:
                    continue
                if tested >= max_queries:
                    stopped_reason = f"límite de {max_queries} consultas"
                    break
                if time.monotonic() - started > max_seconds:
                    stopped_reason = f"límite de {max_seconds:g} s"
                    break
                tested += 1
                result = _solve_lcg(
                    spec, shift, mapping, fit, timeout_ms, max_solutions,
                    deadline=started + max_seconds,
                )
                outcome[result["status"]] += 1
                if not result["seeds"]:
                    continue
                predictions = []
                for seed in result["seeds"]:
                    full = lcg_sequence(spec, seed, fit_size + holdout_size, shift, mapping)
                    predictions.append(full[fit_size:])
                matches = [sum(p == r for p, r in zip(pred, reserved)) for pred in predictions]
                candidates.append(
                    {
                        "generator": spec.name,
                        "shift": shift,
                        "mapping": mapping,
                        "method": result.get("method"),
                        "solutions_found": len(result["seeds"]),
                        "solutions_capped": len(result["seeds"]) >= max_solutions,
                        "seeds": result["seeds"],
                        "holdout_matches": matches,
                        "predictions_agree": all(p == predictions[0] for p in predictions),
                        "exact_holdout": all(m == holdout_size for m in matches),
                    }
                )
            if stopped_reason:
                break
        if stopped_reason:
            break

    chance = min(1.0, max(tested, 1) * (1.0 / K) ** holdout_size)
    validated = [c for c in candidates if c["exact_holdout"] and c["predictions_agree"]]
    if validated and chance < CHANCE_THRESHOLD:
        status = STATUS_RECOVERED
        reason = "un candidato predice exactamente el bloque reservado y todas sus soluciones coinciden"
    elif candidates:
        status = STATUS_CANDIDATE
        reason = "hay soluciones para el ajuste pero ninguna supera el bloque reservado de forma única"
    elif outcome["unknown"] or stopped_reason:
        status = STATUS_INCONCLUSIVE
        reason = "consultas sin resolver por límite de tiempo/coste" + (f" ({stopped_reason})" if stopped_reason else "")
    else:
        status = STATUS_NOT_RECOVERED
        reason = "todas las hipótesis del catálogo son insatisfacibles para el bloque de ajuste"
    return {
        "status": status,
        "reason": reason,
        "fit_size": fit_size,
        "holdout_size": holdout_size,
        "hypotheses_tested": tested,
        "solver_outcomes": outcome,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "bonferroni_chance_probability": chance,
        "candidates": candidates[:20],
        "stopped_reason": stopped_reason,
        "limits": {"timeout_ms": timeout_ms, "max_seconds": max_seconds, "max_queries": max_queries},
        "method_notes": METHOD_NOTES,
    }


METHOD_NOTES = {
    "exhaustive": (
        "estados <= 32 bits (glibc_type0, msvc_rand): búsqueda exacta; sat/unsat definitivo; "
        "control positivo superado en tests"
    ),
    "smt": (
        "estados > 32 bits (java.util.Random): Z3 con codificación lineal; el control positivo "
        "no se resuelve dentro del presupuesto por defecto, así que 'unknown' no descarta la hipótesis"
    ),
}


# ----------------------------------------------------------------------
# Identificación sobre datos publicados
# ----------------------------------------------------------------------
def identify_public(
    dataset: Dataset,
    run_solver: bool = False,
    fit_size: int = 24,
    holdout_size: int = 16,
    **solver_kwargs: Any,
) -> dict[str, Any]:
    """Evalúa todas las hipótesis sobre el bloque contiguo más reciente."""
    start, end = dataset.longest_contiguous_suffix()
    block = dataset.values[start:end].tolist()
    bits_per_draw = math.log2(K)
    hypotheses: list[dict[str, Any]] = [
        {
            "generator": "MT19937",
            "status": STATUS_NOT_APPLICABLE,
            "reason": (
                "requiere 624 salidas consecutivas de 32 bits; cada sorteo aporta "
                f"{bits_per_draw:.2f} bits y el mapeo a 0..18 es desconocido y no lineal"
            ),
        },
        {
            "generator": "LCG (parámetros desconocidos)",
            "status": STATUS_NOT_APPLICABLE,
            "reason": "la recuperación por GCD requiere salidas completas del estado",
        },
        {
            "generator": "xorshift / PCG",
            "status": STATUS_INCONCLUSIVE,
            "reason": "no implementado: requiere hipótesis concretas de parámetros y mapeo",
        },
        {
            "generator": "CSPRNG / fuente física",
            "status": STATUS_NOT_APPLICABLE,
            "reason": "no recuperable por diseño; compatible con ausencia de señal estadística",
        },
    ]
    if run_solver:
        search = lcg_catalog_search(
            block[-(fit_size + holdout_size):] if len(block) >= fit_size + holdout_size else block,
            fit_size=fit_size,
            holdout_size=holdout_size,
            **solver_kwargs,
        )
    else:
        search = {
            "status": STATUS_INCONCLUSIVE,
            "reason": "búsqueda SMT no ejecutada en línea (usar `python -m quant --prng`)",
            "hypotheses_tested": 0,
        }
    hypotheses.insert(2, {"generator": "LCG (catálogo conocido)", **search})
    recovered = any(h.get("status") == STATUS_RECOVERED for h in hypotheses)
    return {
        "block": {
            "first_draw": int(dataset.draw_ids[start]) if end > start else None,
            "last_draw": int(dataset.draw_ids[end - 1]) if end > start else None,
            "length": end - start,
        },
        "bits_per_draw": bits_per_draw,
        "hypotheses": hypotheses,
        "recovered": recovered,
        "verdict": STATUS_RECOVERED if recovered else STATUS_INCONCLUSIVE
        if search.get("status") in (STATUS_INCONCLUSIVE, STATUS_CANDIDATE)
        else STATUS_NOT_RECOVERED,
        "disclaimer": (
            "Un solver que satisface observaciones no prueba la identidad del generador, "
            "y un fallo no descarta otros PRNG."
        ),
    }
