"""Monitor en vivo de Ruleta Express.

El monitor es el componente que convierte el histórico en un registro
persistente y auditable:

1. **Predicciones comparadas**: cada ronda queda asociada al sorteo que
   sirvió de base y, en cuanto aparece el siguiente resultado, se resuelve
   con el número real, la posición que ocupaba en el ranking Markov y en el
   heurístico, y si acertó o falló. El registro se guarda en disco (local) o
   en Vercel Blob (producción), de modo que la evidencia se acumula entre
   reinicios y se puede seguir la tasa de acierto ronda a ronda.

2. **Alerta M4**: antes de cada sorteo se evalúa la ronda con el
   `AlertEngine` del paquete `m4_engine` usando la matriz de diez submodelos
   en vivo. La decisión indica si se cumple la ventana de probabilidad, el
   filtro de dispersión y el anclaje, y qué stake dimensiona el módulo de
   Kelly defensivo.

3. **Contadores acumulados**: aciertos y fallos de las alertas y de las
   predicciones, con su tasa y su comparación frente a la línea base de
   1/19 = 5,26%.

El monitor nunca usa el resultado de la ronda que está evaluando: los
estimadores sólo se actualizan con sorteos ya resueltos.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from m4_engine.alerts import AlertDecision, AlertEngine
from m4_engine.config import PayoutModel, StrategySpec
from m4_engine.kelly import KellyConfig

from live_models import OUTPUT_MODULUS, LiveForecast, build_forecasts

STORE_VERSION = 1

STATUS_PENDIENTE = "pendiente"
STATUS_ACIERTO = "acierto"
STATUS_FALLO = "fallo"
STATUS_OMITIDA = "omitida_pausa"

#: Línea base equiprobable del espacio observado (0..18).
BASELINE = 1.0 / OUTPUT_MODULUS

#: Línea base de los cinco primeros candidatos de un ranking.
BASELINE_TOP5 = 5.0 / OUTPUT_MODULUS

StoreLoader = Callable[[], dict]
StoreSaver = Callable[[dict], None]


# ----------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def draw_number(draw: str) -> int:
    """Extrae el número de sorteo de una etiqueta tipo «No. 249491»."""
    return int(str(draw).rsplit(" ", 1)[-1])


def parse_draws(rows: Iterable[dict[str, str]]) -> list[tuple[int, str, int]]:
    """Normaliza las filas del CSV en una lista cronológica de sorteos."""
    draws: dict[int, tuple[int, str, int]] = {}
    for row in rows:
        label = str(row.get("No_Sorteo", "")).strip()
        if not label:
            continue
        try:
            number = draw_number(label)
            value = int(str(row.get("Numero", "")).strip())
        except ValueError:
            continue
        draws[number] = (number, label, value)
    return [draws[number] for number in sorted(draws)]


def _rank_of(ranking: list[dict], actual: int) -> int | None:
    for index, item in enumerate(ranking):
        if int(item.get("number", -1)) == int(actual):
            return index + 1
    return None


def _decision_from_dict(data: dict[str, Any]) -> AlertDecision:
    """Reconstruye una decisión serializada para poder cerrar el ciclo."""
    return AlertDecision(
        fired=bool(data.get("fired")),
        model=str(data.get("model", "M4")),
        number=data.get("number"),
        probability=data.get("probability"),
        spread=data.get("spread"),
        confidence=str(data.get("confidence", "NINGUNA")),
        anchor=bool(data.get("anchor")),
        stake=None,
        failures=tuple(data.get("failures") or ()),
        diagnostics={},
    )


def json_file_store(path: Path) -> tuple[StoreLoader, StoreSaver]:
    """Devuelve un par (cargar, guardar) sobre un archivo JSON local."""

    def load() -> dict:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=path.stem,
            suffix=".tmp",
            delete=False,
        )
        try:
            with handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            Path(handle.name).replace(path)
        except OSError:
            Path(handle.name).unlink(missing_ok=True)
            raise

    return load, save


def memory_store() -> tuple[StoreLoader, StoreSaver]:
    """Store en memoria (respaldo cuando no hay almacenamiento persistente)."""
    state: dict[str, Any] = {}

    def load() -> dict:
        return state

    def save(data: dict) -> None:
        state.clear()
        state.update(data)

    return load, save


def _empty_stats() -> dict[str, int]:
    return {
        "rounds": 0,
        "alerts": 0,
        "alert_hits": 0,
        "window_rounds": 0,
        "window_hits": 0,
        "anchor_alerts": 0,
        "anchor_hits": 0,
        "markov_hits": 0,
        "markov_trials": 0,
        "markov_top1_hits": 0,
        "heuristic_hits": 0,
        "heuristic_trials": 0,
        "heuristic_top1_hits": 0,
        "pauses": 0,
    }


def _normalize_stats(raw: Any) -> dict[str, int]:
    stats = _empty_stats()
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in stats:
                try:
                    stats[key] = int(value)
                except (TypeError, ValueError):
                    continue
    return stats


def _migrate_records(records: list[Any]) -> tuple[list[dict], int]:
    """Conserva sólo los registros con la matriz en vivo.

    El registro que dejó la versión anterior de la API guardaba el ranking
    Markov y el heurístico, pero no la matriz de diez submodelos ni la
    decisión de la alerta: sin esos datos la ronda no puede reevaluarse, así
    que se descarta y el monitor vuelve a retroanalizar el histórico.
    """
    migrated: list[dict] = []
    dropped = 0
    for record in records:
        if not isinstance(record, dict):
            dropped += 1
            continue
        matrix = record.get("matrix")
        if not isinstance(matrix, dict) or "numbers" not in matrix or "probabilities" not in matrix:
            dropped += 1
            continue
        item = dict(record)
        if item.get("source_number") is None:
            try:
                item["source_number"] = draw_number(str(item.get("source_draw", "")))
            except ValueError:
                dropped += 1
                continue
        migrated.append(item)
    return migrated, dropped


@dataclass
class _State:
    predictions: list[dict] = field(default_factory=list)
    pauses: list[dict] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=_empty_stats)
    dirty: bool = False


# ----------------------------------------------------------------------
# Monitor
# ----------------------------------------------------------------------
class LiveMonitor:
    """Registro vivo de predicciones comparadas y alerta M4."""

    def __init__(
        self,
        load_store: StoreLoader,
        save_store: StoreSaver,
        spec: StrategySpec | None = None,
        payout: PayoutModel | None = None,
        kelly_config: KellyConfig | None = None,
        bankroll: float = 1000.0,
        backfill_rounds: int = 300,
        max_records: int = 300,
    ) -> None:
        self.load_store = load_store
        self.save_store = save_store
        self.spec = spec or StrategySpec()
        self.payout = payout
        self.kelly_config = kelly_config or KellyConfig()
        self.bankroll = bankroll
        self.backfill_rounds = max(1, int(backfill_rounds))
        self.max_records = max(10, int(max_records))
        self._state = _State()
        self._loaded = False
        self._migrated = False

    # ------------------------------------------------------------------
    # Almacén
    # ------------------------------------------------------------------
    def _load(self) -> _State:
        if not self._loaded:
            stored = self.load_store() or {}
            predictions, dropped = _migrate_records(list(stored.get("predictions") or []))
            self._state = _State(
                predictions=predictions,
                pauses=list(stored.get("pauses") or []),
                stats=_normalize_stats(stored.get("stats")) if not dropped else _empty_stats(),
            )
            self._migrated = dropped > 0
            self._loaded = True
        return self._state

    def _persist(self, state: _State) -> None:
        if not state.dirty:
            return
        payload = {
            "version": STORE_VERSION,
            "updated_at": _now(),
            "stats": dict(state.stats),
            "predictions": state.predictions,
            "pauses": state.pauses,
        }
        self.save_store(payload)
        state.dirty = False

    # ------------------------------------------------------------------
    # Motor de alertas
    # ------------------------------------------------------------------
    def _build_engine(self, stats: dict[str, int]) -> AlertEngine:
        engine = AlertEngine(
            spec=self.spec,
            payout=self.payout,
            kelly_config=self.kelly_config,
            bankroll=self.bankroll,
            half_life=None,
        )
        engine.seed_cohorts(
            window=(stats["window_hits"], stats["window_rounds"]),
            alert=(stats["alert_hits"], stats["alerts"]),
            anchor=(stats["anchor_hits"], stats["anchor_alerts"]),
        )
        return engine

    def _evaluate(self, engine: AlertEngine, record: dict) -> dict:
        decision = engine.evaluate(
            {
                "draw_id": record.get("source_number"),
                "last_number": record.get("last_number"),
                "numbers": record["matrix"]["numbers"],
                "probabilities": record["matrix"]["probabilities"],
            }
        )
        return decision.as_dict()

    def _observe(
        self,
        engine: AlertEngine,
        decision: AlertDecision,
        actual: int,
        state: _State,
    ) -> bool:
        """Cierra el ciclo de una ronda y actualiza los estimadores."""
        engine.observe(decision, actual)
        hit = bool(decision.number is not None and int(actual) == int(decision.number))
        probability = decision.probability
        if (
            probability is not None
            and self.spec.probability_min < probability <= self.spec.probability_max
        ):
            state.stats["window_rounds"] += 1
            state.stats["window_hits"] += int(hit)
        if decision.fired:
            state.stats["alerts"] += 1
            state.stats["alert_hits"] += int(hit)
            if decision.anchor:
                state.stats["anchor_alerts"] += 1
                state.stats["anchor_hits"] += int(hit)
        return hit

    # ------------------------------------------------------------------
    # Construcción de registros
    # ------------------------------------------------------------------
    def _build_record(
        self,
        label: str,
        number: int,
        forecast: LiveForecast,
    ) -> dict:
        markov_list = list(forecast.markov)
        heuristic_list = list(forecast.heuristic)
        markov_forecast = [
            {"number": int(n), "probability": float(forecast.probabilities.get(n, 0.0))}
            for n, _ in markov_list[:5]
        ]
        heuristic_forecast = [
            {"number": int(n), "probability": float(forecast.probabilities.get(n, 0.0))}
            for n, _ in heuristic_list[:5]
        ]
        return {
            "source_draw": label,
            "source_number": number,
            "created_at": _now(),
            "last_number": forecast.last_number,
            "matrix": {
                "numbers": dict(forecast.numbers),
                "probabilities": dict(forecast.probabilities),
            },
            "markov": markov_list,
            "heuristic": heuristic_list,
            "markov_forecast": markov_forecast,
            "heuristic_forecast": heuristic_forecast,
            "decision": {},
            "status": STATUS_PENDIENTE,
            "observed_draw": None,
            "actual_number": None,
            "markov_rank": None,
            "heuristic_rank": None,
            "matched": "pendiente",
            "alert_hit": None,
        }

    def _close(
        self,
        engine: AlertEngine,
        record: dict,
        observed_draw: str,
        actual: int,
        state: _State,
    ) -> None:
        """Resuelve una ronda con el resultado observado."""
        markov_rank = _rank_of(record.get("markov") or [], actual)
        heuristic_rank = _rank_of(record.get("heuristic") or [], actual)
        if markov_rank and heuristic_rank:
            matched = "ambos"
        elif markov_rank:
            matched = "markov"
        elif heuristic_rank:
            matched = "heuristico"
        else:
            matched = "ninguno"

        decision = _decision_from_dict(record.get("decision") or {})
        hit = self._observe(engine, decision, actual, state)

        record.update(
            {
                "status": STATUS_ACIERTO if (markov_rank or heuristic_rank) else STATUS_FALLO,
                "observed_draw": observed_draw,
                "actual_number": int(actual),
                "markov_rank": markov_rank,
                "heuristic_rank": heuristic_rank,
                "matched": matched,
                "alert_hit": hit if decision.fired else None,
            }
        )
        state.stats["rounds"] += 1
        state.stats["markov_trials"] += 1
        state.stats["markov_hits"] += int(markov_rank is not None)
        state.stats["markov_top1_hits"] += int(markov_rank == 1)
        state.stats["heuristic_trials"] += 1
        state.stats["heuristic_hits"] += int(heuristic_rank is not None)
        state.stats["heuristic_top1_hits"] += int(heuristic_rank == 1)
        state.dirty = True

    def _register_pause(self, state: _State, from_number: int, to_number: int) -> None:
        if to_number < from_number:
            return
        from_draw = f"No. {from_number}"
        to_draw = f"No. {to_number}"
        for pause in state.pauses:
            if pause.get("from_draw") == from_draw and pause.get("to_draw") == to_draw:
                return
        state.pauses.append(
            {
                "from_draw": from_draw,
                "to_draw": to_draw,
                "skipped_count": to_number - from_number + 1,
                "detected_at": _now(),
            }
        )
        state.stats["pauses"] += 1
        state.dirty = True

    # ------------------------------------------------------------------
    # Flujo principal
    # ------------------------------------------------------------------
    def update(self, rows: Iterable[dict[str, str]]) -> dict:
        """Actualiza el registro con los resultados conocidos y devuelve el estado."""
        state = self._load()
        draws = parse_draws(rows)
        engine = self._build_engine(state.stats)

        if len(draws) >= 2:
            values = [value for _, _, value in draws]
            forecasts = build_forecasts(values, self.backfill_rounds)
            if not state.predictions or self._migrated:
                state.predictions = []
                state.stats = _empty_stats()
                self._migrated = False
                self._backfill(state, draws, engine, forecasts)
            else:
                self._resolve(state, draws, engine)
                self._open(state, draws, engine, forecasts)
            self._refresh_pending(state, engine)
            self._trim(state)

        self._persist(state)
        return self.payload()

    def _backfill(
        self,
        state: _State,
        draws: list[tuple[int, str, int]],
        engine: AlertEngine,
        forecasts: list[LiveForecast],
    ) -> None:
        """Retroanaliza las últimas rondas del histórico."""
        first_index = len(draws) - len(forecasts)
        for offset, forecast in enumerate(forecasts):
            index = first_index + offset
            number, label, _ = draws[index]
            record = self._build_record(label, number, forecast)
            record["decision"] = self._evaluate(engine, record)
            state.predictions.append(record)

            if index + 1 < len(draws):
                next_number, next_label, next_value = draws[index + 1]
                if next_number > number + 1:
                    self._register_pause(state, number + 1, next_number - 1)
                    record.update(
                        {
                            "status": STATUS_OMITIDA,
                            "observed_draw": next_label,
                            "actual_number": None,
                            "matched": "no evaluada",
                        }
                    )
                    state.dirty = True
                else:
                    self._close(engine, record, next_label, next_value, state)
        state.dirty = True

    def _resolve(
        self,
        state: _State,
        draws: list[tuple[int, str, int]],
        engine: AlertEngine,
    ) -> None:
        """Cierra las rondas pendientes cuyo resultado ya está publicado."""
        for record in state.predictions:
            if record.get("status") != STATUS_PENDIENTE:
                continue
            source = int(record["source_number"])
            following = next((item for item in draws if item[0] > source), None)
            if following is None:
                continue
            number, label, value = following
            if number > source + 1:
                self._register_pause(state, source + 1, number - 1)
                record.update(
                    {
                        "status": STATUS_OMITIDA,
                        "observed_draw": label,
                        "actual_number": None,
                        "matched": "no evaluada",
                        "alert_hit": None,
                    }
                )
                state.dirty = True
                continue
            self._close(engine, record, label, value, state)

    def _open(
        self,
        state: _State,
        draws: list[tuple[int, str, int]],
        engine: AlertEngine,
        forecasts: list[LiveForecast],
    ) -> None:
        """Abre la ronda pendiente con la predicción del último sorteo."""
        if not forecasts:
            return
        number, label, _ = draws[-1]
        if any(record.get("source_number") == number for record in state.predictions):
            return

        previous_sources = [
            int(record["source_number"])
            for record in state.predictions
            if record.get("source_number") is not None
        ]
        if previous_sources:
            previous = max(previous_sources)
            if number > previous + 1:
                self._register_pause(state, previous + 1, number - 1)

        record = self._build_record(label, number, forecasts[-1])
        record["decision"] = self._evaluate(engine, record)
        state.predictions.append(record)
        state.dirty = True

    def _refresh_pending(self, state: _State, engine: AlertEngine) -> None:
        """Recalcula la decisión de la ronda pendiente con la evidencia actual."""
        for record in reversed(state.predictions):
            if record.get("status") != STATUS_PENDIENTE:
                continue
            decision = self._evaluate(engine, record)
            if decision != record.get("decision"):
                record["decision"] = decision
                state.dirty = True
            return

    def _trim(self, state: _State) -> None:
        """Conserva sólo las rondas más recientes."""
        if len(state.predictions) <= self.max_records:
            return
        state.predictions = state.predictions[-self.max_records :]
        state.dirty = True

    # ------------------------------------------------------------------
    # Payload para la interfaz
    # ------------------------------------------------------------------
    def payload(self) -> dict:
        """Estado completo del monitor, listo para la API y la interfaz."""
        state = self._state
        pending = next(
            (
                record
                for record in reversed(state.predictions)
                if record.get("status") == STATUS_PENDIENTE
            ),
            None,
        )
        return {
            "alert": self._alert_payload(pending),
            "alert_stats": self._alert_stats(),
            "alert_history": self._alert_history(),
            "predictions": [self._public_record(record) for record in reversed(state.predictions)],
            "pauses": list(reversed(state.pauses)),
            "prediction_stats": self._prediction_stats(),
        }

    def _alert_payload(self, pending: dict | None) -> dict:
        decision = dict(pending.get("decision") or {}) if pending else {}
        stake = decision.get("stake") or None
        fired = bool(decision.get("fired"))
        return {
            "available": pending is not None,
            "fired": fired,
            "state": "ACTIVA" if fired else "SIN SEÑAL",
            "model": decision.get("model", self.spec.model),
            "number": decision.get("number"),
            "probability": decision.get("probability"),
            "spread": decision.get("spread"),
            "confidence": decision.get("confidence", "NINGUNA"),
            "anchor": bool(decision.get("anchor")),
            "failures": list(decision.get("failures") or []),
            "stake": stake,
            "stake_fraction": float(stake.get("stake_fraction", 0.0)) if stake else 0.0,
            "stake_amount": float(stake.get("stake_amount", 0.0)) if stake else 0.0,
            "stake_applied": bool(stake.get("applied")) if stake else False,
            "stake_notes": list(stake.get("notes") or []) if stake else [],
            "source_draw": pending.get("source_draw") if pending else None,
            "last_number": pending.get("last_number") if pending else None,
            "bankroll": self.bankroll,
            "payout": self.payout.name if self.payout else None,
            "window": [self.spec.probability_min, self.spec.probability_max],
            "max_spread": self.spec.max_spread,
            "baseline": BASELINE,
        }

    def _alert_stats(self) -> dict:
        stats = self._state.stats
        alerts = stats["alerts"]
        window = stats["window_rounds"]
        anchor = stats["anchor_alerts"]
        return {
            "rounds": stats["rounds"],
            "alerts": alerts,
            "alert_hits": stats["alert_hits"],
            "alert_rate": (stats["alert_hits"] / alerts) if alerts else 0.0,
            "window_rounds": window,
            "window_hits": stats["window_hits"],
            "window_rate": (stats["window_hits"] / window) if window else 0.0,
            "anchor_alerts": anchor,
            "anchor_hits": stats["anchor_hits"],
            "anchor_rate": (stats["anchor_hits"] / anchor) if anchor else 0.0,
            "pauses": stats["pauses"],
            "baseline": BASELINE,
        }

    def _prediction_stats(self) -> dict:
        stats = self._state.stats
        markov_trials = stats["markov_trials"]
        heuristic_trials = stats["heuristic_trials"]
        return {
            "rounds": stats["rounds"],
            "markov_hits": stats["markov_hits"],
            "markov_trials": markov_trials,
            "markov_rate": (stats["markov_hits"] / markov_trials) if markov_trials else 0.0,
            "markov_top1_hits": stats["markov_top1_hits"],
            "markov_top1_rate": (
                stats["markov_top1_hits"] / markov_trials if markov_trials else 0.0
            ),
            "heuristic_hits": stats["heuristic_hits"],
            "heuristic_trials": heuristic_trials,
            "heuristic_rate": (
                stats["heuristic_hits"] / heuristic_trials if heuristic_trials else 0.0
            ),
            "heuristic_top1_hits": stats["heuristic_top1_hits"],
            "heuristic_top1_rate": (
                stats["heuristic_top1_hits"] / heuristic_trials if heuristic_trials else 0.0
            ),
            "baseline": BASELINE,
            "baseline_top5": BASELINE_TOP5,
        }

    def _alert_history(self) -> list[dict]:
        history: list[dict] = []
        for record in reversed(self._state.predictions):
            decision = record.get("decision") or {}
            if not decision.get("fired"):
                continue
            stake = decision.get("stake") or {}
            history.append(
                {
                    "source_draw": record.get("source_draw"),
                    "observed_draw": record.get("observed_draw"),
                    "number": decision.get("number"),
                    "probability": decision.get("probability"),
                    "confidence": decision.get("confidence"),
                    "anchor": bool(decision.get("anchor")),
                    "status": record.get("status"),
                    "actual_number": record.get("actual_number"),
                    "hit": record.get("alert_hit"),
                    "stake_fraction": float(stake.get("stake_fraction", 0.0)),
                    "stake_applied": bool(stake.get("applied")),
                }
            )
            if len(history) >= 100:
                break
        return history

    def _public_record(self, record: dict) -> dict:
        """VersiÃ³n ligera del registro para el navegador."""
        decision = record.get("decision") or {}
        stake = decision.get("stake") or {}
        return {
            "source_draw": record.get("source_draw"),
            "created_at": record.get("created_at"),
            "status": record.get("status"),
            "observed_draw": record.get("observed_draw"),
            "actual_number": record.get("actual_number"),
            "markov_rank": record.get("markov_rank"),
            "heuristic_rank": record.get("heuristic_rank"),
            "markov_forecast": record.get("markov_forecast") or [],
            "heuristic_forecast": record.get("heuristic_forecast") or [],
            "matched": record.get("matched"),
            "alert_fired": bool(decision.get("fired")),
            "alert_hit": record.get("alert_hit"),
            "alert_number": decision.get("number"),
            "alert_probability": decision.get("probability"),
        }
