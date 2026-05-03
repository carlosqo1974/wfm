"""Planificador básico de turnos para Perú (referencial, no asesoría legal)."""
from dataclasses import dataclass
from typing import List, Dict
import math


@dataclass
class ShiftRule:
    max_hours_day: int = 8
    max_hours_week: int = 48
    break_minutes: int = 45
    min_rest_hours: int = 12
    max_consecutive_days: int = 6


def _hour_from_label(label: str) -> int:
    return int(label.split(":")[0])


def dimension_shifts_peru(interval_plan: List[Dict], interval_minutes: int, rule: ShiftRule | None = None) -> Dict:
    rule = rule or ShiftRule()
    # consolidar a requerimiento por hora
    hourly = [0] * 24
    for row in interval_plan:
        req = int(math.ceil(float(row.get("required_agents", 0))))
        h = _hour_from_label(row.get("interval", "00:00"))
        hourly[h] = max(hourly[h], req)

    shifts = []
    max_span = 9  # 8h efectivas + 45m descanso aprox.

    for start in range(0, 24):
        end = min(24, start + max_span)
        demand_slice = hourly[start:end]
        if not demand_slice or max(demand_slice) == 0:
            continue
        peak = max(demand_slice)
        avg = sum(demand_slice) / len(demand_slice)
        # plantilla del turno basada en mezcla estándar: cubrir promedio + buffer pico
        agents = int(math.ceil(avg * 0.8 + peak * 0.2))
        if agents <= 0:
            continue
        shifts.append({
            "name": f"T{start:02d}",
            "start": f"{start:02d}:00",
            "end": f"{end:02d}:00",
            "paid_hours": rule.max_hours_day,
            "break_minutes": rule.break_minutes,
            "agents": agents,
            "covers_peak": peak,
        })

    # simplificar: quedarnos con turnos cada 2h para evitar sobrefragmentación
    compact = [s for i, s in enumerate(shifts) if i % 2 == 0]
    if not compact and shifts:
        compact = [shifts[0]]

    total_agents_day = max(hourly) if hourly else 0
    weekly_hours_per_agent = rule.max_hours_week

    return {
        "legal": {
            "country": "Perú",
            "max_hours_day": rule.max_hours_day,
            "max_hours_week": rule.max_hours_week,
            "min_rest_hours": rule.min_rest_hours,
            "max_consecutive_days": rule.max_consecutive_days,
            "note": "Modelo referencial basado en jornada máxima 8h/día y 48h/semana.",
        },
        "hourly_required": hourly,
        "recommended_shifts": compact,
        "summary": {
            "peak_agents": max(hourly) if hourly else 0,
            "total_shift_lines": sum(s["agents"] for s in compact),
            "suggested_fte_pool": total_agents_day,
            "weekly_hours_per_agent": weekly_hours_per_agent,
        },
    }
