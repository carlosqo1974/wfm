"""
Erlang C and related calculations for WFM.
Industry-standard formulas used by Genesys, NICE, Verint, Aspect.
"""
import math


def erlang_c(agents: int, traffic_intensity: float) -> float:
    """
    Erlang C formula: probability a call must wait (P(wait)).

    agents: number of agents (N)
    traffic_intensity: A = arrival_rate * avg_handle_time (in same time unit)
    Returns probability between 0 and 1.
    """
    if agents <= 0:
        return 1.0
    if traffic_intensity <= 0:
        return 0.0
    if traffic_intensity >= agents:
        return 1.0

    N = agents
    A = traffic_intensity

    # Numerator: A^N / N! * N/(N-A)
    # Use log-space to avoid overflow
    log_num = N * math.log(A) - math.lgamma(N + 1) + math.log(N / (N - A))

    # Denominator: sum(A^k/k!, k=0..N-1) + numerator_raw
    log_terms = []
    for k in range(N):
        log_terms.append(k * math.log(A) - math.lgamma(k + 1) if A > 0 else (0 if k == 0 else -math.inf))

    max_log = max(log_terms + [log_num])
    sum_terms = sum(math.exp(t - max_log) for t in log_terms)
    num_raw = math.exp(log_num - max_log)
    denominator = sum_terms + num_raw

    return num_raw / denominator if denominator > 0 else 1.0


def service_level(agents: int, traffic_intensity: float, target_seconds: float, aht_seconds: float) -> float:
    """
    Service level: fraction of calls answered within target_seconds.
    Returns value between 0 and 1.
    """
    if agents <= 0 or aht_seconds <= 0:
        return 0.0
    if traffic_intensity <= 0:
        return 1.0
    if traffic_intensity >= agents:
        return 0.0

    ec = erlang_c(agents, traffic_intensity)
    exponent = -(agents - traffic_intensity) * (target_seconds / aht_seconds)
    sl = 1 - ec * math.exp(exponent)
    return max(0.0, min(1.0, sl))


def average_speed_of_answer(agents: int, traffic_intensity: float, aht_seconds: float) -> float:
    """Average Speed of Answer (ASA) in seconds."""
    if traffic_intensity >= agents or agents <= 0:
        return float('inf')
    if traffic_intensity <= 0:
        return 0.0
    ec = erlang_c(agents, traffic_intensity)
    return (ec * aht_seconds) / (agents - traffic_intensity)


def occupancy(agents: int, traffic_intensity: float) -> float:
    """Agent occupancy rate (0-1). Industry max recommended: 85%."""
    if agents <= 0:
        return 1.0
    return min(1.0, traffic_intensity / agents)


def agents_for_service_level(
    calls_per_interval: float,
    aht_seconds: float,
    interval_seconds: int,
    target_sl: float,
    target_seconds: float,
    max_occupancy: float = 0.85,
    shrinkage: float = 0.30,
) -> dict:
    """
    Calculate minimum agents needed to meet a service level target.

    calls_per_interval: calls arriving in the interval
    aht_seconds: average handle time in seconds
    interval_seconds: interval length (e.g. 1800 for 30 min)
    target_sl: target service level (e.g. 0.80 for 80%)
    target_seconds: answer time target in seconds (e.g. 20)
    max_occupancy: maximum allowed occupancy (e.g. 0.85)
    shrinkage: shrinkage factor (e.g. 0.30 for 30%)

    Returns dict with agents (productive), gross_agents (with shrinkage),
    service_level, asa, occupancy.
    """
    arrival_rate = calls_per_interval / interval_seconds  # calls per second
    traffic_intensity = arrival_rate * aht_seconds  # Erlangs

    if traffic_intensity <= 0:
        return {
            "traffic_intensity": 0, "productive_agents": 0, "gross_agents": 0,
            "service_level": 1.0, "asa_seconds": 0, "occupancy": 0.0,
            "shrinkage": shrinkage, "calls_per_interval": 0, "aht_seconds": 0
        }

    # Minimum agents needed to prevent infinite queue (must exceed traffic)
    min_agents = max(1, math.ceil(traffic_intensity) + 1)

    best_agents = min_agents
    for n in range(min_agents, min_agents + 500):
        occ = occupancy(n, traffic_intensity)
        if occ > max_occupancy:
            continue
        sl = service_level(n, traffic_intensity, target_seconds, aht_seconds)
        if sl >= target_sl:
            best_agents = n
            break
    else:
        # Couldn't meet SL within 500 agent search — return max checked
        best_agents = min_agents + 499

    gross = math.ceil(best_agents / (1 - shrinkage))
    sl_achieved = service_level(best_agents, traffic_intensity, target_seconds, aht_seconds)
    asa = average_speed_of_answer(best_agents, traffic_intensity, aht_seconds)
    occ = occupancy(best_agents, traffic_intensity)

    return {
        "traffic_intensity": round(traffic_intensity, 3),
        "productive_agents": best_agents,
        "gross_agents": gross,
        "service_level": round(sl_achieved, 4),
        "asa_seconds": round(asa, 1),
        "occupancy": round(occ, 4),
        "shrinkage": shrinkage,
        "calls_per_interval": calls_per_interval,
        "aht_seconds": aht_seconds,
    }


def erlang_b(agents: int, traffic_intensity: float) -> float:
    """
    Erlang B: probability of blocking (no queue, call lost).
    Used for trunk/line dimensioning.
    """
    if agents <= 0:
        return 1.0
    if traffic_intensity <= 0:
        return 0.0

    # Iterative Erlang B (numerically stable)
    b = 1.0
    for n in range(1, agents + 1):
        b = (traffic_intensity * b) / (n + traffic_intensity * b)
    return b
