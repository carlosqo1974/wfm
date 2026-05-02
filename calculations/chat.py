"""
Chat / messaging campaign staffing module.
Agents handle multiple concurrent sessions — modified Erlang C approach.
Industry standard: Genesys, LivePerson, Zendesk methodology.
"""
import math
from typing import List, Dict
from .erlang import erlang_c, service_level as erlang_sl, occupancy as erlang_occ


def effective_aht(session_aht_seconds: float, concurrency: float) -> float:
    """
    Effective AHT when agents handle multiple concurrent chats.
    With concurrency C, agent is busy for AHT but switches between sessions.
    Effective capacity per agent = AHT / concurrency for staffing purposes.
    """
    if concurrency <= 0:
        return session_aht_seconds
    return session_aht_seconds / concurrency


def agents_for_chat(
    sessions_per_interval: float,
    session_aht_seconds: float,
    interval_minutes: int,
    max_concurrency: float,
    target_response_seconds: float,
    target_response_rate: float,
    shrinkage: float,
    max_occupancy: float,
) -> Dict:
    """
    Calculate agents needed for chat campaigns.

    sessions_per_interval: chat sessions arriving in interval
    session_aht_seconds: avg total session duration (first response to close)
    interval_minutes: interval length in minutes
    max_concurrency: max simultaneous chats per agent (e.g., 3)
    target_response_seconds: target initial response time (e.g., 30)
    target_response_rate: % sessions receiving initial response within target (e.g., 0.80)
    shrinkage: shrinkage factor
    max_occupancy: max occupancy (typically lower for chat, e.g., 0.80)
    """
    interval_seconds = interval_minutes * 60

    if sessions_per_interval <= 0:
        return {
            "sessions": 0, "concurrency": max_concurrency, "productive_agents": 0,
            "gross_agents": 0, "service_level": 1.0, "occupancy": 0.0,
            "traffic_intensity": 0, "effective_capacity": 0,
        }

    arrival_rate = sessions_per_interval / interval_seconds  # sessions per second

    # Effective traffic intensity accounting for concurrency
    # Each agent handles max_concurrency sessions simultaneously
    # From Erlang perspective: treat effective AHT = session_aht / concurrency
    eff_aht = session_aht_seconds  # actual session duration
    traffic_intensity = arrival_rate * eff_aht  # Erlangs (sessions in system)

    # Effective agents (each agent = max_concurrency capacity units)
    # We need ceil(traffic_intensity / max_concurrency) base agents, then optimize
    min_agent_seats = max(1, math.ceil(traffic_intensity / max_concurrency) + 1)

    # Find agents meeting SL target
    best_agents = min_agent_seats
    for n in range(min_agent_seats, min_agent_seats + 300):
        effective_n = n * max_concurrency  # capacity units
        if effective_n <= traffic_intensity:
            continue

        occ = min(1.0, traffic_intensity / effective_n)
        if occ > max_occupancy:
            continue

        # Use Erlang C with effective capacity
        sl = erlang_sl(int(effective_n), traffic_intensity, target_response_seconds, eff_aht)
        if sl >= target_response_rate:
            best_agents = n
            break
    else:
        best_agents = min_agent_seats + 299

    effective_n = best_agents * max_concurrency
    sl_achieved = erlang_sl(int(effective_n), traffic_intensity, target_response_seconds, eff_aht)
    occ = min(1.0, traffic_intensity / effective_n) if effective_n > 0 else 0

    gross = math.ceil(best_agents / (1 - shrinkage))

    # Simultaneous sessions at peak
    avg_concurrent_sessions = min(traffic_intensity, effective_n)

    return {
        "sessions_per_interval": round(sessions_per_interval, 1),
        "session_aht_seconds": session_aht_seconds,
        "max_concurrency": max_concurrency,
        "traffic_intensity": round(traffic_intensity, 3),
        "productive_agents": best_agents,
        "gross_agents": gross,
        "effective_capacity_units": round(effective_n, 0),
        "service_level": round(sl_achieved, 4),
        "occupancy": round(occ, 4),
        "avg_concurrent_sessions": round(avg_concurrent_sessions, 1),
        "shrinkage": shrinkage,
    }


def build_chat_interval_plan(
    interval_sessions: List[float],
    session_aht_seconds: float,
    interval_minutes: int,
    max_concurrency: float,
    target_response_seconds: float,
    target_response_rate: float,
    shrinkage: float,
    max_occupancy: float,
) -> List[Dict]:
    """Build interval-level chat staffing plan."""
    results = []
    for i, sessions in enumerate(interval_sessions):
        hour = (i * interval_minutes) // 60
        minute = (i * interval_minutes) % 60
        label = f"{hour:02d}:{minute:02d}"

        r = agents_for_chat(
            sessions, session_aht_seconds, interval_minutes,
            max_concurrency, target_response_seconds, target_response_rate,
            shrinkage, max_occupancy
        )
        r["interval"] = label
        results.append(r)

    return results


def concurrency_sensitivity(
    sessions_per_interval: float,
    session_aht_seconds: float,
    interval_minutes: int,
    target_response_seconds: float,
    target_response_rate: float,
    shrinkage: float,
    max_occupancy: float,
) -> List[Dict]:
    """Show impact of different concurrency levels (1-5) on staffing."""
    rows = []
    for c in [1, 1.5, 2, 2.5, 3, 3.5, 4, 5]:
        r = agents_for_chat(
            sessions_per_interval, session_aht_seconds, interval_minutes,
            c, target_response_seconds, target_response_rate,
            shrinkage, max_occupancy
        )
        rows.append({
            "concurrency": c,
            "productive_agents": r["productive_agents"],
            "gross_agents": r["gross_agents"],
            "service_level_pct": round(r["service_level"] * 100, 1),
            "occupancy_pct": round(r["occupancy"] * 100, 1),
        })
    return rows
