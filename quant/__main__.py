"""CLI: `python -m quant [--csv RUTA] [--prng] [--out-dir DIR]`.

Genera `informe_quant.json` e `informe_quant.md` reproducibles (hash del
dataset, particiones, hiperparámetros, priors, semillas y resultados).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .data import load_csv
from .report import DEFAULT_SEED, build_report, to_markdown

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Informe cuantitativo reproducible de Ruleta Express.")
    parser.add_argument("--csv", type=Path, default=ROOT / "Resultados_RuletaExpress.csv")
    parser.add_argument("--out-dir", type=Path, default=ROOT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--mc-sims", type=int, default=2000)
    parser.add_argument("--prng", action="store_true", help="Ejecuta la búsqueda SMT acotada del catálogo LCG.")
    parser.add_argument("--prng-seconds", type=float, default=600.0)
    parser.add_argument("--prng-timeout-ms", type=int, default=2000)
    args = parser.parse_args(argv)

    dataset = load_csv(args.csv)
    report = build_report(
        dataset,
        seed=args.seed,
        mc_sims=args.mc_sims,
        run_prng_solver=args.prng,
        prng_kwargs={"max_seconds": args.prng_seconds, "timeout_ms": args.prng_timeout_ms} if args.prng else None,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "informe_quant.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (args.out_dir / "informe_quant.md").write_text(to_markdown(report), encoding="utf-8")
    decision = report["decision"]
    print(f"Decisión: {decision['status']} · modo {decision['trading_mode']} · señales {decision['signals_allowed']}")
    for reason in decision["reasons"]:
        print(f"  - {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
