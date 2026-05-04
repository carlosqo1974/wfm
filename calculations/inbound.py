"""
Inbound campaign staffing module.
Handles voice inbound campaigns with interval-level granularity.
"""
import math
from typing import List, Dict, Union, Optional
from .erlang import agents_for_service_level, service_level, average_speed_of_answer, occupancy, erlang_b


def build_interval_plan(
    interval_volumes: List[float],
    aht_seconds: Union[float, List[Optional[float]]],
    interval_minutes: int,
    target_sl: float,
    target_seconds: float,
    shrinkage: float,
    max_occupancy: float,
    trunk_blocking_target: float = 0.02,
) -> List[Dict]:
    """
    Build a full interval staffing plan for inbound campaign.

    interval_volumes: call volumes per interval (e.g., 48 values for 30-min intervals in 24h)
    aht_seconds: uniform AHT as float, or per-interval list (None entries fall back to list mean)
    interval_minutes: interval length in minutes (15, 30, or 60)
    target_sl: service level target (e.g., 0.80)
    target_seconds: answer within N seconds (e.g., 20)
    shrinkage: shrinkage percentage (e.g., 0.30)
    max_occupancy: max agent occupancy (e.g., 0.85)
    trunk_blocking_target: max acceptable blocking for trunk calc (e.g., 0.02)
    """
    # Resolve per-interval AHT list; fallback value = mean of provided values or 180 s
    _per_interval = isinstance(aht_seconds, (list, tuple))
    if _per_interval:
        _known = [v for v in aht_seconds if v is not None]
        _fallback = sum(_known) / len(_known) if _known else 180.0
    else:
        _fallback = float(aht_seconds)

    def _eff_aht(i: int) -> float:
        if not _per_interval:
            return _fallback
        v = aht_seconds[i] if i < len(aht_seconds) else None
        return float(v) if v is not None else _fallback

    interval_seconds = interval_minutes * 60
    results = []

    for i, volume in enumerate(interval_volumes):
        hour = (i * interval_minutes) // 60
        minute = (i * interval_minutes) % 60
        interval_label = f"{hour:02d}:{minute:02d}"
        eff_aht = _eff_aht(i)

        if volume <= 0:
            results.append({
                "interval": interval_label,
                "calls": 0,
                "aht_seconds": round(eff_aht, 1),
                "traffic_intensity": 0,
                "productive_agents": 0,
                "gross_agents": 0,
                "service_level": 1.0,
                "asa_seconds": 0,
                "occupancy": 0.0,
                "trunks_needed": 0,
            })
            continue

        staffing = agents_for_service_level(
            calls_per_interval=volume,
            aht_seconds=eff_aht,
            interval_seconds=interval_seconds,
            target_sl=target_sl,
            target_seconds=target_seconds,
            max_occupancy=max_occupancy,
            shrinkage=shrinkage,
        )

        # Trunk dimensioning (Erlang B) — lines needed for blocking target
        # Traffic for trunks: all simultaneous calls (AHT includes wrap, but trunks only for talk+hold)
        traffic_trunks = staffing["traffic_intensity"]
        trunks = _trunks_for_blocking(traffic_trunks, trunk_blocking_target)

        results.append({
            "interval": interval_label,
            "calls": round(volume, 1),
            "aht_seconds": round(eff_aht, 1),
            "traffic_intensity": staffing["traffic_intensity"],
            "productive_agents": staffing["productive_agents"],
            "gross_agents": staffing["gross_agents"],
            "service_level": staffing["service_level"],
            "asa_seconds": staffing["asa_seconds"],
            "occupancy": staffing["occupancy"],
            "trunks_needed": trunks,
        })

    return results


def _trunks_for_blocking(traffic: float, max_blocking: float) -> int:
    """Find minimum trunks so Erlang B blocking <= max_blocking."""
    if traffic <= 0:
        return 0
    for n in range(1, 5000):
        from .erlang import erlang_b
        if erlang_b(n, traffic) <= max_blocking:
            return n
    return 5000


def summary_stats(plan: List[Dict]) -> Dict:
    """Aggregate summary statistics for the interval plan."""
    intervals_with_calls = [p for p in plan if p["calls"] > 0]
    if not intervals_with_calls:
        return {}

    total_calls = sum(p["calls"] for p in plan)
    peak_interval = max(intervals_with_calls, key=lambda x: x["calls"])
    peak_agents = max(p["gross_agents"] for p in plan)
    avg_sl = sum(p["service_level"] for p in intervals_with_calls) / len(intervals_with_calls)
    avg_occ = sum(p["occupancy"] for p in intervals_with_calls) / len(intervals_with_calls)
    avg_asa = sum(p["asa_seconds"] for p in intervals_with_calls) / len(intervals_with_calls)

    return {
        "total_calls": round(total_calls, 0),
        "peak_interval": peak_interval["interval"],
        "peak_calls": peak_interval["calls"],
        "peak_gross_agents": peak_agents,
        "average_service_level": round(avg_sl * 100, 1),
        "average_occupancy": round(avg_occ * 100, 1),
        "average_asa_seconds": round(avg_asa, 1),
    }


def sensitivity_analysis(
    calls_per_interval: float,
    aht_seconds: float,
    interval_minutes: int,
    target_sl: float,
    target_seconds: float,
    shrinkage: float,
    max_occupancy: float,
    agent_range: int = 10,
) -> List[Dict]:
    """
    Show SL and ASA for N-range to N+range agents around optimal.
    Useful for understanding the impact of adding/removing agents.
    """
    from .erlang import agents_for_service_level, service_level, average_speed_of_answer, occupancy as occ_fn

    base = agents_for_service_level(
        calls_per_interval, aht_seconds, interval_minutes * 60,
        target_sl, target_seconds, max_occupancy, shrinkage
    )
    optimal = base["productive_agents"]
    A = base["traffic_intensity"]

    rows = []
    for n in range(max(1, optimal - agent_range), optimal + agent_range + 1):
        sl = service_level(n, A, target_seconds, aht_seconds)
        asa = average_speed_of_answer(n, A, aht_seconds) if A < n else None
        occ = occ_fn(n, A)
        gross = math.ceil(n / (1 - shrinkage))
        rows.append({
            "productive_agents": n,
            "gross_agents": gross,
            "service_level": round(sl * 100, 1),
            "asa_seconds": round(asa, 1) if asa is not None else "∞",
            "occupancy": round(occ * 100, 1),
            "meets_target": sl >= target_sl,
        })
    return rows
