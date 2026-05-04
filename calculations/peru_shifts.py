"""
Gestor de turnos para contact centers en Perú.
Referencial — no constituye asesoría legal.

Marco legal:
  Ley N° 27671 / D.S. N° 007-2002-TR — Jornada máxima 8h/día · 48h/semana
  Refrigerio mínimo 45 min, NO computable como jornada efectiva
  Trabajo nocturno 22:00-06:00 → recargo 35 % sobre RMV
  Descanso mínimo entre jornadas: 12 horas
"""
import math
from dataclasses import dataclass
from typing import List, Dict, Optional


@dataclass
class ShiftRule:
    max_hours_day: int = 8
    max_hours_week: int = 48
    break_minutes: int = 45
    min_rest_hours: int = 12
    max_consecutive_days: int = 6
    night_start: int = 22
    night_end: int = 6
    night_premium_pct: float = 35.0


def _hour_from_label(label: str) -> int:
    try:
        return int(str(label).split(":")[0])
    except (ValueError, IndexError):
        return 0


def _legal_dict(rule: ShiftRule) -> Dict:
    return {
        "country": "Perú",
        "max_hours_day": rule.max_hours_day,
        "max_hours_week": rule.max_hours_week,
        "min_lunch_minutes": rule.break_minutes,
        "min_rest_hours": rule.min_rest_hours,
        "night_hours": f"{rule.night_start:02d}:00–{rule.night_end:02d}:00",
        "night_premium_pct": rule.night_premium_pct,
        "note": "El refrigerio no es parte de la jornada efectiva (Ley N° 27671).",
    }


def _night_hours_in_shift(start_h: float, paid_h: float, lunch_min: int) -> float:
    span = paid_h + lunch_min / 60
    night = 0.0
    t = start_h
    for _ in range(int(span * 2)):
        h = t % 24
        if h >= 22 or h < 6:
            night += 0.5
        t += 0.5
    return round(night, 1)


def _validate_shift(sd: Dict, rule: ShiftRule) -> List[str]:
    warnings = []
    if sd.get("paid_hours", 8) > rule.max_hours_day:
        warnings.append(f"Jornada supera el máximo legal de {rule.max_hours_day}h/día")
    if sd.get("lunch_minutes", 0) < rule.break_minutes:
        warnings.append(f"Refrigerio inferior al mínimo legal de {rule.break_minutes} min")
    return warnings


def _best_lunch_offset(
    start_h: int,
    paid_h: float,
    hourly: List[int],
    reserved_hours: Optional[set] = None,
) -> float:
    """
    Encuentra la mejor hora para el almuerzo dentro del turno:
    la de menor demanda, evitando las primeras 2h y la última 1h de trabajo.
    reserved_hours: horas ya reservadas por otros agentes del mismo turno.
    """
    min_offset = min(2.0, paid_h / 3)
    max_offset = paid_h - 1.0
    if max_offset <= min_offset:
        return min_offset

    best_offset = min_offset
    lowest = float("inf")
    offset = min_offset
    while offset <= max_offset:
        h = int(start_h + offset) % 24
        if reserved_hours and h in reserved_hours:
            offset += 0.5
            continue
        d = hourly[h]
        if d < lowest:
            lowest = d
            best_offset = offset
        offset += 0.5
    return round(best_offset, 1)


# ── Dimensionamiento ──────────────────────────────────────────────────────────

def dimension_shifts_peru(
    interval_plan: List[Dict],
    interval_minutes: int,
    rule: Optional[ShiftRule] = None,
    shift_defs: Optional[List[Dict]] = None,
) -> Dict:
    """
    Genera plan de turnos cubriendo la curva de demanda horaria.

    shift_defs: plantillas {name, paid_hours, lunch_minutes, color}.
                El gestor coloca cada plantilla en el horario óptimo y
                encuentra el mejor momento para el refrigerio.
    """
    rule = rule or ShiftRule()

    hourly = [0] * 24
    for row in interval_plan:
        req = int(math.ceil(float(row.get("required_agents", 0))))
        h = _hour_from_label(row.get("interval", "00:00"))
        hourly[h] = max(hourly[h], req)

    peak = max(hourly, default=0)
    if peak == 0:
        return {
            "legal": _legal_dict(rule),
            "hourly_required": hourly,
            "recommended_shifts": [],
            "summary": {"peak_agents": 0, "total_shift_lines": 0,
                        "suggested_fte_pool": 0, "weekly_hours_per_agent": rule.max_hours_week},
        }

    if shift_defs:
        shifts = _place_shifts_smart(hourly, shift_defs, rule)
    else:
        shifts = _dimension_greedy(hourly, rule)

    fte_pool = sum(s["agents"] for s in shifts)

    # Cobertura neta por hora: descontando agentes en almuerzo
    # Usa coverage_hours (ya calculado en el ILP, maneja starts negativos/cruce de medianoche)
    net_coverage = [0] * 24
    for s in shifts:
        lunch_count: Dict[int, int] = {}
        for lp in s.get("lunch_positions", []):
            if lp:
                lh = int(lp.split(":")[0])
                lunch_count[lh] = lunch_count.get(lh, 0) + 1
        n = s["agents"]
        for h in s.get("coverage_hours", []):
            absent = lunch_count.get(h, 0)
            net_coverage[h] += max(0, n - absent)

    return {
        "legal": _legal_dict(rule),
        "hourly_required": hourly,
        "net_coverage": net_coverage,
        "recommended_shifts": shifts,
        "summary": {
            "peak_agents": peak,
            "total_shift_lines": fte_pool,
            "suggested_fte_pool": fte_pool,
            "weekly_hours_per_agent": rule.max_hours_week,
        },
    }


def _place_shifts_smart(hourly: List[int], shift_defs: List[Dict], rule: ShiftRule) -> List[Dict]:
    """Resuelve con ILP (PuLP + CBC); fallback al greedy si el solver falla."""
    try:
        result = _solve_ilp_shifts(hourly, shift_defs, rule)
        if result is not None:
            return result
    except Exception:
        pass
    return _dimension_greedy(hourly, rule)


def _solve_ilp_shifts(hourly: List[int], shift_defs: List[Dict], rule: ShiftRule) -> Optional[List[Dict]]:
    """
    Workforce Scheduling Problem como ILP (PuLP + CBC).

    Estrategia:
    1. MATRIZ DE COBERTURA: filas = (shift_def × start_hour), columnas = horas 0..23.
       Se permiten starts negativos (p.ej. -2 = 22:00 del día anterior) para cubrir
       demanda en las primeras horas sin forzar turnos que empiecen exactamente a las 00:00.
    2. min Σ x[t]  s.t.  Σ_t A[t,h]·x[t] ≥ hourly[h]  ∀h con demanda.
    3. Post-procesa almuerzos escalonados agente por agente (mínimo déficit).
    """
    import pulp

    first_h = next((h for h in range(24) if hourly[h] > 0), 0)
    last_h  = next((h for h in range(23, -1, -1) if hourly[h] > 0), 23)

    # ── 1. Templates ──────────────────────────────────────────────────────────────
    # min_start puede ser negativo: permite turnos que empezaron antes de medianoche.
    # today_start = max(0, start): horas del día actual que cubre el turno.
    # coverage_hours: lista de horas 0-23 efectivamente cubiertas hoy.
    templates = []
    for sdef in shift_defs:
        paid   = float(sdef["paid_hours"])
        lunch  = int(sdef["lunch_minutes"])
        span_h = math.ceil(paid + lunch / 60)

        min_start  = first_h - span_h + 1   # puede ser negativo
        last_start = last_h                  # incluye starts que sólo cubren la última hora

        for start in range(min_start, last_start + 1):
            today_start = max(0, start)
            end = min(24, start + span_h)
            if end <= today_start:
                continue
            cov_hours = list(range(today_start, end))
            if not any(hourly[h] > 0 for h in cov_hours):
                continue
            templates.append({
                "sdef":          sdef,
                "start":         start,        # lógico, puede ser negativo
                "today_start":   today_start,  # primera hora 0-23 cubierta
                "end":           end,           # última hora + 1, ≤ 24
                "paid":          paid,
                "lunch":         lunch,
                "cov_hours":     cov_hours,
            })

    if not templates:
        return []

    T = len(templates)

    # ── 2. ILP ────────────────────────────────────────────────────────────────────
    prob = pulp.LpProblem("WFM_Peru_WSP", pulp.LpMinimize)
    x    = [pulp.LpVariable(f"x_{t}", lowBound=0, cat="Integer") for t in range(T)]

    # Coste: el ILP minimiza total de agentes (objetivo principal = 1.0 por agente).
    # Tie-breaking con dos ε:
    #   waste      (1e-4): horas sin demanda cubiertas → evita 00:00 cuando no hay demanda.
    #   late_start (1e-5): inicio tardío respecto a first_h → prefiere starts tempranos.
    # waste >> late_start: un turno ajustado pero tarde (22:00 para cubrir 22-23) es
    # preferible a uno temprano con 7 horas muertas (15:00 para cubrir 22-23).
    def _template_cost(tmpl):
        cov_hours  = tmpl["cov_hours"]
        waste      = sum(1 for h in cov_hours if hourly[h] == 0)
        late_start = max(0, tmpl["start"] - first_h)
        return 1.0 + 1e-4 * waste + 1e-5 * late_start

    costs = [_template_cost(templates[t]) for t in range(T)]
    prob += pulp.lpSum(costs[t] * x[t] for t in range(T))

    # Cobertura por hora (usando today_start para starts negativos)
    for h in range(24):
        if hourly[h] <= 0:
            continue
        active = [t for t in range(T) if h in templates[t]["cov_hours"]]
        if active:
            prob += pulp.lpSum(x[t] for t in active) >= hourly[h], f"cov_h{h}"

    # Al menos 1 turno arrancando en first_h (mismo día, no del día anterior)
    open_t = [t for t in range(T) if templates[t]["start"] == first_h]
    if open_t:
        prob += pulp.lpSum(x[t] for t in open_t) >= 1, "force_open"

    solver = pulp.PULP_CBC_CMD(msg=0, timeLimit=15)
    prob.solve(solver)

    if pulp.LpStatus[prob.status] not in ("Optimal", "Feasible"):
        return None

    # ── 3. Post-proceso: escalonar almuerzos y calcular cobertura neta ────────────
    instance_counts: Dict[int, int] = {}
    placements     : List[Dict]     = []
    net_coverage   : List[int]      = [0] * 24

    for t in range(T):
        n = max(0, int(round(pulp.value(x[t]) or 0)))
        if n <= 0:
            continue

        tmpl        = templates[t]
        sdef        = tmpl["sdef"]
        start       = tmpl["start"]        # lógico (puede ser < 0)
        cov_hours   = tmpl["cov_hours"]
        paid        = tmpl["paid"]
        lunch       = tmpl["lunch"]

        # Hora de inicio/fin para mostrar (reloj de pared, 0-23)
        start_disp  = start % 24
        end_min_wall = start_disp * 60 + int(paid * 60) + lunch
        end_h_disp  = (end_min_wall // 60) % 24
        end_m_disp  = end_min_wall % 60
        crosses_mid = (start < 0) or (end_min_wall >= 24 * 60)

        tid = sdef.get("id", 0)
        instance_counts[tid] = instance_counts.get(tid, 0) + 1
        count = instance_counts[tid]
        name  = sdef["name"] if count == 1 else f"{sdef['name']} ({count})"

        reserved_hours  : set       = set()
        lunch_positions : List[str] = []
        lunch_offset_repr           = None

        for _ in range(n):
            if lunch > 0:
                min_off  = min(2.0, paid / 3)
                max_off  = paid - 1.0
                best_off = min_off
                best_key : tuple = (float("inf"), float("inf"))

                off = min_off
                while off <= max_off:
                    h = int(start + off) % 24
                    if h not in reserved_hours:
                        deficit = max(0, hourly[h] - max(0, net_coverage[h] - 1))
                        key = (deficit, hourly[h])
                        if key < best_key:
                            best_key = key
                            best_off = off
                    off += 0.5

                lh = int(start + best_off) % 24
                reserved_hours.add(lh)
                lunch_positions.append(f"{lh:02d}:00")
                if lunch_offset_repr is None:
                    lunch_offset_repr = best_off
                for h in cov_hours:
                    if h != lh:
                        net_coverage[h] += 1
            else:
                lunch_positions.append("")
                if lunch_offset_repr is None:
                    lunch_offset_repr = 0.0
                for h in cov_hours:
                    net_coverage[h] += 1

        lunch_offset_repr = lunch_offset_repr or 0.0
        first_lunch_h     = int(start + lunch_offset_repr) % 24

        placements.append({
            "name":              name,
            "start":             f"{start_disp:02d}:00",
            "end":               f"{end_h_disp:02d}:{end_m_disp:02d}",
            "crosses_midnight":  crosses_mid,
            # Horas lógicas (pueden ser negativas o >24) para el Gantt extendido
            "start_h":           start,
            "end_h":             round(start + paid + lunch / 60, 4),
            "paid_hours":        paid,
            "lunch_minutes":     lunch,
            "lunch_after_hours": lunch_offset_repr,
            "lunch_start":       f"{first_lunch_h:02d}:00",
            "lunch_positions":   lunch_positions,
            "agents":            n,
            "covers_peak":       max(hourly[h] for h in cov_hours) if cov_hours else 0,
            "night_hours":       _night_hours_in_shift(start_disp, paid, lunch),
            "shift_def_id":      tid,
            "color":             sdef.get("color", "#6366f1"),
            "warnings":          _validate_shift(sdef, rule),
            "coverage_hours":    cov_hours,
        })

    return placements


def _dimension_greedy(hourly: List[int], rule: ShiftRule) -> List[Dict]:
    """Fallback cuando no hay plantillas definidas."""
    span = rule.max_hours_day + math.ceil(rule.break_minutes / 60)
    step = rule.max_hours_day
    active = [h for h in range(24) if hourly[h] > 0]
    cursor = min(active)
    coverage = [0] * 24
    shifts = []

    while cursor < 24:
        end = min(24, cursor + span)
        agents = max((max(0, hourly[h] - coverage[h]) for h in range(cursor, end)), default=0)
        if agents > 0:
            for h in range(cursor, end):
                coverage[h] += agents
            lunch_offset = _best_lunch_offset(cursor, rule.max_hours_day, hourly)
            lunch_h = int(cursor + lunch_offset) % 24
            end_min = cursor * 60 + rule.max_hours_day * 60 + rule.break_minutes
            shifts.append({
                "name": f"T{cursor:02d}",
                "start": f"{cursor:02d}:00",
                "end": f"{end_min // 60 % 24:02d}:{end_min % 60:02d}",
                "crosses_midnight": False,
                "start_h": cursor,
                "end_h": cursor + rule.max_hours_day + rule.break_minutes / 60,
                "paid_hours": rule.max_hours_day,
                "lunch_minutes": rule.break_minutes,
                "lunch_after_hours": lunch_offset,
                "lunch_start": f"{lunch_h:02d}:00",
                "lunch_positions": [f"{lunch_h:02d}:00"],
                "agents": agents,
                "covers_peak": max(hourly[h] for h in range(cursor, end)),
                "night_hours": _night_hours_in_shift(cursor, rule.max_hours_day, rule.break_minutes),
                "warnings": [],
                "color": "#6366f1",
                "coverage_hours": list(range(cursor, end)),
            })
        next_cursor = cursor + step
        if next_cursor < 24 and all(coverage[h] >= hourly[h] for h in range(next_cursor, 24)):
            break
        cursor = next_cursor

    return shifts
