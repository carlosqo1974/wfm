"""
Outbound campaign staffing module.
Supports predictive, progressive, and preview dialing strategies.
"""
import math
from typing import List, Dict


DIALING_MODES = {
    "predictive": {
        "description": "Marcación predictiva — sistema marca anticipadamente múltiples contactos",
        "efficiency_factor": 1.25,   # agents handle more calls due to predictive pacing
        "abandonment_risk": "alto",
    },
    "progressive": {
        "description": "Marcación progresiva — una llamada por agente disponible",
        "efficiency_factor": 1.0,
        "abandonment_risk": "bajo",
    },
    "preview": {
        "description": "Marcación preview — agente revisa ficha antes de marcar",
        "efficiency_factor": 0.80,   # less efficient, agent controls pacing
        "abandonment_risk": "muy bajo",
    },
    "power": {
        "description": "Marcación power — ratio fijo de llamadas por agente",
        "efficiency_factor": 1.10,
        "abandonment_risk": "medio",
    },
}


def agents_needed(
    records_to_contact: int,
    contact_rate: float,
    right_party_rate: float,
    aht_seconds: float,
    available_hours: float,
    occupancy_target: float,
    dial_mode: str = "progressive",
    max_abandonment: float = 0.03,
    redial_factor: float = 1.0,
) -> Dict:
    """
    Calculate agents needed for an outbound campaign.

    records_to_contact: total records in the list to work
    contact_rate: probability of the phone being answered (e.g., 0.35 for 35%)
    right_party_rate: of contacts, fraction reaching the right person (e.g., 0.70)
    aht_seconds: average handle time for right-party contacts
    available_hours: working hours available to run the campaign
    occupancy_target: target agent occupancy (e.g., 0.80)
    dial_mode: predictive | progressive | preview | power
    max_abandonment: max abandonment rate for compliance (e.g., 0.03 = 3%)
    redial_factor: multiplier for records needing redial (e.g., 1.20 for 20% redials)
    """
    mode = DIALING_MODES.get(dial_mode, DIALING_MODES["progressive"])
    efficiency = mode["efficiency_factor"]

    # Total dial attempts needed
    total_dials = math.ceil(records_to_contact * redial_factor / contact_rate)

    # Right-party contacts
    right_party_contacts = records_to_contact * contact_rate * right_party_rate

    # Total productive seconds needed
    total_productive_seconds = right_party_contacts * aht_seconds

    # Available agent-seconds per agent per campaign window
    agent_seconds_per_agent = available_hours * 3600 * occupancy_target * efficiency

    # Raw agents needed
    raw_agents = total_productive_seconds / agent_seconds_per_agent
    productive_agents = max(1, math.ceil(raw_agents))

    # Dials per agent per hour (efficiency already encodes lines-per-agent ratio)
    dial_rate_per_agent = efficiency * 3600 / aht_seconds

    # Abandonment: when efficiency > 1 the dialer places more calls than agents can absorb.
    # Upper-bound estimate: (efficiency - 1) / efficiency of answered calls find no agent.
    # For progressive/preview (efficiency ≤ 1) there is no dialer-driven abandonment.
    est_abandonment = (efficiency - 1.0) / efficiency if efficiency > 1.0 else 0.0

    # Campaign duration estimate (dial_rate_per_agent already includes efficiency)
    campaign_hours = total_dials / (productive_agents * dial_rate_per_agent) if productive_agents > 0 else 0

    hourly_volume = right_party_contacts / available_hours if available_hours > 0 else 0

    return {
        "dial_mode": dial_mode,
        "dial_mode_description": mode["description"],
        "records_to_contact": records_to_contact,
        "total_dials_needed": total_dials,
        "right_party_contacts": round(right_party_contacts, 0),
        "contact_rate_pct": round(contact_rate * 100, 1),
        "right_party_rate_pct": round(right_party_rate * 100, 1),
        "aht_seconds": aht_seconds,
        "available_hours": available_hours,
        "productive_agents": productive_agents,
        "occupancy_target_pct": round(occupancy_target * 100, 1),
        "efficiency_factor": efficiency,
        "estimated_abandonment_pct": round(est_abandonment * 100, 2),
        "max_abandonment_pct": round(max_abandonment * 100, 1),
        "abandonment_compliant": est_abandonment <= max_abandonment,
        "estimated_campaign_hours": round(campaign_hours, 2),
        "hourly_right_party_contacts": round(hourly_volume, 1),
        "abandonment_risk": mode["abandonment_risk"],
    }


def build_outbound_interval_plan(
    hourly_targets: List[float],
    contact_rate: float,
    right_party_rate: float,
    aht_seconds: float,
    occupancy_target: float,
    dial_mode: str = "progressive",
    shrinkage: float = 0.15,
) -> List[Dict]:
    """
    Build hour-by-hour outbound plan given hourly right-party contact targets.

    hourly_targets: list of right-party contacts to achieve each hour
    """
    results = []
    for hour, target_contacts in enumerate(hourly_targets):
        label = f"{hour:02d}:00"

        if target_contacts <= 0:
            results.append({
                "hour": label,
                "target_contacts": 0,
                "dials_needed": 0,
                "productive_agents": 0,
                "gross_agents": 0,
                "occupancy": 0.0,
            })
            continue

        # Dials needed this hour
        dials = math.ceil(target_contacts / (contact_rate * right_party_rate))

        # Agent-seconds needed for talk time this hour
        talk_seconds_needed = target_contacts * aht_seconds

        # Per-agent capacity this hour
        mode = DIALING_MODES.get(dial_mode, DIALING_MODES["progressive"])
        per_agent_seconds = 3600 * occupancy_target * mode["efficiency_factor"]

        prod_agents = max(1, math.ceil(talk_seconds_needed / per_agent_seconds)) if talk_seconds_needed > 0 else 0
        gross = math.ceil(prod_agents / (1 - shrinkage)) if prod_agents > 0 else 0
        actual_occ = min(1.0, talk_seconds_needed / (prod_agents * 3600)) if prod_agents > 0 else 0

        results.append({
            "hour": label,
            "target_contacts": round(target_contacts, 0),
            "dials_needed": dials,
            "productive_agents": prod_agents,
            "gross_agents": gross,
            "occupancy": round(actual_occ * 100, 1),
        })

    return results


def compare_dial_modes(
    records_to_contact: int,
    contact_rate: float,
    right_party_rate: float,
    aht_seconds: float,
    available_hours: float,
    occupancy_target: float,
) -> List[Dict]:
    """Compare all dialing modes for a given campaign."""
    results = []
    for mode in DIALING_MODES:
        r = agents_needed(
            records_to_contact, contact_rate, right_party_rate,
            aht_seconds, available_hours, occupancy_target, dial_mode=mode
        )
        results.append({
            "mode": mode,
            "description": r["dial_mode_description"],
            "productive_agents": r["productive_agents"],
            "est_abandonment_pct": r["estimated_abandonment_pct"],
            "campaign_hours": r["estimated_campaign_hours"],
            "abandonment_risk": r["abandonment_risk"],
            "compliant": r["abandonment_compliant"],
        })
    return results
