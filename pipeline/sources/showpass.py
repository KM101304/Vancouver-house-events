"""Showpass -- the ticketing platform most independent Vancouver promoters use.

DISABLED BY DEFAULT. Showpass has no published public listings endpoint, and
the paths tried below returned nothing usable in production, so leaving it on
only decorates the footer with a permanent failure. The adapter is kept because
the platform genuinely matters for this city: set ENABLE_SHOWPASS=1 to run it,
and fix ENDPOINTS below once the real path is known.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

from ..normalize import VANCOUVER, make_event
from . import qs, request_json

NAME = "showpass"
LABEL = "Showpass"

# Tried in order; the first one that yields parseable events wins.
ENDPOINTS = [
    "https://www.showpass.com/api/public/discover/",
    "https://api.showpass.com/api/public/discovery/",
]

PAGE_SIZE = 100

SKIP_REASON = "disabled — set ENABLE_SHOWPASS=1"


def available() -> bool:
    return os.environ.get("ENABLE_SHOWPASS", "").strip() not in ("", "0", "false")


def _extract(payload) -> list[dict]:
    """Showpass has shipped both a bare list and a DRF-style envelope."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "events", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _venue_of(raw: dict) -> tuple[str, str]:
    venue = raw.get("venue")
    if isinstance(venue, dict):
        return venue.get("name") or "", venue.get("address") or ""
    location = raw.get("location")
    if isinstance(location, dict):
        return location.get("name") or "", location.get("address") or ""
    return (venue or raw.get("venue_name") or ""), (raw.get("address") or "")


def fetch(days_ahead: int = 120) -> list[dict]:
    today = datetime.now(VANCOUVER).date()
    params = {
        "location": "Vancouver",
        "page_size": PAGE_SIZE,
        "starts_on__gte": today.isoformat(),
        "starts_on__lte": (today + timedelta(days=days_ahead)).isoformat(),
        "ordering": "starts_on",
    }

    raw_events: list[dict] = []
    last_error: Exception | None = None
    for endpoint in ENDPOINTS:
        try:
            page = 1
            while page <= 8:
                payload = request_json(qs(endpoint, {**params, "page": page}), retries=2)
                batch = _extract(payload)
                if not batch:
                    break
                raw_events.extend(batch)
                page += 1
                time.sleep(0.4)
            if raw_events:
                break
        except Exception as exc:  # noqa: BLE001 - try the next known path shape
            last_error = exc
            continue

    if not raw_events:
        raise RuntimeError(f"no parseable response from any Showpass endpoint: {last_error}")

    events: list[dict] = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        venue_name, address = _venue_of(raw)
        slug = raw.get("slug") or ""
        url = raw.get("url") or (f"https://www.showpass.com/{slug}/" if slug else "")

        event = make_event(
            source=NAME,
            source_id=str(raw.get("id") or raw.get("uuid") or ""),
            title=raw.get("name") or raw.get("title"),
            start=raw.get("starts_on") or raw.get("start_date"),
            end=raw.get("ends_on") or raw.get("end_date"),
            venue=venue_name,
            address=address,
            url=url,
            ticket_url=url,
            genres=[t.get("name") if isinstance(t, dict) else t for t in (raw.get("tags") or [])],
            image=raw.get("image") or raw.get("cover_image") or "",
            description=raw.get("description") or raw.get("short_description") or "",
            # Showpass carries every kind of event, so no fallback genre here:
            # only listings that actually read as house/techno are kept.
        )
        if event:
            events.append(event)

    return events
