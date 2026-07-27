"""Songkick -- broad concert coverage, good for the bigger touring bookings.

Needs a free API key from songkick.com/developer, supplied as
SONGKICK_API_KEY. The source skips itself cleanly when the key is absent.

Songkick lists every genre, so no fallback genre is applied here: a listing
only lands if it actually reads as house/techno. That is deliberate -- it
keeps rock and comedy shows at the same venues off the site.
"""

from __future__ import annotations

import os
import time

from ..normalize import make_event
from . import qs, request_json

BASE = "https://api.songkick.com/api/3.0"
NAME = "songkick"
LABEL = "Songkick"

SKIP_REASON = "no API key configured"

PAGE_SIZE = 50


def available() -> bool:
    return bool(os.environ.get("SONGKICK_API_KEY"))


def _results(payload, key: str) -> list:
    page = (payload or {}).get("resultsPage") or {}
    results = page.get("results") or {}
    value = results.get(key)
    if isinstance(value, list):
        return value
    return [value] if isinstance(value, dict) else []


def metro_area_id(api_key: str) -> int:
    """Resolve Vancouver's metro area rather than hardcoding a guessed id."""
    override = os.environ.get("SONGKICK_METRO_ID", "").strip()
    if override.isdigit():
        return int(override)

    payload = request_json(qs(f"{BASE}/search/locations.json", {
        "query": "Vancouver", "apikey": api_key,
    }))
    for location in _results(payload, "location"):
        metro = (location or {}).get("metroArea") or {}
        country = (metro.get("country") or {}).get("displayName") or ""
        state = (metro.get("state") or {}).get("displayName") or ""
        # Guard against Vancouver, Washington -- same name, wrong country.
        if country.lower() == "canada" and state.upper() in ("BC", "BRITISH COLUMBIA", ""):
            if metro.get("id"):
                return int(metro["id"])
    raise RuntimeError("could not resolve a Canadian Vancouver metro area")


def fetch(days_ahead: int = 120) -> list[dict]:
    api_key = os.environ.get("SONGKICK_API_KEY")
    if not api_key:
        raise RuntimeError("SONGKICK_API_KEY not set")

    metro = metro_area_id(api_key)

    events: list[dict] = []
    page = 1
    while page <= 10:
        payload = request_json(qs(f"{BASE}/metro_areas/{metro}/calendar.json", {
            "apikey": api_key, "per_page": PAGE_SIZE, "page": page,
        }))
        batch = _results(payload, "event")
        if not batch:
            break

        for raw in batch:
            start = raw.get("start") or {}
            # datetime carries the clock time; date alone means time unannounced.
            when = start.get("datetime") or start.get("date")
            if not when:
                continue

            venue = raw.get("venue") or {}
            artists = [
                ((p or {}).get("artist") or {}).get("displayName")
                for p in (raw.get("performance") or [])
            ]

            event = make_event(
                source=NAME,
                source_id=str(raw.get("id") or ""),
                title=raw.get("displayName"),
                start=when,
                venue=venue.get("displayName") or "",
                url=raw.get("uri") or "",
                ticket_url=raw.get("uri") or "",
                artists=artists,
                description=raw.get("displayName") or "",
                # No fallback: Songkick is all-genre, so only listings that
                # genuinely read as house/techno should reach the site.
            )
            if event:
                events.append(event)

        total = ((payload or {}).get("resultsPage") or {}).get("totalEntries") or 0
        if page * PAGE_SIZE >= total:
            break
        page += 1
        time.sleep(0.3)

    return [e for e in events if e["venue"]]
