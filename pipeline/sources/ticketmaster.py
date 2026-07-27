"""Ticketmaster Discovery API -- the larger rooms and touring headliners.

Needs a free API key from developer.ticketmaster.com, supplied as the
TICKETMASTER_API_KEY environment variable. The source skips itself cleanly
when the key is absent, so the pipeline still runs without one.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from ..normalize import make_event
from . import qs, request_json

ENDPOINT = "https://app.ticketmaster.com/discovery/v2/events.json"
NAME = "ticketmaster"
LABEL = "Ticketmaster"

PAGE_SIZE = 100

SKIP_REASON = "no API key configured"


def available() -> bool:
    return bool(os.environ.get("TICKETMASTER_API_KEY"))


def fetch(days_ahead: int = 120) -> list[dict]:
    api_key = os.environ.get("TICKETMASTER_API_KEY")
    if not api_key:
        raise RuntimeError("TICKETMASTER_API_KEY not set")

    now = datetime.now(timezone.utc)
    params = {
        "apikey": api_key,
        "city": "Vancouver",
        "countryCode": "CA",
        "classificationName": "Dance/Electronic",
        "startDateTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDateTime": (now + timedelta(days=days_ahead)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "size": PAGE_SIZE,
        "sort": "date,asc",
    }

    events: list[dict] = []
    page = 0
    while page < 10:
        data = request_json(qs(ENDPOINT, {**params, "page": page}))
        batch = ((data.get("_embedded") or {}).get("events")) or []
        if not batch:
            break

        for raw in batch:
            dates = (raw.get("dates") or {}).get("start") or {}
            # dateTime is UTC; localDate/localTime are Vancouver wall-clock.
            if dates.get("dateTime"):
                start = dates["dateTime"]
            elif dates.get("localDate"):
                start = f"{dates['localDate']}T{dates.get('localTime') or '22:00:00'}"
            else:
                continue

            venues = (raw.get("_embedded") or {}).get("venues") or []
            venue = venues[0] if venues else {}

            genres = []
            for cls in raw.get("classifications") or []:
                for key in ("genre", "subGenre"):
                    name = (cls.get(key) or {}).get("name") or ""
                    # Ticketmaster uses "Undefined" as a real value.
                    if name and name.lower() not in ("undefined", "other"):
                        genres.append(name)

            images = sorted(
                (i for i in raw.get("images") or [] if i.get("url")),
                key=lambda i: i.get("width") or 0,
                reverse=True,
            )
            prices = raw.get("priceRanges") or []
            attractions = (raw.get("_embedded") or {}).get("attractions") or []

            event = make_event(
                source=NAME,
                source_id=str(raw.get("id") or ""),
                title=raw.get("name"),
                start=start,
                end=((raw.get("dates") or {}).get("end") or {}).get("dateTime"),
                venue=venue.get("name") or "",
                address=(venue.get("address") or {}).get("line1") or "",
                url=raw.get("url") or "",
                ticket_url=raw.get("url") or "",
                artists=[a.get("name") for a in attractions],
                genres=genres,
                image=images[0]["url"] if images else "",
                price_min=prices[0].get("min") if prices else None,
                price_max=prices[0].get("max") if prices else None,
                currency=(prices[0].get("currency") if prices else "CAD") or "CAD",
                description=(raw.get("info") or "") + " " + (raw.get("pleaseNote") or ""),
                # The query is already scoped to Dance/Electronic.
                fallback_genres=["electronic"],
            )
            if event:
                events.append(event)

        pagination = data.get("page") or {}
        if page >= (pagination.get("totalPages") or 1) - 1:
            break
        page += 1
        time.sleep(0.3)

    # A listing with no venue is unusable on a "where do I go tonight" site.
    return [e for e in events if e["venue"]]
