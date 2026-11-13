"""Carga del histórico (CSV o filas ya leídas) a arreglos en orden cronológico."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

CSV_PATH = Path(__file__).resolve().parents[1] / "Resultados_RuletaExpress.csv"
PRIMER_TURNO = 5 * 60 + 3      # 05:03
ULTIMO_TURNO = 21 * 60 + 58    # 21:58
TURNOS_DIA = 204


@dataclass
class Datos:
    sorteo: np.ndarray   # identificador entero, ascendente
    num: np.ndarray      # resultado 0..18
    ts: list             # datetime (hora de RD, sin zona)
    turno: np.ndarray    # 0 = 05:03 ... 203 = 21:58
    dia: np.ndarray      # índice de jornada 0..D-1
    consec: np.ndarray   # True si el sorteo anterior es el inmediato (sin hueco)

    def __len__(self) -> int:
        return len(self.num)


def turno_de(ts: datetime) -> int:
    return (ts.hour * 60 + ts.minute - PRIMER_TURNO) // 5


def hora_de_turno(turno: int) -> str:
    m = PRIMER_TURNO + 5 * int(turno)
    return f"{m // 60:02d}:{m % 60:02d}"


def siguiente_instante(ts: datetime) -> datetime:
    """Hora prevista del sorteo siguiente al de `ts`."""
    if ts.hour * 60 + ts.minute >= ULTIMO_TURNO:
        return (ts + timedelta(days=1)).replace(hour=5, minute=3)
    return ts + timedelta(minutes=5)


def desde_filas(rows) -> Datos:
    """Acepta filas con Fecha, Hora, No_Sorteo, Numero en cualquier orden."""
    filas = {}
    for row in rows:
        m = re.search(r"(\d+)\s*$", str(row.get("No_Sorteo") or ""))
        try:
            n = int(str(row.get("Numero") or "").strip())
            ts = datetime.strptime(f"{str(row['Fecha']).strip()} {str(row['Hora']).strip()}", "%d/%m/%Y %I:%M %p")
        except (ValueError, KeyError, AttributeError):
            continue
        if m and 0 <= n <= 18:
            filas[int(m.group(1))] = (n, ts)
    ids = sorted(filas)
    sorteo = np.array(ids, dtype=np.int64)
    num = np.array([filas[i][0] for i in ids], dtype=np.int64)
    ts = [filas[i][1] for i in ids]
    turno = np.array([turno_de(t) for t in ts], dtype=np.int64)
    fechas = [t.date() for t in ts]
    orden = {d: i for i, d in enumerate(sorted(set(fechas)))}
    dia = np.array([orden[d] for d in fechas], dtype=np.int64)
    consec = np.r_[False, np.diff(sorteo) == 1] if len(ids) else np.zeros(0, bool)
    return Datos(sorteo, num, ts, turno, dia, consec)


def cargar(path: Path = CSV_PATH) -> Datos:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as fh:
        return desde_filas(list(csv.DictReader(fh)))
