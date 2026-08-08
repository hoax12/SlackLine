"""Open-Meteo weather context for Scout. Keyless, free, no signup.

Weather is a context string only (plan section 7): it cannot affect
feasibility because no rain rule exists. One string passed to the Selector
and Narrator prompts, not a modeled input. Build last, drop first.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Optional

from slackline.core.state import LatLng

API_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes, coarse buckets are plenty for a prompt.
_WMO = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "drizzle",
    55: "dense drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    80: "rain showers",
    81: "rain showers",
    82: "violent rain showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


def fetch_summary(
    origin: LatLng, service_date: str, timeout_s: float = 5.0
) -> Optional[str]:
    """One human-readable line for the service date, or None.

    None on any failure — including dates beyond the ~16-day forecast
    horizon — and the caller records a notice; never raises to the pipeline.
    """
    params = urllib.parse.urlencode(
        {
            "latitude": f"{origin.lat:.4f}",
            "longitude": f"{origin.lon:.4f}",
            "daily": "weathercode,temperature_2m_max,temperature_2m_min,"
                     "precipitation_probability_max",
            "timezone": "America/Los_Angeles",
            "start_date": service_date,
            "end_date": service_date,
        }
    )
    req = urllib.request.Request(
        f"{API_URL}?{params}", headers={"User-Agent": "slackline/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.load(resp)
        daily = payload["daily"]
        code = int(daily["weathercode"][0])
        t_max = daily["temperature_2m_max"][0]
        t_min = daily["temperature_2m_min"][0]
        rain = daily["precipitation_probability_max"][0]
        desc = _WMO.get(code, "mixed conditions")
        line = f"{desc}, high {t_max:.0f}C / low {t_min:.0f}C"
        if rain is not None:
            line += f", {rain:.0f}% chance of precipitation"
        return line
    except Exception:
        return None
