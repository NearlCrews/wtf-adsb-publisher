"""Validate imported wind evidence and reproduce airspeed calculations.

The engine collects and caches weather data. These frozen helpers let the
publisher check the evidence and preserve its Open-Meteo attribution without
running an enrichment worker or accessing the receiver's journal.
"""

from datetime import datetime, timezone
from urllib.parse import urlencode

from . import limits
from .curation import utc_day
from .enrichment_rules import WIND_ATTRIBUTION as ATTRIBUTION  # noqa: F401  (re-exported)
from .enrichment_rules import WIND_SOURCE as SOURCE
from .rules import (
    MIN_REPORT_SECONDS,
    MIN_REPORTS,
    REPORTED_TAS_KT,
    number,
)

NAME = "wind"
BASE = "https://api.open-meteo.com/v1/forecast"
MAX_BYTES = 512 * 1024
LEVELS = (1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100)
GRID = 0.25
PAST_DAYS = 2
FORECAST_DAYS = 1
RETENTION = 86400
INTERVAL = 5
IDLE = 30
# A reading is matched to the nearest hour; beyond this the wind is unknown.
MAX_HOUR_GAP = 5400
LOOKBACK = 7 * 86400
# The most waiting events one pass reads: with the source off, windows that need the
# wind are passed over, and one that does not can still be settled.
PENDING_LIMIT = 50


def level_for(alt_baro_ft):
    """The listed pressure level nearest the ISA pressure of a pressure altitude."""
    pressure = limits.isa_pressure_hpa(alt_baro_ft)
    return min(LEVELS, key=lambda level: abs(level - pressure))


def grid(lat, lon):
    """The aircraft position rounded to the 0.25-degree grid sent to the service."""
    lat, lon = number(lat), number(lon)
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    return round(lat / GRID) * GRID, round(lon / GRID) * GRID


def askable(stamp, now):
    """Whether the service's past-days and forecast window covers a reading's time."""
    return now - PAST_DAYS * 86400 - 86400 <= stamp <= now + FORECAST_DAYS * 86400


def request_url(lat, lon):
    hourly = ",".join(f"wind_speed_{level}hPa,wind_direction_{level}hPa" for level in LEVELS)
    return (
        BASE
        + "?"
        + urlencode(
            {
                "latitude": f"{lat:.2f}",
                "longitude": f"{lon:.2f}",
                "hourly": hourly,
                "wind_speed_unit": "kn",
                "past_days": PAST_DAYS,
                "forecast_days": FORECAST_DAYS,
                "timezone": "UTC",
            }
        )
    )


def _hour(text):
    try:
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def compact(raw):
    """The hourly times (epoch seconds) and per-level speeds and directions, or raise."""
    hourly = raw.get("hourly") if isinstance(raw, dict) else None
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        raise ValueError("Invalid Open-Meteo response")
    times = [_hour(text) for text in hourly["time"]]
    if (
        not times
        or any(stamp is None for stamp in times)
        or len(times) > 24 * (PAST_DAYS + FORECAST_DAYS + 1)
    ):
        raise ValueError("Invalid Open-Meteo hours")
    levels = {}
    for level in LEVELS:
        speeds, directions = hourly.get(f"wind_speed_{level}hPa"), hourly.get(f"wind_direction_{level}hPa")
        if not isinstance(speeds, list) or not isinstance(directions, list):
            continue
        if len(speeds) != len(times) or len(directions) != len(times):
            raise ValueError("Invalid Open-Meteo series")
        levels[str(level)] = {
            "speed": [number(value) for value in speeds],
            "direction": [number(value) for value in directions],
        }
    if not levels:
        raise ValueError("Open-Meteo response has no pressure levels")
    return {"time": times, "levels": levels}


def wind_from_payload(payload, level, stamp):
    """The wind at a level for the hour nearest a timestamp, or None."""
    series = (payload.get("levels") or {}).get(str(level))
    times = payload.get("time") or []
    if not series or not times:
        return None
    index = min(range(len(times)), key=lambda i: abs(times[i] - stamp))
    if abs(times[index] - stamp) > MAX_HOUR_GAP:
        return None
    speed, direction = series["speed"][index], series["direction"][index]
    if speed is None or direction is None or speed < 0 or not 0 <= direction <= 360:
        return None
    return {"speed_kt": speed, "direction_deg": direction % 360, "level_hpa": level}


def window_samples(evidence):
    """The confirmation window's reports with their inputs, oldest first; the peak
    report stands in for an older row that kept no inputs."""
    confirmation = evidence.get("confirmation") if isinstance(evidence.get("confirmation"), dict) else {}
    samples = [
        sample
        for sample in confirmation.get("samples") or []
        if isinstance(sample, dict)
        and isinstance(sample.get("inputs"), dict)
        and number(sample.get("ts")) is not None
    ]
    if samples:
        return sorted(samples, key=lambda sample: sample["ts"])
    aircraft = evidence.get("aircraft") or {}
    observed = number(evidence.get("observed_at"))
    if observed is None:
        return []
    return [
        {
            "ts": observed,
            "inputs": {field: number(aircraft.get(field)) for field in ("gs", "track", "alt_baro", "tas")},
            "quality_flags": [],
        }
    ]


def _reported_tas(sample):
    tas = number((sample.get("inputs") or {}).get("tas"))
    return tas if tas is not None and REPORTED_TAS_KT[0] <= tas <= REPORTED_TAS_KT[1] else None


def correct_sample(entry, sample, wind):
    """One report's airspeed and verdict: from its reported true airspeed, else
    from ground speed, track, and the wind. Returns the corrected sample, whose
    airspeed_basis says which ("reported" or "modelled_wind"), or None without one."""
    inputs = sample.get("inputs") or {}
    gs, track, altitude = (number(inputs.get(key)) for key in ("gs", "track", "alt_baro"))
    result = {
        "ts": sample["ts"],
        "gs": gs,
        "track": track,
        "alt_baro": altitude,
        "wind": wind,
        "airspeed_kt": None,
        "airspeed_basis": None,
        "verdict": "unknown",
    }
    tas, basis = _reported_tas(sample), "reported"
    if tas is None and wind is not None:
        estimate = limits.tas_from_ground_speed(gs, track, wind.get("speed_kt"), wind.get("direction_deg"))
        tas, basis = (estimate[0], "modelled_wind") if estimate is not None else (None, None)
    if tas is None or altitude is None:
        return result
    result.update(
        airspeed_kt=round(tas, 1),
        airspeed_basis=basis,
        verdict=limits.speed_verdict(entry, tas, altitude) or "unknown",
    )
    return result


def resolve_rated_speed(entry, block, corrected):
    """Settle an above_rated_speed verdict over the corrected window.

    "exceeded" needs a run of at least rules.MIN_REPORTS consecutive corrected
    "exceeded" reports spanning rules.MIN_REPORT_SECONDS (limits.exceeded_run): a
    report within the limit between them breaks the run. Any "implausible" report
    makes the window implausible (bad data); a report without an airspeed makes
    the calculation incomplete, "unknown", which run_once never saves; otherwise it
    is "within". The block keeps every corrected report, which
    limits.speed_comparison summarizes. `entry` is the type's limits, which
    correct_sample already applied.
    """
    verdicts = [sample["verdict"] for sample in corrected]
    if not corrected or "unknown" in verdicts:
        verdict = "unknown"
    elif "implausible" in verdicts:
        verdict = "implausible"
    elif limits.exceeded_run(corrected, MIN_REPORTS, MIN_REPORT_SECONDS):
        verdict = "exceeded"
    else:
        verdict = "within"
    return {**block, "verdict": verdict, "samples": corrected}


def _wind_key(sample, cell, now):
    """(UTC day, pressure level) of the wind a report needs, or None: the source is off or
    has no grid cell, the report states its own usable true airspeed or no altitude, or its
    time is outside the service's window."""
    altitude = number((sample.get("inputs") or {}).get("alt_baro"))
    if (
        cell is None
        or _reported_tas(sample) is not None
        or altitude is None
        or not askable(sample["ts"], now)
    ):
        return None
    return utc_day(sample["ts"]), level_for(altitude)


def _sample_wind(found, key, stamp):
    """The wind a report takes from its day's payload, naming its source; None without one."""
    if key is None:
        return None
    payload, fetched_at = found[key[0]]
    reading = wind_from_payload(payload, key[1], stamp)
    return None if reading is None else {**reading, "source": SOURCE, "fetched_at": fetched_at}


def _needs_wind(samples):
    return any(_reported_tas(sample) is None for sample in samples)
