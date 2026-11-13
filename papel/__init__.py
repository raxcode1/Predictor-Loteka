"""Modo papel para Ruleta Express: predice ANTES del sorteo, registra y mide.

No apuesta ni toca el predictor existente. Origen: auditoría estadística del
CSV (nov-2026), que encontró en horario diurno (~08:30-20:30):
  - el número anterior casi nunca se repite,
  - los números de hace 2-3 sorteos salen de más.

Uso:
    python -m papel predecir     # registra la predicción del próximo sorteo
    python -m papel evaluar      # rellena resultados ya publicados
    python -m papel informe      # aciertos vs 1/19 (azar) y 1/PAGO (equilibrio)
    python -m papel ciclo        # predecir+evaluar en bucle
    python -m papel backtest     # walk-forward sobre el CSV histórico
"""

#: Retorno total por peso apostado a un número. SUPUESTO sin confirmar:
#: verifícalo en la agencia y cámbialo aquí si es otro.
PAGO = 18.0
K = 19
