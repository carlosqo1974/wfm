"""
Forecasting module: historical pattern analysis, trend/seasonality decomposition.
Simple but effective — similar to base methods in NICE/Verint without ML overhead.
"""
import math
from typing import List, Dict, Optional
from datetime import date, timedelta


def moving_average(data: List[float], window: int) -> List[Optional[float]]:
    """Simple moving average for smoothing historical data."""
    result = []
    for i in range(len(data)):
        if i < window - 1:
            result.append(None)
        else:
            result.append(sum(data[i - window + 1:i + 1]) / window)
    return result


def weighted_moving_average(data: List[float], weights: List[float]) -> List[Optional[float]]:
    """Weighted moving average — recent data weighted more heavily."""
    w = len(weights)
    total_weight = sum(weights)
    result = []
    for i in range(len(data)):
        if i < w - 1:
            result.append(None)
        else:
            wma = sum(data[i - w + k] * weights[k] for k in range(w)) / total_weight
            result.append(round(wma, 2))
    return result


def day_of_week_factors(daily_volumes: List[float]) -> List[float]:
    """
    Compute day-of-week seasonality factors from historical data.
    Returns 7 factors (Mon-Sun), normalized to average=1.0.
    """
    if len(daily_volumes) < 7:
        return [1.0] * 7

    # Accumulate by weekday
    day_sums = [0.0] * 7
    day_counts = [0] * 7
    avg_daily = sum(daily_volumes) / len(daily_volumes)

    for i, v in enumerate(daily_volumes):
        dow = i % 7
        day_sums[dow] += v
        day_counts[dow] += 1

    factors = []
    for d in range(7):
        if day_counts[d] > 0 and avg_daily > 0:
            day_avg = day_sums[d] / day_counts[d]
            factors.append(round(day_avg / avg_daily, 4))
        else:
            factors.append(1.0)
    return factors


def hour_of_day_factors(hourly_data: List[float]) -> List[float]:
    """
    Compute intraday distribution factors from historical hourly data.
    Returns 24 factors summing to 24 (each represents proportion vs flat).
    Handles 30-min intervals too (returns 48 factors summing to 48).
    """
    n = len(hourly_data)
    total = sum(hourly_data)
    if total == 0:
        return [1.0] * n
    avg = total / n
    return [round(v / avg, 4) if avg > 0 else 1.0 for v in hourly_data]


def forecast_volume(
    historical_daily: List[float],
    forecast_days: int,
    method: str = "wma",
    growth_rate: float = 0.0,
    intraday_profile: Optional[List[float]] = None,
    interval_minutes: int = 30,
) -> Dict:
    """
    Forecast future volumes.

    historical_daily: daily call volumes (list, most recent last)
    forecast_days: number of days to forecast
    method: 'wma' (weighted moving avg) | 'sma' (simple moving avg)
    growth_rate: annual growth rate (e.g., 0.05 for 5% YoY)
    intraday_profile: list of interval volumes for a typical day (optional)
    interval_minutes: interval granularity for intraday expansion

    Returns dict with daily forecasts and optionally intraday expansion.
    """
    # Daily forecast
    if method == "wma":
        # Exponential-style weights: most recent = highest weight
        window = min(8, len(historical_daily))
        weights = [2 ** i for i in range(window)]
        wma = weighted_moving_average(historical_daily, weights)
        base_forecast = next((v for v in reversed(wma) if v is not None), None)
    else:
        window = min(7, len(historical_daily))
        sma = moving_average(historical_daily, window)
        base_forecast = next((v for v in reversed(sma) if v is not None), None)

    if base_forecast is None:
        base_forecast = sum(historical_daily) / len(historical_daily) if historical_daily else 0

    # DoW factors
    dow_factors = day_of_week_factors(historical_daily)
    day_names = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

    # Determine starting DoW based on history length
    start_dow = len(historical_daily) % 7

    daily_forecasts = []
    for d in range(forecast_days):
        dow = (start_dow + d) % 7
        daily_growth = (1 + growth_rate) ** (d / 365)
        volume = base_forecast * dow_factors[dow] * daily_growth
        daily_forecasts.append({
            "day": d + 1,
            "day_name": day_names[dow],
            "forecast_volume": round(max(0, volume), 0),
            "dow_factor": dow_factors[dow],
        })

    result = {
        "base_daily_forecast": round(base_forecast, 0),
        "method": method,
        "growth_rate_annual": growth_rate,
        "dow_factors": {day_names[i]: dow_factors[i] for i in range(7)},
        "daily_forecasts": daily_forecasts,
    }

    # Intraday expansion
    if intraday_profile:
        factors = hour_of_day_factors(intraday_profile)
        intervals_per_day = 24 * 60 // interval_minutes
        avg_factor = sum(factors) / len(factors) if factors else 1

        expanded = []
        for day in daily_forecasts:
            daily_total = day["forecast_volume"]
            intervals = []
            for idx, f in enumerate(factors[:intervals_per_day]):
                hour = (idx * interval_minutes) // 60
                minute = (idx * interval_minutes) % 60
                interval_vol = (daily_total / intervals_per_day) * (f / avg_factor) if avg_factor else 0
                intervals.append({
                    "interval": f"{hour:02d}:{minute:02d}",
                    "forecast_volume": round(max(0, interval_vol), 1),
                })
            expanded.append({"day": day["day"], "day_name": day["day_name"], "intervals": intervals})
        result["intraday_expansion"] = expanded

    return result


def shrinkage_components() -> Dict:
    """
    Standard shrinkage components (industry reference values).
    Total shrinkage typically 25-35% in contact centers.
    """
    return {
        "external": {
            "vacaciones": 5.0,
            "enfermedad_ausencia": 4.0,
            "permisos": 2.0,
            "subtotal": 11.0,
        },
        "internal": {
            "capacitacion": 4.0,
            "reuniones": 2.0,
            "coaching": 1.5,
            "breaks_almuerzo": 8.5,
            "tiempo_no_productivo": 2.0,
            "subtotal": 18.0,
        },
        "total_reference": 29.0,
    }
